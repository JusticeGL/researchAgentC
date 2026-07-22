"""本项目最重要的文件。每条不变式一个测试。见 IMPLEMENTATION.md §1、§6。

测试先行：这些测试在实现完成前应当失败，且**不允许**为让它们通过而放宽不变式。
"""
import ast
import sqlite3
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# I1  runs / metrics append-only
# ---------------------------------------------------------------------------
def test_runs_append_only(db_path, base_run_fields):
    from core import results

    rid = results.record_run(**base_run_fields())
    con = sqlite3.connect(db_path)
    with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError)):
        con.execute("UPDATE runs SET status='hacked' WHERE run_id=?", (rid,))
        con.commit()
    with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError)):
        con.execute("DELETE FROM runs WHERE run_id=?", (rid,))
        con.commit()
    con.close()


def test_metrics_append_only(db_path, base_run_fields):
    from core import results

    rid = results.record_run(metrics={"test_acc": 0.5}, **base_run_fields())
    con = sqlite3.connect(db_path)
    with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError)):
        con.execute("UPDATE metrics SET value=0.99 WHERE run_id=?", (rid,))
        con.commit()
    with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError)):
        con.execute("DELETE FROM metrics WHERE run_id=?", (rid,))
        con.commit()
    con.close()


def test_no_update_delete_api_on_results():
    """I2/规约：results 不得提供 update_run / fix_metric / delete_run。"""
    from core import results

    for forbidden in ("update_run", "fix_metric", "delete_run", "delete_metric"):
        assert not hasattr(results, forbidden), f"results 不应有 {forbidden}"


# ---------------------------------------------------------------------------
# I2  指标值不可被字符串化
# ---------------------------------------------------------------------------
def test_metric_str_raises(db_path, base_run_fields):
    from core import results

    rid = results.record_run(metrics={"acc": 0.72}, **base_run_fields())
    m = results.get_metric(rid, "acc")
    with pytest.raises(TypeError):
        str(m)
    with pytest.raises(TypeError):
        f"{m}"
    with pytest.raises(TypeError):
        "acc = %s" % m
    with pytest.raises(TypeError):
        "acc = {}".format(m)


def test_metric_has_no_value_escape_hatches(db_path, base_run_fields):
    """禁止事项 §2：不得给 Metric 加 .value / __float__ 等便利方法。"""
    from core import results

    rid = results.record_run(metrics={"acc": 0.72}, **base_run_fields())
    m = results.get_metric(rid, "acc")
    assert not hasattr(m, "value")
    assert not hasattr(m, "__float__")
    with pytest.raises(TypeError):
        float(m)


def test_agg_str_raises(db_path, base_run_fields):
    from core import results

    rids = [
        results.record_run(metrics={"acc": v}, **base_run_fields(seed=i))
        for i, v in enumerate([0.6, 0.7, 0.8])
    ]
    a = results.agg(rids, "acc")
    with pytest.raises(TypeError):
        str(a)
    with pytest.raises(TypeError):
        f"{a}"


# ---------------------------------------------------------------------------
# I3  论文里的数字只能来自模板替换：unwrap 只在 core/render.py 被调用
# ---------------------------------------------------------------------------
def _iter_source_files():
    skip = {".venv", "build", "__pycache__", ".git", ".pytest_cache", "tests"}
    for p in REPO_ROOT.rglob("*.py"):
        if any(part in skip for part in p.relative_to(REPO_ROOT).parts):
            continue
        yield p


def test_unwrap_callsites():
    render_file = (REPO_ROOT / "core" / "render.py").resolve()
    offenders = []
    for path in _iter_source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "unwrap":
                    if path.resolve() != render_file:
                        offenders.append(f"{path}:{node.lineno}")
    assert not offenders, f"unwrap() 只能在 core/render.py 调用，越界：{offenders}"


# ---------------------------------------------------------------------------
# I4  \cite{k} 的 k 必须在 corpus 中
# ---------------------------------------------------------------------------
def test_render_rejects_unknown_cite(tmp_path):
    from core import corpus, render

    corpus_path = tmp_path / "corpus.sqlite"
    corpus.init_db(corpus_path)
    src = tmp_path / "paper.md"
    src.write_text("## Results\nAs shown \\cite{ghost2099}, it works.\n", encoding="utf-8")
    with pytest.raises(render.UnknownCitationError):
        render.render_file(src, tmp_path / "build", corpus_path=corpus_path,
                           results_path=None, config_path=None)


# ---------------------------------------------------------------------------
# I3 (裸数字) 渲染拒绝 Results 章节里未经模板的裸数字
# ---------------------------------------------------------------------------
def test_render_rejects_bare_number_in_results(tmp_path, db_path, base_run_fields):
    from core import corpus, render

    corpus_path = tmp_path / "corpus.sqlite"
    corpus.init_db(corpus_path)
    src = tmp_path / "paper.md"
    # 0.87 是裸数字，未走模板 → 必须被拒
    src.write_text("## Results\nOur accuracy is 0.87 which is great.\n", encoding="utf-8")
    with pytest.raises(render.BareNumberError):
        render.render_file(src, tmp_path / "build", corpus_path=corpus_path,
                           results_path=db_path,
                           config_path=REPO_ROOT / "config" / "render.yaml")


