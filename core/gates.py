"""人工卡点。见 IMPLEMENTATION.md §5.7。

CLI：
    python -m core.gates list                 待处理卡点
    python -m core.gates review <gate_id>     逐字段 approve/reject/edit
    python -m core.gates history              审计轨迹

交互形式：逐字段展示，每个字段 [a]pprove / [r]eject / [e]dit；
reject 和 edit 必须填一行理由。**不接受"整体批准"**。

本期两个卡点类型：
  - contract_review   契约冻结前
  - novelty_verdict   只问"这个想法是否已经有人做过？"，展示 novelty_evidence top-5

决策写 audit 表。字段：gate_id, gate_type, subject_id, field, decision, reason, decided_at。
"""
from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

_SCHEMA = Path(__file__).resolve().parent.parent / "schema" / "audit.sql"
_DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "audit.sqlite"

# 逐字段过契约时要人工确认的字段
CONTRACT_REVIEW_FIELDS = [
    "question", "hypothesis", "datasets", "split_protocol", "paradigm",
    "baselines", "primary_metric", "success_threshold", "direction",
    "stat_plan", "budget", "kill_criteria", "preregistered_ablations",
    "novelty_evidence", "novelty_note",
]

# 第四期选题卡点：逐字段过 ContractDraft（见 IMPLEMENTATION-P4.md §4.8）
TOPIC_SELECTION_FIELDS = [
    "question", "hypothesis", "datasets", "split_protocol", "paradigm",
    "baselines", "primary_metric", "success_threshold", "direction",
    "n_seeds", "kill_criteria", "preregistered_ablations", "novelty_evidence",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db(path: Path = _DEFAULT_DB) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(_SCHEMA.read_text(encoding="utf-8"))
    con.commit()
    con.close()
    return path


def _connect(db: Optional[Path]) -> sqlite3.Connection:
    con = sqlite3.connect(db or _DEFAULT_DB)
    con.row_factory = sqlite3.Row
    return con


# ---------------------------------------------------------------------------
# 决策记录
# ---------------------------------------------------------------------------
def record_decision(gate_id: str, gate_type: str, subject_id: str, field: str,
                    decision: str, reason: Optional[str] = None,
                    db: Optional[Path] = None) -> None:
    """写一条逐字段决策。reject / edit 必须带非空理由（应用层强制）。"""
    if decision not in ("approve", "reject", "edit"):
        raise ValueError(f"非法决策：{decision}")
    if decision in ("reject", "edit") and not (reason and reason.strip()):
        raise ValueError(f"{decision} 必须填写一行理由")
    con = _connect(db)
    try:
        con.execute(
            "INSERT INTO audit (gate_id, gate_type, subject_id, field, decision, reason, decided_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (gate_id, gate_type, subject_id, field, decision, reason, _now()),
        )
        con.commit()
    finally:
        con.close()


def enqueue_gate(gate_type: str, subject_id: str, payload: dict,
                 db: Optional[Path] = None) -> str:
    gate_id = f"{gate_type}_{uuid.uuid4().hex[:8]}"
    con = _connect(db)
    try:
        con.execute(
            "INSERT INTO gate_queue (gate_id, gate_type, subject_id, payload, status, created_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?)",
            (gate_id, gate_type, subject_id, json.dumps(payload, ensure_ascii=False), _now()),
        )
        con.commit()
    finally:
        con.close()
    return gate_id


def resolve_gate(gate_id: str, approved: bool, db: Optional[Path] = None) -> None:
    con = _connect(db)
    try:
        con.execute(
            "UPDATE gate_queue SET status=?, resolved_at=? WHERE gate_id=?",
            ("approved" if approved else "rejected", _now(), gate_id),
        )
        con.commit()
    finally:
        con.close()


def list_pending(db: Optional[Path] = None) -> List[sqlite3.Row]:
    con = _connect(db)
    try:
        return con.execute(
            "SELECT * FROM gate_queue WHERE status='pending' ORDER BY created_at"
        ).fetchall()
    finally:
        con.close()


def get_history(subject_id: Optional[str] = None,
                db: Optional[Path] = None) -> List[sqlite3.Row]:
    con = _connect(db)
    try:
        if subject_id:
            return con.execute(
                "SELECT * FROM audit WHERE subject_id=? ORDER BY decided_at", (subject_id,)
            ).fetchall()
        return con.execute("SELECT * FROM audit ORDER BY decided_at").fetchall()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 编排：逐字段过一个契约（可编程调用，供测试与端到端）
# ---------------------------------------------------------------------------
def review_contract(contract, decisions: Optional[Dict[str, dict]] = None,
                    db: Optional[Path] = None, interactive: bool = False) -> bool:
    """逐字段过契约。返回是否全部 approve。

    decisions: {field: {"decision": "approve|reject|edit", "reason": str|None}}
               用于非交互（测试 / 自动）场景。interactive=True 时走 stdin。
    没有整体批准的路径——必须逐字段。
    """
    gate_type = "contract_review"
    subject_id = f"{contract.contract_id}.v{contract.version}"
    gate_id = enqueue_gate(gate_type, subject_id,
                           {"fields": CONTRACT_REVIEW_FIELDS}, db=db)
    all_approved = True
    data = contract.model_dump()
    for field in CONTRACT_REVIEW_FIELDS:
        value = data.get(field)
        if interactive:
            decision, reason = _prompt_field(field, value)
        else:
            d = (decisions or {}).get(field, {"decision": "approve", "reason": None})
            decision, reason = d["decision"], d.get("reason")
        record_decision(gate_id, gate_type, subject_id, field, decision, reason, db=db)
        if decision != "approve":
            all_approved = False
    resolve_gate(gate_id, all_approved, db=db)
    return all_approved


def novelty_verdict(contract, top5_titles: List[str], decision: str,
                    reason: Optional[str] = None, db: Optional[Path] = None) -> bool:
    """独立一步：只问"这个想法是否已经有人做过？"。展示 novelty_evidence top-5。"""
    gate_type = "novelty_verdict"
    subject_id = f"{contract.contract_id}.v{contract.version}"
    gate_id = enqueue_gate(gate_type, subject_id, {"top5": top5_titles}, db=db)
    record_decision(gate_id, gate_type, subject_id, "is_novel", decision, reason, db=db)
    approved = decision == "approve"
    resolve_gate(gate_id, approved, db=db)
    return approved


def topic_selection(draft, axis_rows: Optional[List[dict]] = None,
                    novelty_top5: Optional[List[str]] = None,
                    redteam: Optional[List[dict]] = None,
                    cost: Optional[dict] = None,
                    decisions: Optional[Dict[str, dict]] = None,
                    db: Optional[Path] = None, interactive: bool = False) -> bool:
    """第四期选题卡点（IMPLEMENTATION-P4.md §4.8）：逐字段过 ContractDraft。

    只对 status=complete 的 draft 有意义（incomplete 的按 I22 根本不进排名）。
    payload 附带：五轴分数表、novelty evidence 的 top-5 标题、三份 red team 报告、成本预估。
    没有"整体批准"路径 —— 必须逐字段。返回是否全部 approve。
    """
    if getattr(draft, "status", "complete") != "complete":
        raise ValueError(
            f"draft 状态为 {getattr(draft, 'status', None)!r}（缺 "
            f"{getattr(draft, 'missing_fields', None)}），incomplete 不进选题卡点（I22）")
    gate_type = "topic_selection"
    subject_id = getattr(draft, "contract_id", "draft")
    payload = {
        "axis_scores": axis_rows or [],       # 五列表，无总分
        "novelty_top5": novelty_top5 or [],
        "redteam": redteam or [],
        "cost": cost or {},
        "fields": TOPIC_SELECTION_FIELDS,
    }
    gate_id = enqueue_gate(gate_type, subject_id, payload, db=db)
    all_approved = True
    for field in TOPIC_SELECTION_FIELDS:
        value = getattr(draft, field, None)
        if interactive:
            decision, reason = _prompt_field(field, value)  # pragma: no cover
        else:
            d = (decisions or {}).get(field, {"decision": "approve", "reason": None})
            decision, reason = d["decision"], d.get("reason")
        record_decision(gate_id, gate_type, subject_id, field, decision, reason, db=db)
        if decision != "approve":
            all_approved = False
    resolve_gate(gate_id, all_approved, db=db)
    return all_approved


def sign_unverifiable_citations(keys: List[str], decision: str,
                                reason: Optional[str] = None,
                                subject_id: str = "paper",
                                db: Optional[Path] = None) -> bool:
    """C15/I21：unverifiable 引用清单的人工签字。

    不静默放行，也不自动删句子——列出来，人决定。
    decision=approve 表示"已知晓并放行这些 unverifiable 引用"。
    """
    if not keys:
        return True
    gate_type = "citation_unverifiable"
    gate_id = enqueue_gate(gate_type, subject_id, {"keys": list(keys)}, db=db)
    record_decision(gate_id, gate_type, subject_id, "unverifiable_list",
                    decision, reason, db=db)
    approved = decision == "approve"
    resolve_gate(gate_id, approved, db=db)
    return approved


def _prompt_field(field: str, value):  # pragma: no cover - 交互路径
    print(f"\n=== 字段: {field} ===")
    print(json.dumps(value, ensure_ascii=False, indent=2))
    while True:
        choice = input("[a]pprove / [r]eject / [e]dit: ").strip().lower()
        if choice in ("a", "approve"):
            return "approve", None
        if choice in ("r", "reject"):
            reason = input("拒绝理由（必填一行）: ").strip()
            if reason:
                return "reject", reason
        elif choice in ("e", "edit"):
            reason = input("修改说明（必填一行）: ").strip()
            if reason:
                return "edit", reason
        print("无效输入，或理由为空，请重试。")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _main(argv: List[str]) -> int:  # pragma: no cover - CLI
    init_db()
    if not argv:
        print(__doc__)
        return 0
    cmd = argv[0]
    if cmd == "list":
        rows = list_pending()
        if not rows:
            print("没有待处理卡点。")
        for r in rows:
            print(f"{r['gate_id']}  {r['gate_type']}  {r['subject_id']}  ({r['created_at']})")
        return 0
    if cmd == "history":
        subject = argv[1] if len(argv) > 1 else None
        for r in get_history(subject):
            print(f"{r['decided_at']}  {r['gate_type']}  {r['subject_id']}  "
                  f"{r['field']}={r['decision']}  {r['reason'] or ''}")
        return 0
    if cmd == "review":
        if len(argv) < 2:
            print("用法: python -m core.gates review <gate_id|contract_json_path>")
            return 2
        target = argv[1]
        path = Path(target)
        if path.exists():
            from core.contract import Contract

            c = Contract.load(path)
            ok = review_contract(c, interactive=True)
            print("\n结果：", "全部通过 ✅" if ok else "存在未通过字段 ❌")
            return 0 if ok else 1
        print(f"未找到契约文件：{target}（本期 review 逐字段过契约 JSON）")
        return 2
    print(f"未知命令：{cmd}")
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main(sys.argv[1:]))
