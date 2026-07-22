"""结果库。见 IMPLEMENTATION.md §5.1。

强制的不变式：
  I1  runs / metrics append-only（trigger 在 SQLite 层强制，UPDATE/DELETE 直接 ABORT）
  I2  指标值不可被字符串化（Metric / Agg 的 __str__ / __format__ 抛 TypeError）
  I6  run 作废写 run_invalidations 表，绝不 UPDATE runs
  I7  测试集访问需一次性 token，redeem 写 holdout_access

**不要**给本模块加 update_run / fix_metric / delete_run，哪怕"仅供调试"。
**不要**给 Metric / Agg 加 .value / __float__ 或任何能进 f-string 的便利方法。
"""
from __future__ import annotations

import inspect
import math
import sqlite3
import uuid
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

_SCHEMA = Path(__file__).resolve().parent.parent / "schema" / "results.sql"

# 当前活动的结果库路径（由 init_db 设置）。函数也可显式传 db= 覆盖。
_active_db: Optional[Path] = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve(db: Optional[Path]) -> Path:
    p = Path(db) if db is not None else _active_db
    if p is None:
        raise RuntimeError("结果库未初始化，请先调用 results.init_db(path)")
    return p


def _connect(db: Optional[Path] = None) -> sqlite3.Connection:
    con = sqlite3.connect(_resolve(db))
    con.execute("PRAGMA foreign_keys = ON")
    con.row_factory = sqlite3.Row
    return con


def init_db(path: Path) -> None:
    """建库（幂等）并把它设为当前活动库。"""
    global _active_db
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(_SCHEMA.read_text(encoding="utf-8"))
    con.commit()
    con.close()
    _active_db = path


# ---------------------------------------------------------------------------
# 核心类型
# ---------------------------------------------------------------------------
class Metric:
    """指标值。刻意设计成无法进入字符串。见 I2。"""

    __slots__ = ("run_id", "name", "subject", "_v")

    def __init__(self, run_id: str, name: str, subject: Optional[str], value: float):
        self.run_id = run_id
        self.name = name
        self.subject = subject
        self._v = float(value)

    def __str__(self):  # noqa: D401
        raise TypeError(
            "Metric 不能被字符串化。论文中的数字必须写成 "
            "{{run:<run_id>.<metric>}} 模板，由 core.render 替换。"
        )

    def __format__(self, spec):
        return self.__str__()

    def __repr__(self):
        return f"<Metric {self.name}@{self.run_id[:8]}>"

    def unwrap(self) -> float:
        """只允许 core/render.py 调用。见 tests/test_invariants.py::test_unwrap_callsites。"""
        return self._v


AggStats = namedtuple("AggStats", ["mean", "std", "n", "ci_low", "ci_high"])


class Agg:
    """多 run（多 seed）聚合。同样不可字符串化。见 I2。

    论文默认只能引用 Agg；引用单次 run 必须显式标注。
    CI 用样本标准差 + 正态近似（1.96）算 95% 区间；n<=1 时 std=0、区间退化为点。
    """

    __slots__ = ("name", "run_ids", "_mean", "_std", "_n", "_ci_low", "_ci_high")

    _Z95 = 1.959963984540054

    def __init__(self, name: str, run_ids: List[str], values: List[float]):
        self.name = name
        self.run_ids = list(run_ids)
        n = len(values)
        self._n = n
        if n == 0:
            raise ValueError(f"agg({name}) 没有任何值")
        mean = sum(values) / n
        if n > 1:
            var = sum((v - mean) ** 2 for v in values) / (n - 1)  # 样本方差 ddof=1
            std = math.sqrt(var)
            ci_half = self._Z95 * std / math.sqrt(n)
        else:
            std = 0.0
            ci_half = 0.0
        self._mean = mean
        self._std = std
        self._ci_low = mean - ci_half
        self._ci_high = mean + ci_half

    def __str__(self):
        raise TypeError(
            "Agg 不能被字符串化。论文中的聚合数字必须写成 "
            "{{agg:<tag>.<metric>}} 模板，由 core.render 替换。"
        )

    def __format__(self, spec):
        return self.__str__()

    def __repr__(self):
        return f"<Agg {self.name} n={self._n}>"

    def unwrap(self) -> AggStats:
        """只允许 core/render.py 调用。"""
        return AggStats(self._mean, self._std, self._n, self._ci_low, self._ci_high)

    def confirms_threshold(self, threshold: float, direction: str) -> bool:
        """确认判定：用 CI 的**下界**（maximize）/**上界**（minimize）对比阈值。

        返回 bool（不泄露原始浮点，故不违反 I2/I3 —— 数字仍只能经 render 进论文）。
        这是"确认协议"区分真实提升与运气 fluke 的关键：均值过线不算数，
        必须区间下界也过线。见 loop/confirm.py。
        """
        if direction == "maximize":
            return self._ci_low >= threshold
        if direction == "minimize":
            return self._ci_high <= threshold
        raise ValueError(f"未知 direction：{direction}")


