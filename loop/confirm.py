"""确认协议 + 搜索裁决。见 IMPLEMENTATION-P2.md §4.6。

I11（本期最重要的不变式）：**命中 dev 阈值不得终止搜索。**
    SearchVerdict 里根本没有 DONE / SUCCESS —— 命中阈值只会得到 CONFIRM_PENDING，
    然后进入确认协议。搜索本身永远只会 CONTINUE 或 BUDGET_EXHAUSTED。

确认协议状态机：
    SEARCHING → (dev 命中阈值) → CONFIRM_SEEDS
      CONFIRM_SEEDS：换 n 个新 seed 重跑，用 **CI 下界** 对比阈值。
                     不过 → 标 fluke、写 deadend 台账、回到 SEARCHING（继续搜）。
                     过   → CONFIRM_TRANSFER
      CONFIRM_TRANSFER：跨被试/跨会话稳健性复核。不过 → 回 SEARCHING。过 → GATE_PRE_TEST
      GATE_PRE_TEST：**人工闸门**。这是通往 TEST_ONCE 的唯一门。
                     approve → TEST_ONCE（测试集，一次性 token）；reject → REJECTED
      TEST_ONCE → DONE

测试集只能碰一次，且**只有**经过人工 approve 才可能进入 TEST_ONCE。
见 tests/test_confirm.py::test_no_code_path_reaches_test_once_without_human_approval
（对本文件做 AST 分析，确认所有 TEST_ONCE 赋值都在检查 approve 的分支里）。
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import List, Optional


class SearchVerdict(str, Enum):
    """搜索裁决。**刻意不存在 DONE / SUCCESS**（I11）。"""

    CONTINUE = "continue"
    CONFIRM_PENDING = "confirm_pending"
    BUDGET_EXHAUSTED = "budget_exhausted"


class ConfirmState(str, Enum):
    SEARCHING = "searching"
    CONFIRM_SEEDS = "confirm_seeds"
    CONFIRM_TRANSFER = "confirm_transfer"
    GATE_PRE_TEST = "gate_pre_test"
    TEST_ONCE = "test_once"
    DONE = "done"
    REJECTED = "rejected"
    BUDGET_EXHAUSTED = "budget_exhausted"


def crosses_threshold(score: Optional[float], threshold: float, direction: str) -> bool:
    if score is None:
        return False
    if direction == "maximize":
        return score >= threshold
    if direction == "minimize":
        return score <= threshold
    raise ValueError(f"未知 direction：{direction}")


def judge_search(dev_score: Optional[float], threshold: float, direction: str,
                 budget_exhausted: bool = False) -> SearchVerdict:
    """搜索裁决。命中阈值 → CONFIRM_PENDING（**不是** DONE）。"""
    if budget_exhausted:
        return SearchVerdict.BUDGET_EXHAUSTED
    if crosses_threshold(dev_score, threshold, direction):
        return SearchVerdict.CONFIRM_PENDING
    return SearchVerdict.CONTINUE


def confirm_seeds(run_ids: List[str], metric: str, threshold: float, direction: str,
                  results_path: Path) -> bool:
    """确认阶段判定：用新 seed 的聚合 **CI 下界/上界** 对比阈值。"""
    from core import results

    a = results.agg(run_ids, metric, db=results_path)
    return a.confirms_threshold(threshold, direction)


class ConfirmProtocol:
    """把确认协议编码为显式状态机，禁止任何绕过人工闸门直达测试集的路径。"""

    def __init__(self, contract, results_path: Path, tree_db: Optional[Path] = None,
                 ledger_db: Optional[Path] = None):
        self.contract = contract
        self.results_path = Path(results_path)
        self.tree_db = Path(tree_db) if tree_db else None
        self.ledger_db = Path(ledger_db) if ledger_db else None
        self.state = ConfirmState.SEARCHING

    # -- 阶段 1：换新 seed 复核 --------------------------------------------
    def confirm_seeds_stage(self, node_id: str, seed_run_ids: List[str]) -> bool:
        """新 seed 复核。不过 → fluke + deadend 台账 + 回 SEARCHING。过 → CONFIRM_TRANSFER。"""
        self.state = ConfirmState.CONFIRM_SEEDS
        passed = confirm_seeds(seed_run_ids, self.contract.primary_metric,
                               self.contract.success_threshold,
                               self.contract.direction, self.results_path)
        if not passed:
            self._mark_fluke(node_id, seed_run_ids)
            self.state = ConfirmState.SEARCHING
            return False
        self.state = ConfirmState.CONFIRM_TRANSFER
        return True

    # -- 阶段 2：跨被试/会话稳健性 -----------------------------------------
    def confirm_transfer_stage(self, node_id: str, transfer_run_ids: List[str]) -> bool:
        assert self.state == ConfirmState.CONFIRM_TRANSFER, "必须先通过 seed 复核"
        passed = confirm_seeds(transfer_run_ids, self.contract.primary_metric,
                               self.contract.success_threshold,
                               self.contract.direction, self.results_path)
        if not passed:
            self._mark_fluke(node_id, transfer_run_ids)
            self.state = ConfirmState.SEARCHING
            return False
        self.state = ConfirmState.GATE_PRE_TEST
        return True

    # -- 阶段 3：人工闸门 → 测试集（唯一入口）------------------------------
    def request_test(self, gate_approved: bool) -> ConfirmState:
        """通往 TEST_ONCE 的**唯一**方法。只有 gate_approved 才进入 TEST_ONCE。"""
        assert self.state == ConfirmState.GATE_PRE_TEST, "只能在人工闸门处请求测试"
        if gate_approved:
            # ↓↓↓ 全代码库中唯一给状态赋 TEST_ONCE 的地方，且被 approve 分支包住 ↓↓↓
            self.state = ConfirmState.TEST_ONCE
        else:
            self.state = ConfirmState.REJECTED
        return self.state

    def finish_test(self) -> ConfirmState:
        assert self.state == ConfirmState.TEST_ONCE, "只有测试后才能结束"
        self.state = ConfirmState.DONE
        return self.state

    # -- 内部：标 fluke + 写台账 -------------------------------------------
    def _mark_fluke(self, node_id: str, evidence_run_ids: List[str]) -> None:
        if self.tree_db is not None:
            from loop import tree

            tree.set_status(self.tree_db, node_id, "fluke")
        if self.ledger_db is not None:
            from loop import ledger

            ledger.add_lesson(
                self.ledger_db, self.contract.contract_id,
                text=(f"节点 {node_id} dev 命中阈值但换 seed 后 CI 下界未过线 → "
                      f"判定为运气（fluke），非真实提升。"),
                kind="deadend", evidence=list(evidence_run_ids))
