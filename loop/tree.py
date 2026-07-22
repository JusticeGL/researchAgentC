"""实验树。见 IMPLEMENTATION-P2.md §4.2。

I10：每个树节点至少对应一条 run 记录。run_ids 是 JSON list 列，SQLite 无法对它做外键，
     因此"非空且每个 run_id 都在 runs 里"在本模块写入时用应用层校验强制 ——
     不要以为写了 REFERENCES 就有保证。

dev_score 只在搜索内部使用；它不是 Metric，也永远不能进论文（见 test_tree 的断言）。
树 append-only：禁止 DELETE（trigger），status/debug_attempts/expansion_count 允许 UPDATE。
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

_SCHEMA = Path(__file__).resolve().parent.parent / "schema" / "tree.sql"

VALID_STATUS = {"ok", "buggy", "abandoned", "fluke", "confirmed"}

Node = namedtuple(
    "Node",
    ["node_id", "contract_id", "parent_node_id", "change_description",
     "change_embedding", "patch", "run_ids", "status", "debug_attempts",
     "expansion_count", "dev_score", "created_at"],
)


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


def _validate_run_ids(run_ids: List[str], results_path: Optional[Path]) -> None:
    if not run_ids:
        raise ValueError("I10：树节点的 run_ids 不能为空（每个节点至少一条 run）")
    if results_path is not None:
        from core import results

        for rid in run_ids:
            try:
                results.get_run(rid, db=results_path)
            except KeyError:
                raise ValueError(f"I10：run_id {rid} 不在 runs 库里")


def add_node(db: Path, contract_id: str, change_description: str, patch: str,
             run_ids: List[str], status: str = "ok",
             parent_node_id: Optional[str] = None, dev_score: Optional[float] = None,
             change_embedding: Optional[bytes] = None,
             node_id: Optional[str] = None,
             results_path: Optional[Path] = None) -> str:
    if status not in VALID_STATUS:
        raise ValueError(f"非法 status：{status}")
    _validate_run_ids(run_ids, results_path)
    node_id = node_id or f"n_{uuid.uuid4().hex[:10]}"
    con = _connect(db)
    try:
        con.execute(
            "INSERT INTO tree_nodes (node_id, contract_id, parent_node_id, "
            "change_description, change_embedding, patch, run_ids, status, "
            "debug_attempts, expansion_count, dev_score, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)",
            (node_id, contract_id, parent_node_id, change_description,
             change_embedding, patch, json.dumps(list(run_ids)), status,
             dev_score, _now()),
        )
        con.commit()
    finally:
        con.close()
    return node_id


def _row_to_node(row: sqlite3.Row) -> Node:
    return Node(
        node_id=row["node_id"], contract_id=row["contract_id"],
        parent_node_id=row["parent_node_id"],
        change_description=row["change_description"],
        change_embedding=row["change_embedding"], patch=row["patch"],
        run_ids=json.loads(row["run_ids"]), status=row["status"],
        debug_attempts=row["debug_attempts"], expansion_count=row["expansion_count"],
        dev_score=row["dev_score"], created_at=row["created_at"],
    )


def get_node(db: Path, node_id: str) -> Node:
    con = _connect(db)
    try:
        row = con.execute("SELECT * FROM tree_nodes WHERE node_id=?", (node_id,)).fetchone()
        if row is None:
            raise KeyError(f"节点不存在：{node_id}")
        return _row_to_node(row)
    finally:
        con.close()


def query_nodes(db: Path, contract_id: Optional[str] = None,
                status: Optional[str] = None) -> List[Node]:
    con = _connect(db)
    try:
        clauses, params = [], []
        if contract_id is not None:
            clauses.append("contract_id=?")
            params.append(contract_id)
        if status is not None:
            clauses.append("status=?")
            params.append(status)
        sql = "SELECT * FROM tree_nodes"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at"
        return [_row_to_node(r) for r in con.execute(sql, params).fetchall()]
    finally:
        con.close()


def set_status(db: Path, node_id: str, status: str) -> None:
    if status not in VALID_STATUS:
        raise ValueError(f"非法 status：{status}")
    con = _connect(db)
    try:
        con.execute("UPDATE tree_nodes SET status=? WHERE node_id=?", (status, node_id))
        con.commit()
    finally:
        con.close()


def increment_expansion(db: Path, node_id: str) -> None:
    con = _connect(db)
    try:
        con.execute(
            "UPDATE tree_nodes SET expansion_count = expansion_count + 1 WHERE node_id=?",
            (node_id,))
        con.commit()
    finally:
        con.close()


def increment_debug(db: Path, node_id: str) -> None:
    con = _connect(db)
    try:
        con.execute(
            "UPDATE tree_nodes SET debug_attempts = debug_attempts + 1 WHERE node_id=?",
            (node_id,))
        con.commit()
    finally:
        con.close()


def ancestors(db: Path, node_id: str) -> List[Node]:
    """从根到该节点父链（不含自身），按由近到远。"""
    chain: List[Node] = []
    cur = get_node(db, node_id)
    while cur.parent_node_id:
        cur = get_node(db, cur.parent_node_id)
        chain.append(cur)
    return chain


def siblings(db: Path, node_id: str) -> List[Node]:
    node = get_node(db, node_id)
    con = _connect(db)
    try:
        if node.parent_node_id is None:
            rows = con.execute(
                "SELECT * FROM tree_nodes WHERE parent_node_id IS NULL AND node_id!=?",
                (node_id,)).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM tree_nodes WHERE parent_node_id=? AND node_id!=?",
                (node.parent_node_id, node_id)).fetchall()
        return [_row_to_node(r) for r in rows]
    finally:
        con.close()
