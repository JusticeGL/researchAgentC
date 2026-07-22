"""经验台账 + compact。见 IMPLEMENTATION-P2.md §4.4。

- active = superseded_by IS NULL，上限默认 40 条
- 超限触发 compact()：输入全部 active，输出合并后 <= max 条
- 被合并的旧条目不删除，写 superseded_by
- I15：compact() 后必须断言新 active 集合的 evidence run_id 并集 ⊇ 旧集合的并集，
       丢证据即失败（LedgerCompactionError）

compact 的"一次 LLM 调用"用依赖注入的 compactor(list[Lesson]) -> list[dict] 表达，
默认 compactor 是确定性的按 kind 合并（不需要 LLM，也便于测试）；
接入真实 LLM 时替换 compactor 即可，I15 校验对任何 compactor 都强制执行。
"""
from __future__ import annotations

import json
import sqlite3
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional

_SCHEMA = Path(__file__).resolve().parent.parent / "schema" / "ledger.sql"

VALID_KINDS = {"deadend", "constraint", "insight", "bug_pattern"}

Lesson = namedtuple("Lesson", ["id", "contract_id", "text", "kind", "evidence",
                               "created_at", "superseded_by"])


class LedgerCompactionError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db(path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(_SCHEMA.read_text(encoding="utf-8"))
    con.commit()
    con.close()


def _connect(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    return con


def add_lesson(db: Path, contract_id: str, text: str, kind: str,
               evidence: List[str]) -> int:
    if kind not in VALID_KINDS:
        raise ValueError(f"非法 lesson kind：{kind}")
    con = _connect(db)
    try:
        cur = con.execute(
            "INSERT INTO lessons (contract_id, text, kind, evidence, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (contract_id, text, kind, json.dumps(list(evidence)), _now()),
        )
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def _row_to_lesson(row: sqlite3.Row) -> Lesson:
    return Lesson(row["id"], row["contract_id"], row["text"], row["kind"],
                  json.loads(row["evidence"]), row["created_at"], row["superseded_by"])


def active_lessons(db: Path, contract_id: Optional[str] = None) -> List[Lesson]:
    con = _connect(db)
    try:
        if contract_id is not None:
            rows = con.execute(
                "SELECT * FROM lessons WHERE superseded_by IS NULL AND contract_id=? "
                "ORDER BY id", (contract_id,)).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM lessons WHERE superseded_by IS NULL ORDER BY id").fetchall()
        return [_row_to_lesson(r) for r in rows]
    finally:
        con.close()


def _evidence_union(lessons) -> set:
    u = set()
    for l in lessons:
        u |= set(l["evidence"] if isinstance(l, dict) else l.evidence)
    return u


def _default_compactor(lessons: List[Lesson]) -> List[dict]:
    """确定性合并：按 kind 归并，evidence 取并集。不丢证据。"""
    by_kind = {}
    for l in lessons:
        d = by_kind.setdefault(l.kind, {"text": [], "kind": l.kind, "evidence": set()})
        d["text"].append(l.text)
        d["evidence"] |= set(l.evidence)
    out = []
    for kind, d in by_kind.items():
        out.append({
            "text": " | ".join(dict.fromkeys(d["text"]))[:2000],
            "kind": kind,
            "evidence": sorted(d["evidence"]),
        })
    return out


def compact(db: Path, contract_id: str, max_active: int = 40,
            compactor: Optional[Callable[[List[Lesson]], List[dict]]] = None) -> bool:
    """active 超过 max_active 时触发。返回是否执行了压缩。

    I15：新 active 的 evidence 并集必须 ⊇ 旧并集，否则抛 LedgerCompactionError。
    """
    compactor = compactor or _default_compactor
    old = active_lessons(db, contract_id)
    if len(old) <= max_active:
        return False

    new_items = compactor(old)
    old_union = _evidence_union(old)
    new_union = _evidence_union(new_items)
    if not old_union.issubset(new_union):
        lost = old_union - new_union
        raise LedgerCompactionError(
            f"compact 丢失了证据 run_id（I15）：{sorted(lost)}。拒绝提交。")
    if len(new_items) > max_active:
        raise LedgerCompactionError(
            f"compact 后仍有 {len(new_items)} 条 > 上限 {max_active}。")

    con = _connect(db)
    try:
        new_ids = []
        for item in new_items:
            if item["kind"] not in VALID_KINDS:
                raise LedgerCompactionError(f"compact 产出非法 kind：{item['kind']}")
            cur = con.execute(
                "INSERT INTO lessons (contract_id, text, kind, evidence, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (contract_id, item["text"], item["kind"],
                 json.dumps(list(item["evidence"])), _now()),
            )
            new_ids.append(cur.lastrowid)
        # 旧条目不删除，标 superseded_by（指向第一条新条目作代表）
        target = new_ids[0]
        for l in old:
            con.execute("UPDATE lessons SET superseded_by=? WHERE id=?", (target, l.id))
        con.commit()
    finally:
        con.close()
    return True