RunRecord = namedtuple(
    "RunRecord",
    [
        "run_id", "parent_run_id", "contract_id", "contract_hash", "harness_hash",
        "code_sha", "config_hash", "data_sha", "env_hash", "seed", "split", "phase",
        "status", "failure_class", "wall_clock_s", "gpu_hours", "cost_usd",
        "artifacts_dir", "created_at", "is_invalid",
    ],
)


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------
_RUN_COLUMNS = [
    "run_id", "parent_run_id", "contract_id", "contract_hash", "harness_hash",
    "code_sha", "config_hash", "data_sha", "env_hash", "seed", "split", "phase",
    "status", "failure_class", "wall_clock_s", "gpu_hours", "cost_usd",
    "artifacts_dir", "created_at",
]
_REQUIRED = [
    "contract_id", "contract_hash", "harness_hash", "code_sha", "config_hash",
    "data_sha", "env_hash", "seed", "split", "phase", "status",
]


def record_run(
    db: Optional[Path] = None,
    metrics: Optional[Dict[str, float]] = None,
    subject_metrics: Optional[Dict[str, Dict[str, float]]] = None,
    **fields,
) -> str:
    """插入一条 run（append-only）及其指标，返回 run_id。

    metrics:         整体指标 {name: value}（subject=NULL）
    subject_metrics: 分被试指标 {subject: {name: value}}
    """
    missing = [k for k in _REQUIRED if k not in fields or fields[k] is None]
    if missing:
        raise ValueError(f"record_run 缺少必填字段：{missing}")

    run_id = fields.pop("run_id", None) or uuid.uuid4().hex
    row = {c: None for c in _RUN_COLUMNS}
    row["run_id"] = run_id
    row["created_at"] = _now()
    for k, v in fields.items():
        if k not in _RUN_COLUMNS:
            raise ValueError(f"record_run 收到未知字段：{k}")
        row[k] = v

    con = _connect(db)
    try:
        cols = ", ".join(_RUN_COLUMNS)
        ph = ", ".join(["?"] * len(_RUN_COLUMNS))
        con.execute(f"INSERT INTO runs ({cols}) VALUES ({ph})",
                    [row[c] for c in _RUN_COLUMNS])
        if metrics:
            for name, value in metrics.items():
                con.execute(
                    "INSERT INTO metrics (run_id, subject, name, value) VALUES (?, NULL, ?, ?)",
                    (run_id, name, float(value)),
                )
        if subject_metrics:
            for subject, mm in subject_metrics.items():
                for name, value in mm.items():
                    con.execute(
                        "INSERT INTO metrics (run_id, subject, name, value) VALUES (?, ?, ?, ?)",
                        (run_id, subject, name, float(value)),
                    )
        con.commit()
    finally:
        con.close()
    return run_id


def invalidate(run_id: str, reason: str, db: Optional[Path] = None) -> None:
    """作废一次 run。不 UPDATE runs（I1），改往 run_invalidations 插记录（I6）。"""
    if not reason:
        raise ValueError("invalidate 必须给出理由")
    con = _connect(db)
    try:
        exists = con.execute("SELECT 1 FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not exists:
            raise KeyError(f"run 不存在：{run_id}")
        con.execute(
            "INSERT INTO run_invalidations (run_id, reason, created_at) VALUES (?, ?, ?)",
            (run_id, reason, _now()),
        )
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------
def _is_invalid(con: sqlite3.Connection, run_id: str) -> bool:
    r = con.execute(
        "SELECT 1 FROM run_invalidations WHERE run_id=? LIMIT 1", (run_id,)
    ).fetchone()
    return r is not None


def _row_to_record(con: sqlite3.Connection, row: sqlite3.Row) -> RunRecord:
    inv = _is_invalid(con, row["run_id"])
    status = "invalid" if inv else row["status"]
    return RunRecord(
        run_id=row["run_id"], parent_run_id=row["parent_run_id"],
        contract_id=row["contract_id"], contract_hash=row["contract_hash"],
        harness_hash=row["harness_hash"], code_sha=row["code_sha"],
        config_hash=row["config_hash"], data_sha=row["data_sha"],
        env_hash=row["env_hash"], seed=row["seed"], split=row["split"],
        phase=row["phase"], status=status, failure_class=row["failure_class"],
        wall_clock_s=row["wall_clock_s"], gpu_hours=row["gpu_hours"],
        cost_usd=row["cost_usd"], artifacts_dir=row["artifacts_dir"],
        created_at=row["created_at"], is_invalid=inv,
    )


def get_run(run_id: str, db: Optional[Path] = None) -> RunRecord:
    con = _connect(db)
    try:
        row = con.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"run 不存在：{run_id}")
        return _row_to_record(con, row)
    finally:
        con.close()


