"""上下文构建测试。见 IMPLEMENTATION-P2.md §4.5、§6。"""
import inspect
from collections import namedtuple

FakeLesson = namedtuple("FakeLesson", ["kind", "text"])
FakeRow = namedtuple("FakeRow", ["node_id", "change_description", "dev_score"])


def test_context_includes_contract_fields(sample_contract):
    from loop import context

    ctx = context.build_context(sample_contract, budget_tokens=24000)
    assert "primary_metric" in ctx
    assert sample_contract.question in ctx


def test_context_under_token_budget(sample_contract):
    from loop import context

    lessons = [FakeLesson("insight", "x" * 500) for _ in range(50)]
    rows = [FakeRow(f"n{i}", "some change " * 20, 0.7) for i in range(100)]
    sibs = ["兄弟改动 " * 20 for _ in range(50)]
    ctx = context.build_context(
        sample_contract, parent_code="print(1)\n" * 2000,
        active_lessons=lessons, ancestor_rows=rows, sibling_descriptions=sibs,
        last_error="err\n" * 500, budget_tokens=2000)
    assert context.count_tokens(ctx) <= 2000


def test_context_excludes_full_runs_table(sample_contract):
    from loop import context

    # 结构性保证：build_context 不接受任何"全量 runs"参数
    params = set(inspect.signature(context.build_context).parameters)
    assert not (params & {"all_runs", "runs", "runs_table", "full_runs"})

    # 祖先结果表被限制在 ancestor_k 条以内
    rows = [FakeRow(f"n{i}", f"change {i}", 0.5 + i * 0.001) for i in range(100)]
    ctx = context.build_context(sample_contract, ancestor_rows=rows,
                                ancestor_k=15, budget_tokens=100000)
    appearing = sum(1 for i in range(100) if f"n{i}:" in ctx)
    assert appearing <= 15


def test_context_only_active_lessons(sample_contract):
    """context 只吃传进来的 active lessons；superseded 的在 ledger.active_lessons 层就被过滤。"""
    from loop import context

    ctx = context.build_context(sample_contract,
                                active_lessons=[FakeLesson("deadend", "只有这条")],
                                budget_tokens=24000)
    assert "只有这条" in ctx


def test_last_error_truncated_to_50_lines(sample_contract):
    from loop import context

    err = "\n".join(f"line{i}" for i in range(500))
    ctx = context.build_context(sample_contract, last_error=err, budget_tokens=100000)
    assert "line499" in ctx        # 末尾保留
    assert "line0\n" not in ctx    # 开头被截掉
