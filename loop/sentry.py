"""作弊探测 + 熔断。见 IMPLEMENTATION-P2.md §4.7、§4.1。

对每个节点的 patch 做静态检查，对执行做运行时/边界检查：

| 规则 | 动作 |
| S1 写了 harness/ core/ contracts/            | 熔断停机 halt |
| S2 写文件到 solution/ 和 artifacts/<node> 之外 | 熔断停机 halt |
| S3 裸 except / except Exception: pass 包住评测调用 | run 作废 invalidate + 人工审阅 |
| S4 函数体只有 return <literal> 且函数名含 metric/score/acc | run 作废 invalidate |
| S5 dev 阶段代码里出现 test split 相关标识符 | run 作废 invalidate + 人工审阅 |
| S6 sys.path / importlib 操作指向 harness      | run 作废 invalidate + 人工审阅 |
| S7 训练脚本里出现 requests / urllib / socket    | run 作废 invalidate |
| S8 harness_hash() 与 run 记录不符              | run 作废 invalidate |

**误报是可接受的成本。** 漏报的代价是整篇论文作废，误报只是 agent 重写几分钟。
"""
from __future__ import annotations

import ast
import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

_SCHEMA = Path(__file__).resolve().parent.parent / "schema" / "sentry.sql"

HALT = "halt"
INVALIDATE = "invalidate"


class BoundaryViolation(Exception):
    """越界写 harness/core/contracts —— 触发熔断停机（不是作废单次 run）。"""


