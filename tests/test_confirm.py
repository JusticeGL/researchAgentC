"""确认协议测试。见 IMPLEMENTATION-P2.md §4.6、§6。

核心验收：test_confirm_rejects_pure_noise_improvement —— 只靠运气过线的改动必须被判 fluke。
"""
import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIRM_SRC = REPO_ROOT / "loop" / "confirm.py"


def _record_acc_runs(db_path, base_run_fields, accs, phase="dev", seed0=0):
    from core import results

    ids = []
    for i, a in enumerate(accs):
        ids.append(results.record_run(
            db=db_path, metrics={"acc": a},
            **base_run_fields(seed=seed0 + i, phase=phase)))
    return ids


# ---------------------------------------------------------------------------
# I11：SearchVerdict 里没有 DONE / SUCCESS
# ---------------------------------------------------------------------------
def test_search_verdict_has_no_done_state():
    from loop.confirm import SearchVerdict

    names = {v.name for v in SearchVerdict}
    assert "DONE" not in names
    assert "SUCCESS" not in names
    assert names == {"CONTINUE", "CONFIRM_PENDING", "BUDGET_EXHAUSTED"}


def test_threshold_hit_returns_confirm_pending_not_done():
    from loop.confirm import SearchVerdict, judge_search

    v = judge_search(0.90, threshold=0.75, direction="maximize")
    assert v == SearchVerdict.CONFIRM_PENDING
    # 未过线 → 继续
    assert judge_search(0.60, 0.75, "maximize") == SearchVerdict.CONTINUE
    # 预算耗尽
    assert judge_search(0.90, 0.75, "maximize",
                        budget_exhausted=True) == SearchVerdict.BUDGET_EXHAUSTED


# ---------------------------------------------------------------------------
# 确认用 CI 下界，不是均值
# ---------------------------------------------------------------------------
def test_confirm_seeds_uses_ci_lower_bound(db_path, base_run_fields):
    from loop import confirm

    # 高方差：均值过线但 CI 下界不过线 → 不确认
    noisy = _record_acc_runs(db_path, base_run_fields, [0.95, 0.55, 0.95, 0.55, 0.92])
    assert confirm.confirm_seeds(noisy, "acc", 0.75, "maximize", db_path) is False

    # 低方差：稳稳过线 → 确认
    tight = _record_acc_runs(db_path, base_run_fields, [0.80, 0.81, 0.79, 0.80, 0.82],
                             seed0=100)
    assert confirm.confirm_seeds(tight, "acc", 0.75, "maximize", db_path) is True


# ---------------------------------------------------------------------------
# 核心验收：纯噪声提升被拒
# ---------------------------------------------------------------------------
def test_confirm_rejects_pure_noise_improvement(tmp_path, db_path, base_run_fields):
    from loop import confirm, ledger, tree
    from tests.conftest import _make_contract

    contract = _make_contract()

    tree_db = tmp_path / "tree.sqlite"
    ledger_db = tmp_path / "ledger.sqlite"
    tree.init_db(tree_db)
    ledger.init_db(ledger_db)

    # 某个 seed 走运，dev 命中阈值 0.90
    lucky = _record_acc_runs(db_path, base_run_fields, [0.90])[0]
    node = tree.add_node(tree_db, contract.contract_id, "只改了随机种子", "+seed",
                         run_ids=[lucky], dev_score=0.90, results_path=db_path)

    # 判裁：命中阈值 → CONFIRM_PENDING（绝不是 DONE）
    v = confirm.judge_search(0.90, contract.success_threshold, contract.direction)
    assert v == confirm.SearchVerdict.CONFIRM_PENDING

    # 换 5 个新 seed 复核：真实水平在 ~0.70，CI 下界不过线
    fresh = _record_acc_runs(db_path, base_run_fields,
                             [0.70, 0.72, 0.68, 0.71, 0.69], seed0=200)
    proto = confirm.ConfirmProtocol(contract, db_path, tree_db=tree_db,
                                    ledger_db=ledger_db)
    passed = proto.confirm_seeds_stage(node, fresh)

    assert passed is False
    assert proto.state == confirm.ConfirmState.SEARCHING          # 继续搜，不终止
    assert tree.get_node(tree_db, node).status == "fluke"          # 标记 fluke
    lessons = ledger.active_lessons(ledger_db, contract.contract_id)
    assert any(l.kind == "deadend" for l in lessons)               # 写了 deadend 台账
    deadend = next(l for l in lessons if l.kind == "deadend")
    assert set(fresh).issubset(set(deadend.evidence))              # 证据挂上