# ---------------------------------------------------------------------------
# I5  入库文献必须有 DOI 或 arXiv ID
# ---------------------------------------------------------------------------
def test_corpus_rejects_paper_without_id(tmp_path):
    from core import corpus

    corpus_path = tmp_path / "corpus.sqlite"
    corpus.init_db(corpus_path)
    with pytest.raises(Exception):
        corpus.add_paper(
            corpus_path,
            key="noid2020",
            doi=None,
            arxiv_id=None,
            title="A paper without any id",
            authors="Nobody",
            year=2020,
        )


# ---------------------------------------------------------------------------
# I6  harness 内容变更使实验作废：harness_hash 随文件内容变化
# ---------------------------------------------------------------------------
def test_harness_hash_changes_on_edit(tmp_path, monkeypatch):
    import harness

    original = harness.harness_hash()
    # 临时新建一个 .py 文件到 harness 包目录，断言 hash 变化，随后清理
    extra = Path(harness.__file__).resolve().parent / "_tmp_probe_delete_me.py"
    try:
        extra.write_text("# probe\nX = 1\n", encoding="utf-8")
        changed = harness.harness_hash()
        assert changed != original, "harness 内容变了 harness_hash 必须变"
    finally:
        if extra.exists():
            extra.unlink()
    restored = harness.harness_hash()
    assert restored == original, "删除探针后 hash 应恢复"


# ---------------------------------------------------------------------------
# I7  测试集访问被记录且受限：一次性 token
# ---------------------------------------------------------------------------
def test_test_token_single_use(db_path):
    from core import results

    token = results.issue_test_token("c_test")
    # 同一 contract 不能再签发
    with pytest.raises(Exception):
        results.issue_test_token("c_test")
    results.redeem_test_token(token, caller="test::single_use")
    # 用过即废
    with pytest.raises(Exception):
        results.redeem_test_token(token, caller="test::again")


def test_holdout_access_logged(db_path):
    from core import results

    token = results.issue_test_token("c_hold")
    results.redeem_test_token(token, caller="test::logged")
    con = sqlite3.connect(db_path)
    n = con.execute(
        "SELECT COUNT(*) FROM holdout_access WHERE contract_id=?", ("c_hold",)
    ).fetchone()[0]
    con.close()
    assert n == 1, "redeem 后必须在 holdout_access 留一条记录"


# ---------------------------------------------------------------------------
# I6  作废用 run_invalidations 表达，runs 表不被 UPDATE
# ---------------------------------------------------------------------------
def test_invalidate_uses_side_table(db_path, base_run_fields):
    from core import results

    rid = results.record_run(**base_run_fields())
    results.invalidate(rid, reason="harness changed")
    rec = results.get_run(rid)
    assert rec.status == "invalid" or rec.is_invalid, "作废后查询应视为 invalid"
    con = sqlite3.connect(db_path)
    n = con.execute(
        "SELECT COUNT(*) FROM run_invalidations WHERE run_id=?", (rid,)
    ).fetchone()[0]
    con.close()
    assert n == 1


# ---------------------------------------------------------------------------
# I8  契约冻结后不可变
# ---------------------------------------------------------------------------
def _minimal_contract(**overrides):
    from core.contract import (
        AblationSpec,
        BaselineSpec,
        Budget,
        Contract,
        StatPlan,
    )

    data = dict(
        contract_id="c_demo",
        version=1,
        parent_version=None,
        question="CSP+LDA 能否在运动想象上达到阈值？",
        hypothesis="空间滤波捕捉的判别性节律是性能主因。",
        datasets=["BNCI2014_001"],
        split_protocol="within_session",
        paradigm="motor_imagery",
        baselines=[BaselineSpec(name="csp_lda", cite_key="ang2012csp", reproduced_run_ids=[])],
        primary_metric="acc",
        success_threshold=0.75,
        direction="maximize",
        stat_plan=StatPlan(n_seeds=5, test="paired_t", correction="none", min_effect_size=0.02),
        budget=Budget(gpu_hours=10, usd=20, wall_clock_h=24, per_node_gpu_hours=2),
        kill_criteria=["dev acc < chance+0.05 连续 10 节点"],
        preregistered_ablations=[
            AblationSpec(id="a1", description="移除 CSP", falsifies="性能来自空间滤波而非分类器")
        ],
        novelty_evidence=["ang2012csp"],
        novelty_note="与最近邻工作的差异说明。",
    )
    data.update(overrides)
    return Contract(**data)


def test_contract_frozen_is_immutable():
    c = _minimal_contract()
    c.baselines[0].reproduced_run_ids = ["r1"]
    frozen = c.freeze(out_dir=None, write=False)
    with pytest.raises(Exception):
        frozen.question = "改了"


def test_contract_freeze_requires_reproduced_baseline():
    c = _minimal_contract()
    # reproduced_run_ids 为空 → freeze 必须报错
    with pytest.raises(Exception):
        c.freeze(out_dir=None, write=False)


def test_contract_hash_excludes_reproduced_run_ids():
    """content_hash 不依赖 reproduced_run_ids，回填 run_id 不改变 hash。"""
    c = _minimal_contract()
    h_before = c.content_hash_value()
    c.baselines[0].reproduced_run_ids = ["r1", "r2"]
    h_after = c.content_hash_value()
    assert h_before == h_after


def test_contract_new_version_keeps_old():
    c = _minimal_contract()
    c.baselines[0].reproduced_run_ids = ["r1"]
    v2 = c.new_version(question="新版本问题")
    assert v2.version == 2
    assert v2.parent_version == 1
    assert v2.question == "新版本问题"