@dataclass
class SentryEvent:
    rule: str
    severity: str
    detail: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db(path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(_SCHEMA.read_text(encoding="utf-8"))
    con.commit()
    con.close()


def record_events(db: Path, node_id: str, events: List[SentryEvent]) -> None:
    con = sqlite3.connect(db)
    try:
        for e in events:
            con.execute(
                "INSERT INTO sentry_events (node_id, rule, severity, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (node_id, e.rule, e.severity, e.detail, _now()),
            )
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 边界 hash（I9 的第二层双保险）
# ---------------------------------------------------------------------------
_PROTECTED = ("harness", "core", "contracts")


def boundary_hashes(repo_root: Path) -> Dict[str, str]:
    """harness/ core/ contracts/ 下所有文件的 {relpath: sha256}。"""
    repo_root = Path(repo_root)
    out: Dict[str, str] = {}
    for top in _PROTECTED:
        base = repo_root / top
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                rel = p.relative_to(repo_root).as_posix()
                out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def assert_boundary_intact(before: Dict[str, str], after: Dict[str, str]) -> None:
    """比对 harness/ core/ contracts/ 的文件 hash。任一变化 → BoundaryViolation（熔断）。"""
    changed = [k for k in set(before) | set(after) if before.get(k) != after.get(k)]
    if changed:
        raise BoundaryViolation(
            f"只读边界被破坏（I9）：{sorted(changed)}。触发熔断停机。"
        )


# ---------------------------------------------------------------------------
# S1 / S2：写路径检查（运行时）
# ---------------------------------------------------------------------------
def check_written_paths(written_paths: List[str], node_id: str, repo_root: Path
                        ) -> List[SentryEvent]:
    repo_root = Path(repo_root).resolve()
    solution = (repo_root / "solution").resolve()
    artifacts = (repo_root / "artifacts" / node_id).resolve()
    events: List[SentryEvent] = []
    for raw in written_paths:
        p = Path(raw).resolve()
        rel_parts = None
        try:
            rel_parts = p.relative_to(repo_root).parts
        except ValueError:
            pass
        if rel_parts and rel_parts[0] in _PROTECTED:
            events.append(SentryEvent("S1", HALT, f"写入只读区域：{raw}"))
            continue
        in_solution = _is_within(p, solution)
        in_artifacts = _is_within(p, artifacts)
        if not (in_solution or in_artifacts):
            events.append(SentryEvent(
                "S2", HALT, f"写到 solution/ 与 artifacts/{node_id}/ 之外：{raw}"))
    return events


def _is_within(p: Path, base: Path) -> bool:
    try:
        p.relative_to(base)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# S8：harness_hash 校验
# ---------------------------------------------------------------------------
def check_harness_hash(recorded: str, current: str) -> List[SentryEvent]:
    if recorded != current:
        return [SentryEvent(
            "S8", INVALIDATE,
            f"harness_hash 不符：run={recorded[:12]} vs 当前={current[:12]}")]
    return []


# ---------------------------------------------------------------------------
# S3–S7：patch / 源码静态检查
# ---------------------------------------------------------------------------
_METRIC_NAME_RE = re.compile(r"(metric|score|acc)", re.IGNORECASE)
_EVAL_CALLS = {"evaluate_dev", "evaluate_test", "evaluate", "eval"}
_TEST_SPLIT_IDENT = re.compile(
    r"\b(X_test|y_test|test_split|test_subjects?|test_set|holdout|"
    r"testing_data|test_data|X_holdout)\b")
_NET_MODULES = ("requests", "urllib", "socket", "httpx", "aiohttp")


def _added_code_from_patch(patch: str) -> str:
    """从 unified diff 里抽取新增行（去掉 '+' 前缀）；非 diff 文本原样返回。"""
    lines = patch.splitlines()
    if not any(l.startswith(("+++", "---", "@@")) for l in lines):
        return patch
    out = []
    for l in lines:
        if l.startswith("+") and not l.startswith("+++"):
            out.append(l[1:])
    return "\n".join(out)


def scan_code(code: str) -> List[SentryEvent]:
    """对一段 Python 源码做 S3–S7 静态检查。解析失败时退回逐行正则。"""
    events: List[SentryEvent] = []
    # 行级检查（即使 ast 解析失败也可用）：S5 / S7
    for m in _TEST_SPLIT_IDENT.finditer(code):
        events.append(SentryEvent(
            "S5", INVALIDATE, f"dev 阶段代码出现 test split 标识符：{m.group(0)}"))
    for mod in _NET_MODULES:
        if re.search(rf"\b(import\s+{mod}|from\s+{mod}\b|{mod}\.)", code):
            events.append(SentryEvent(
                "S7", INVALIDATE, f"训练脚本出现联网模块：{mod}"))
    if re.search(r"sys\.path.*harness|importlib.*harness|harness.*sys\.path", code):
        events.append(SentryEvent(
            "S6", INVALIDATE, "sys.path / importlib 操作疑似指向 harness"))

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return _dedup_events(events)

    for node in ast.walk(tree):
        # S4：函数体只有 return <literal> 且函数名含 metric/score/acc
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body = [n for n in node.body if not isinstance(n, ast.Expr)
                    or not isinstance(getattr(n, "value", None), ast.Constant)]
            # 去掉纯 docstring 后，若仅剩一条 return 常量
            real = [n for n in node.body if not (
                isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
            if (len(real) == 1 and isinstance(real[0], ast.Return)
                    and isinstance(real[0].value, ast.Constant)
                    and _METRIC_NAME_RE.search(node.name)):
                events.append(SentryEvent(
                    "S4", INVALIDATE,
                    f"函数 {node.name} 直接 return 常量 {real[0].value.value!r}（疑似硬编码指标）"))
        # S6（ast）：importlib 引用 harness
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr in ("import_module", "reload"):
                for a in node.args:
                    if isinstance(a, ast.Constant) and isinstance(a.value, str) \
                            and "harness" in a.value:
                        events.append(SentryEvent(
                            "S6", INVALIDATE, f"importlib 指向 harness：{a.value}"))
        # S3：裸 except / except Exception: pass 包住评测调用
        if isinstance(node, ast.Try):
            calls = {c.func.id for c in ast.walk(node)
                     if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
            calls |= {c.func.attr for c in ast.walk(node)
                      if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)}
            wraps_eval = bool(calls & _EVAL_CALLS)
            for h in node.handlers:
                bare = h.type is None
                catches_exc = (isinstance(h.type, ast.Name) and h.type.id == "Exception")
                body_pass = len(h.body) == 1 and isinstance(h.body[0], ast.Pass)
                if wraps_eval and (bare or (catches_exc and body_pass)):
                    events.append(SentryEvent(
                        "S3", INVALIDATE,
                        "裸 except / except Exception: pass 包住了评测调用"))
    return _dedup_events(events)


def scan_patch(patch: str) -> List[SentryEvent]:
    """对一个 patch（unified diff 或整段源码）做 S3–S7 静态检查。"""
    return scan_code(_added_code_from_patch(patch))


def _dedup_events(events: List[SentryEvent]) -> List[SentryEvent]:
    seen, out = set(), []
    for e in events:
        key = (e.rule, e.detail)
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


def has_halt(events: List[SentryEvent]) -> bool:
    return any(e.severity == HALT for e in events)
