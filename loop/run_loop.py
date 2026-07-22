"""搜索主循环。见 IMPLEMENTATION-P2.md §4、Phase 9/10。

把 P2 的各部件串起来，并在循环层强制关键不变式：
  I11 命中 dev 阈值 → 走确认协议，**绝不终止搜索**、**绝不自动碰测试集**
  I12 dedup 闸门在"执行之前"
  作弊探测 sentry 在"执行之后"（invalidate 级作废该 run 并跳过建节点）
  预算任一维度到 100% → BUDGET_EXHAUSTED 停机

proposer / evaluator_fn 通过依赖注入传入：
  - 接 AIDE 时由 adapters/aide_adapter.py 提供（需要联网 + 安装 aideml）
  - 离线测试时注入假的（见 tests/test_run_loop.py）
本循环在整个自动过程中从不 redeem 测试 token（test 只在人工 approve 后另行进行）。
"""
from __future__ import annotations

from collections import namedtuple
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from loop import confirm, context, dedup, ledger, sentry, tree
from adapters import evaluator, policy

Proposal = namedtuple("Proposal", ["change_description", "patch", "parent_node_id"])


@dataclass
class SearchReport:
    nodes_created: int = 0
    fluke_count: int = 0
    confirmed_pending: int = 0
    dedup_hard_rejects: int = 0
    dedup_skips: int = 0
    invalidated: int = 0
    halted: bool = False
    budget_exhausted: bool = False
    test_token_redeemed: bool = False
    gates_enqueued: int = 0


def load_config(path: Path) -> dict:
    import yaml

    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