def test_genuine_improvement_advances_to_transfer(tmp_path, db_path, base_run_fields):
    from loop import confirm, ledger, tree
    from tests.conftest import _make_contract

    contract = _make_contract()
    tree_db = tmp_path / "tree.sqlite"
    ledger_db = tmp_path / "ledger.sqlite"
    tree.init_db(tree_db)
    ledger.init_db(ledger_db)

    good = _record_acc_runs(db_path, base_run_fields, [0.90])
    node = tree.add_node(tree_db, contract.contract_id, "真实改动", "+real",
                         run_ids=good, dev_score=0.90, results_path=db_path)
    fresh = _record_acc_runs(db_path, base_run_fields,
                             [0.80, 0.81, 0.79, 0.80, 0.82], seed0=300)
    proto = confirm.ConfirmProtocol(contract, db_path, tree_db=tree_db, ledger_db=ledger_db)
    assert proto.confirm_seeds_stage(node, fresh) is True
    assert proto.state == confirm.ConfirmState.CONFIRM_TRANSFER


# ---------------------------------------------------------------------------
# 通往测试集的唯一门是人工 approve
# ---------------------------------------------------------------------------
def test_request_test_requires_approval(db_path):
    from loop import confirm
    from tests.conftest import _make_contract

    proto = confirm.ConfirmProtocol(_make_contract(), db_path)
    proto.state = confirm.ConfirmState.GATE_PRE_TEST
    assert proto.request_test(False) == confirm.ConfirmState.REJECTED

    proto.state = confirm.ConfirmState.GATE_PRE_TEST
    assert proto.request_test(True) == confirm.ConfirmState.TEST_ONCE


def _stmt_contains(stmts, target):
    for s in stmts:
        for n in ast.walk(s):
            if n is target:
                return True
    return False


def test_no_code_path_reaches_test_once_without_human_approval():
    """AST 分析：所有把状态赋为 ConfirmState.TEST_ONCE 的语句，都必须在检查 approve 的分支里。"""
    tree = ast.parse(CONFIRM_SRC.read_text(encoding="utf-8"))
    parent = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node

    def is_test_once(v):
        return (isinstance(v, ast.Attribute) and v.attr == "TEST_ONCE"
                and isinstance(v.value, ast.Name) and v.value.id == "ConfirmState")

    assigns = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and is_test_once(node.value):
            assigns.append(node)
        elif isinstance(node, ast.AnnAssign) and node.value and is_test_once(node.value):
            assigns.append(node)

    assert assigns, "应至少有一处进入 TEST_ONCE（否则测试无意义）"

    offenders = []
    for a in assigns:
        guarded = False
        cur = a
        while cur in parent:
            up = parent[cur]
            if isinstance(up, ast.If):
                tokens = {n.id.lower() for n in ast.walk(up.test) if isinstance(n, ast.Name)}
                tokens |= {n.attr.lower() for n in ast.walk(up.test)
                           if isinstance(n, ast.Attribute)}
                # 必须在 True 分支（body）里，且分支条件涉及 approve
                if any("approv" in t for t in tokens) and _stmt_contains(up.body, a):
                    guarded = True
                    break
            cur = up
        if not guarded:
            offenders.append(a.lineno)

    assert not offenders, f"存在未经 approve 就进入 TEST_ONCE 的路径，行号：{offenders}"