def get_metric(run_id: str, name: str, subject: Optional[str] = None,
               db: Optional[Path] = None) -> Metric:
    con = _connect(db)
    try:
        if subject is None:
            row = con.execute(
                "SELECT value FROM metrics WHERE run_id=? AND name=? AND subject IS NULL",
                (run_id, name),
            ).fetchone()
        else:
            row = con.execute(
                "SELECT value FROM metrics WHERE run_id=? AND name=? AND subject=?",
                (run_id, name, subject),
            ).fetchone()
        if row is None:
            raise KeyError(f"指标不存在：run={run_id} name={name} subject={subject}")
        return Metric(run_id, name, subject, row["value"])
    finally:
        con.close()


def search_metric_value(run_id: str, name: str, subject: Optional[str] = None,
                        db: Optional[Path] = None) -> float:
    """**仅供搜索内部**读取原始 dev 分数（用于排序/裁决），返回裸 float。

    这不是 Metric，也永远不会进论文（tree_nodes.dev_score 也是裸 float）；
    论文数字仍只能经 {{run:}}/{{agg:}} 模板由 render 产出。故不违反 I2/I3。
    """
    # 直接取值：Metric.unwrap() 受 I3 约束仅限 render.py，这里从底层取原始值。
    con = _connect(db)
    try:
        if subject is None:
            row = con.execute(
                "SELECT value FROM metrics WHERE run_id=? AND name=? AND subject IS NULL",
                (run_id, name)).fetchone()
        else:
            row = con.execute(
                "SELECT value FROM metrics WHERE run_id=? AND name=? AND subject=?",
                (run_id, name, subject)).fetchone()
        return float(row["value"])
    finally:
        con.close()


def agg(run_ids: List[str], name: str, db: Optional[Path] = None) -> Agg:
    """对一组 run 的整体指标（subject=NULL）聚合。作废的 run 不纳入。"""
    con = _connect(db)
    try:
        values: List[float] = []
        used: List[str] = []
        for rid in run_ids:
            if _is_invalid(con, rid):
                continue
            row = con.execute(
                "SELECT value FROM metrics WHERE run_id=? AND name=? AND subject IS NULL",
                (rid, name),
            ).fetchone()
            if row is None:
                raise KeyError(f"run {rid} 没有整体指标 {name}")
            values.append(row["value"])
            used.append(rid)
        return Agg(name, used, values)
    finally:
        con.close()


def query_runs(db: Optional[Path] = None, **filters) -> List[RunRecord]:
    """按等值条件过滤 runs。特殊过滤 include_invalid=False（默认）排除作废的 run。"""
    include_invalid = filters.pop("include_invalid", False)
    con = _connect(db)
    try:
        clauses, params = [], []
        for k, v in filters.items():
            if k not in _RUN_COLUMNS:
                raise ValueError(f"query_runs 未知过滤字段：{k}")
            clauses.append(f"{k}=?")
            params.append(v)
        sql = "SELECT * FROM runs"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at"
        rows = con.execute(sql, params).fetchall()
        out = [_row_to_record(con, r) for r in rows]
        if not include_invalid:
            out = [r for r in out if not r.is_invalid]
        return out
    finally:
        con.close()


# ---------------------------------------------------------------------------
# I7  测试集 token
# ---------------------------------------------------------------------------
def issue_test_token(contract_id: str, db: Optional[Path] = None) -> str:
    """每个 contract 只能签发一次（UNIQUE 约束强制）。"""
    token = uuid.uuid4().hex
    con = _connect(db)
    try:
        con.execute(
            "INSERT INTO test_tokens (token, contract_id, issued_at) VALUES (?, ?, ?)",
            (token, contract_id, _now()),
        )
        con.commit()
    except sqlite3.IntegrityError as e:
        raise RuntimeError(
            f"contract {contract_id} 已签发过测试 token，不可再签（I7）"
        ) from e
    finally:
        con.close()
    return token


def redeem_test_token(token: str, caller: str, run_id: Optional[str] = None,
                      db: Optional[Path] = None) -> None:
    """兑付一次性 token：作废 token 并写 holdout_access。用过即废。"""
    con = _connect(db)
    try:
        row = con.execute(
            "SELECT contract_id, redeemed_at FROM test_tokens WHERE token=?", (token,)
        ).fetchone()
        if row is None:
            raise KeyError("未知的测试 token")
        if row["redeemed_at"] is not None:
            raise RuntimeError("测试 token 已被兑付过，一次性 token 不可复用（I7）")
        contract_id = row["contract_id"]
        # token 表允许 UPDATE（它不是 append-only 的 runs/metrics）
        con.execute(
            "UPDATE test_tokens SET redeemed_at=? WHERE token=?", (_now(), token)
        )
        stack = " <- ".join(
            f"{fr.function}@{Path(fr.filename).name}:{fr.lineno}"
            for fr in inspect.stack()[1:6]
        )
        caller_info = f"{caller} | {stack}"
        con.execute(
            "INSERT INTO holdout_access (contract_id, run_id, token, caller, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (contract_id, run_id, token, caller_info, _now()),
        )
        con.commit()
    finally:
        con.close()
