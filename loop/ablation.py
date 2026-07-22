"""消融执行。见 IMPLEMENTATION-P3.md §4.3。

- plan_ablations 只从 contract.preregistered_ablations 生成 —— 不推断、不补充。
- run_ablation 走和主实验完全相同的 harness / results 路径，每个 run 带 ablation_id，
  需要 CONFIRM_SEEDS（n_seeds 个种子），但不需要 CONFIRM_TRANSFER。
- I20：ablation_id 必须预注册（契约）或经 ablation_extension 卡点批准。
- 新增卡点 ablation_extension（见 request_ablation_extension）：表单必须填"它可能证伪什么"，
  为空则拒绝。刻意麻烦，否则加消融会退化成"补数据支持已有结论"。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional, Sequence


@dataclass(frozen=True)
class AblationPlan:
    id: str
    description: str
    falsifies: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def plan_ablations(contract) -> List[AblationPlan]:
    """只从 contract.preregistered_ablations 生成。"""
    return [AblationPlan(a.id, a.description, a.falsifies)
            for a in contract.preregistered_ablations]


# ---------------------------------------------------------------------------
# ablation_id 合法性（I20）
# ---------------------------------------------------------------------------
def approved_ablation_ids(audit_db: Optional[Path]) -> set:
    """audit 里经 ablation_extension 批准的 ablation_id 集合。"""
    if audit_db is None or not Path(audit_db).exists():
        return set()
    con = sqlite3.connect(audit_db)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT subject_id, field, decision FROM audit "
            "WHERE gate_type='ablation_extension'").fetchall()
    except sqlite3.OperationalError:
        return set()
    finally:
        con.close()
    out = set()
    for r in rows:
        if r["decision"] == "approve":
            out.add(r["subject_id"])
            if r["field"]:
                out.add(r["field"])
    return out


def is_ablation_allowed(ablation_id: str, contract, audit_db: Optional[Path]) -> bool:
    prereg = {a.id for a in contract.preregistered_ablations}
    return ablation_id in prereg or ablation_id in approved_ablation_ids(audit_db)


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------
def _tag_ablation_run(results_path: Path, run_id: str, ablation_id: str,
                      contract_id: str) -> None:
    con = sqlite3.connect(results_path)
    try:
        con.execute(
            "INSERT INTO ablation_runs (run_id, ablation_id, contract_id, created_at) "
            "VALUES (?, ?, ?, ?)", (run_id, ablation_id, contract_id, _now()))
        con.commit()
    finally:
        con.close()


def run_ablation(plan: AblationPlan, contract, results_path: Path,
                 evaluator_fn: Callable, n_seeds: Optional[int] = None,
                 audit_db: Optional[Path] = None) -> List[str]:
    """跑一个预注册/已批准的消融，n_seeds 个种子；每个 run 打上 ablation_id。

    evaluator_fn(plan, seed) -> run_id：走 harness/results，返回 run_id（注入以便离线测）。
    I20：ablation_id 不合法 → 直接拒绝，不跑。
    """
    if not is_ablation_allowed(plan.id, contract, audit_db):
        raise ValueError(
            f"ablation_id={plan.id!r} 未预注册且未经 ablation_extension 批准（I20），拒绝执行。")
    n_seeds = n_seeds or contract.stat_plan.n_seeds
    run_ids: List[str] = []
    for i in range(n_seeds):
        run_id = evaluator_fn(plan, i)
        _tag_ablation_run(results_path, run_id, plan.id, contract.contract_id)
        run_ids.append(run_id)
    return run_ids


def ablation_run_ids(results_path: Path, ablation_id: Optional[str] = None) -> List[str]:
    con = sqlite3.connect(results_path)
    try:
        if ablation_id is None:
            rows = con.execute("SELECT run_id FROM ablation_runs").fetchall()
        else:
            rows = con.execute(
                "SELECT run_id FROM ablation_runs WHERE ablation_id=?",
                (ablation_id,)).fetchall()
        return [r[0] for r in rows]
    finally:
        con.close()


def validate_ablation_runs(results_path: Path, contract,
                           audit_db: Optional[Path] = None) -> List[str]:
    """C13：返回所有 ablation_id 不合法的 run_id（应为空）。"""
    con = sqlite3.connect(results_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT run_id, ablation_id FROM ablation_runs").fetchall()
    finally:
        con.close()
    bad = []
    for r in rows:
        if not is_ablation_allowed(r["ablation_id"], contract, audit_db):
            bad.append(r["run_id"])
    return bad


# ---------------------------------------------------------------------------
# ablation_extension 卡点
# ---------------------------------------------------------------------------
def request_ablation_extension(contract_id: str, ablation_id: str, checks: str,
                               falsifies: str, decision: str,
                               reason: Optional[str] = None,
                               audit_db: Optional[Path] = None) -> bool:
    """新增消融的卡点。表单必须填"它可能证伪什么"（falsifies），为空即拒绝。

    批准后写 audit（gate_type=ablation_extension），此后该 ablation_id 可作为
    claim 的 source.kind=approved，也允许 run_ablation 执行。
    """
    if not (falsifies and falsifies.strip()):
        raise ValueError(
            "ablation_extension 必须填写「它可能证伪什么」（falsifies），为空则拒绝。")
    if not (checks and checks.strip()):
        raise ValueError("ablation_extension 必须填写「它要检验什么」（checks）。")
    from core import gates

    gate_id = gates.enqueue_gate(
        "ablation_extension", ablation_id,
        {"ablation_id": ablation_id, "checks": checks, "falsifies": falsifies},
        db=audit_db)
    gates.record_decision(gate_id, "ablation_extension", ablation_id, ablation_id,
                          decision, reason, db=audit_db)
    approved = decision == "approve"
    gates.resolve_gate(gate_id, approved, db=audit_db)
    return approved