class SearchLoop:
    def __init__(self, contract, results_path: Path, tree_db: Path, ledger_db: Path,
                 sentry_db: Path, cfg: dict,
                 proposer: Callable, evaluator_fn: Callable,
                 primary_pipeline: Optional[str] = None):
        self.contract = contract
        self.results_path = Path(results_path)
        self.tree_db = Path(tree_db)
        self.ledger_db = Path(ledger_db)
        self.sentry_db = Path(sentry_db)
        self.cfg = cfg
        self.proposer = proposer
        self.evaluator_fn = evaluator_fn
        self.primary_pipeline = primary_pipeline
        self._seed = 0
        self._last_hit = {}  # parent_key -> 上次命中的最近邻 node_id

    def _next_seed(self) -> int:
        self._seed += 1
        return self._seed

    def _budget_exhausted(self) -> bool:
        from core import results

        runs = results.query_runs(db=self.results_path,
                                  contract_id=self.contract.contract_id)
        gpu = sum(r.gpu_hours or 0 for r in runs)
        usd = sum(r.cost_usd or 0 for r in runs)
        wall = sum((r.wall_clock_s or 0) for r in runs) / 3600.0
        b = self.contract.budget
        return gpu >= b.gpu_hours or usd >= b.usd or wall >= b.wall_clock_h

    def _build_ctx(self, parent) -> str:
        active = ledger.active_lessons(self.ledger_db, self.contract.contract_id)
        anc = tree.ancestors(self.tree_db, parent.node_id) if parent else []
        sibs = tree.siblings(self.tree_db, parent.node_id) if parent else []
        return context.build_context(
            self.contract,
            parent_code=parent.patch if parent else "",
            active_lessons=active,
            ancestor_rows=anc,
            sibling_descriptions=[s.change_description for s in sibs],
            budget_tokens=self.cfg["context"]["budget_tokens"],
            ancestor_k=self.cfg["context"]["ancestor_rows"])

    def run(self, max_nodes: Optional[int] = None) -> SearchReport:
        max_nodes = max_nodes or self.cfg["search"]["max_nodes"]
        threshold = self.contract.success_threshold
        direction = self.contract.direction
        metric = self.contract.primary_metric
        dedup_thr = self.cfg["dedup"]["threshold"]
        n_confirm = self.cfg["confirm"]["n_seeds"]

        report = SearchReport()
        max_iter = max_nodes * 5  # 防呆：dumb proposer 可能一直撞车
        it = 0
        while report.nodes_created < max_nodes and it < max_iter:
            it += 1
            if self._budget_exhausted():
                report.budget_exhausted = True
                break

            existing = tree.query_nodes(self.tree_db, self.contract.contract_id)
            parent = policy.select_node(existing, self.cfg["search"]["ucb_c"])
            pkey = parent.node_id if parent else "__root__"

            ctx = self._build_ctx(parent)
            proposal = self.proposer(ctx, existing, parent)

            # --- I12：dedup 闸门（执行之前）---
            d = dedup.check_duplicate(proposal.change_description, existing,
                                      threshold=dedup_thr,
                                      last_hit_node_id=self._last_hit.get(pkey))
            if d.hard_reject:
                report.dedup_hard_rejects += 1
                ledger.add_lesson(
                    self.ledger_db, self.contract.contract_id,
                    text=f"方向「{proposal.change_description}」连续命中 {d.nearest_node_id}，弃。",
                    kind="deadend", evidence=[])
                self._last_hit[pkey] = None
                continue
            if d.is_duplicate:
                report.dedup_skips += 1
                self._last_hit[pkey] = d.nearest_node_id
                continue
            self._last_hit[pkey] = None

            # --- 执行（dev）---
            seed = self._next_seed()
            run_ref = self.evaluator_fn(proposal, seed, "dev")
            run_id = evaluator.pick_primary_run(run_ref, self.primary_pipeline)

            # --- 作弊探测（执行之后）---
            events = sentry.scan_patch(proposal.patch)
            if events:
                sentry.record_events(self.sentry_db, run_id, events)
                if sentry.has_halt(events):
                    report.halted = True
                    break
                from core import results

                results.invalidate(run_id, reason="sentry static check",
                                   db=self.results_path)
                report.invalidated += 1
                continue

            dev = evaluator.dev_score_from_run(run_id, metric, self.results_path)
            node_id = tree.add_node(
                self.tree_db, self.contract.contract_id, proposal.change_description,
                proposal.patch, run_ids=[run_id], status="ok",
                parent_node_id=(proposal.parent_node_id
                                or (parent.node_id if parent else None)),
                dev_score=dev, results_path=self.results_path)
            if parent:
                tree.increment_expansion(self.tree_db, parent.node_id)
            report.nodes_created += 1

            # --- I11：命中阈值 → 确认协议，绝不终止搜索/碰测试集 ---
            verdict = confirm.judge_search(dev, threshold, direction)
            if verdict == confirm.SearchVerdict.CONFIRM_PENDING:
                proto = confirm.ConfirmProtocol(
                    self.contract, self.results_path,
                    tree_db=self.tree_db, ledger_db=self.ledger_db)
                fresh = []
                for _ in range(n_confirm):
                    rr = self.evaluator_fn(proposal, self._next_seed(), "dev")
                    fresh.append(evaluator.pick_primary_run(rr, self.primary_pipeline))
                passed = proto.confirm_seeds_stage(node_id, fresh)
                if passed:
                    report.confirmed_pending += 1
                    report.gates_enqueued += 1  # 交人工闸门，搜索继续
                else:
                    report.fluke_count += 1
            elif verdict == confirm.SearchVerdict.BUDGET_EXHAUSTED:
                report.budget_exhausted = True
                break
            # CONTINUE：什么都不做，进入下一轮

        # 自动过程从不动测试 token
        report.test_token_redeemed = self._test_token_redeemed()
        return report

    def _test_token_redeemed(self) -> bool:
        import sqlite3

        con = sqlite3.connect(self.results_path)
        try:
            n = con.execute("SELECT COUNT(*) FROM holdout_access").fetchone()[0]
            return n > 0
        finally:
            con.close()
