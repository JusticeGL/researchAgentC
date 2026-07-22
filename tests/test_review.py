"""第四期 LLM 审稿测试。见 IMPLEMENTATION-P4.md §6。

验收核心：test_objection_without_locator_discarded
其余覆盖 I26 / 无 score 字段 / autocheck 转断言。
"""
from pathlib import Path

import pytest

from review import autocheck as ac
from review import panel
from review.objection import Objection, parse_objection, validate_locator

RENDERED = """# 1 Introduction

Some text on line two.
We report accuracy in Table 2 and show Figure 3.

## 4.2 Results

The method improves accuracy [claim:mech_a1].
"""


# ---------------------------------------------------------------------------
# I26：无 locator 的意见直接丢弃
# ---------------------------------------------------------------------------
def test_objection_without_locator_discarded():
    def review_fn(model, text):
        return [
            # 无法定位的"整体写作质量"意见 —— 必须被丢弃
            {"locator": "overall", "kind": "clarity", "checkable": False,
             "statement": "整体写作质量有待提高。"},
            # 合法意见 —— 保留
            {"locator": "L2", "kind": "clarity", "checkable": False,
             "statement": "第二行表述不清。"},
        ]

    out = panel.panel("paper1", RENDERED, ["m1"], review_fn)
    locators = [c["locator"] for c in out["comments"]]
    assert "overall" not in locators
    assert "L2" in locators
    assert any("overall" in str(d["raw"]) for d in out["discarded"])


def test_locator_forms_validate():
    ids = {"mech_a1"}
    assert validate_locator("L2", RENDERED)
    assert validate_locator("Table 2", RENDERED)
    assert validate_locator("Fig 3", RENDERED)
    assert validate_locator("claim:mech_a1", RENDERED, ids)
    assert validate_locator("§4.2", RENDERED)
    assert validate_locator("4.2", RENDERED)
    # 越界 / 不存在
    assert not validate_locator("L999", RENDERED)
    assert not validate_locator("Table 9", RENDERED)
    assert not validate_locator("claim:ghost", RENDERED, ids)
    assert not validate_locator("overall", RENDERED)
    assert not validate_locator("", RENDERED)


# ---------------------------------------------------------------------------
# 无 score 字段
# ---------------------------------------------------------------------------
def test_no_score_field_in_review_schema():
    sql = (Path(__file__).resolve().parent.parent / "schema" / "review.sql").read_text()
    # 只看 DDL 本身，剥离 -- 注释行（注释里会解释"没有 score 字段"，那不算列）
    import re
    ddl = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    assert not re.search(r"\bscore\b", ddl, re.IGNORECASE), \
        "review schema 不能有 score 字段（§5.1）"


def test_objection_rejects_score_field():
    # 带 score 的原始意见应被拒（extra=forbid）
    obj = parse_objection({"locator": "L2", "kind": "clarity", "checkable": False,
                           "statement": "x", "score": 4.5})
    assert obj is None


def test_objection_checkable_requires_suggested_check():
    obj = parse_objection({"locator": "L2", "kind": "factual", "checkable": True,
                           "statement": "数字不一致"})
    assert obj is None  # 缺 suggested_check


# ---------------------------------------------------------------------------
# autocheck：checkable 意见转断言，能驳回假意见
# ---------------------------------------------------------------------------
def test_checkable_objection_becomes_assertion_variance():
    # 一条"没有报告方差"的假意见；provenance 里确实有 n≥2 的聚合 → 应被驳回
    prov = {"replacements": [{"kind": "agg", "n": 5, "std": 0.03}]}
    obj = Objection(locator="Table 2", kind="unsupported", checkable=True,
                    statement="结果没有报告方差。",
                    suggested_check="检查是否报告了 std/方差")
    verdicts = ac.autocheck([obj], ctx=ac.AutocheckContext(provenance=prov))
    assert verdicts[0].status == ac.REJECTED


def test_checkable_variance_upheld_when_missing():
    prov = {"replacements": [{"kind": "run", "value": 0.8}]}  # 无 agg
    obj = Objection(locator="Table 2", kind="unsupported", checkable=True,
                    statement="没有报告方差。", suggested_check="检查 std")
    verdicts = ac.autocheck([obj], ctx=ac.AutocheckContext(provenance=prov))
    assert verdicts[0].status == ac.UPHELD


def test_checkable_claim_evidence():
    from writing import claims

    reg = claims.ClaimRegistry([
        claims.make_claim("mech_a1", "ablation", "a1")])
    obj_ok = Objection(locator="claim:mech_a1", kind="unsupported", checkable=True,
                       statement="claim 没有对应证据。", suggested_check="查 claim registry")
    obj_bad = Objection(locator="claim:ghost", kind="unsupported", checkable=True,
                        statement="claim 没有对应证据。", suggested_check="查 claim registry")
    verdicts = ac.autocheck([obj_ok, obj_bad],
                            ctx=ac.AutocheckContext(registry=reg))
    assert verdicts[0].status == ac.REJECTED   # 已注册 → 意见不成立
    assert verdicts[1].status == ac.UPHELD      # 未注册 → 意见成立


def test_non_checkable_stays_subjective():
    obj = Objection(locator="L2", kind="clarity", checkable=False,
                    statement="读起来别扭。")
    verdicts = ac.autocheck([obj])
    assert verdicts[0].status == ac.NOT_CONVERTIBLE


# ---------------------------------------------------------------------------
# panel 去重 + 落库
# ---------------------------------------------------------------------------
def test_panel_dedup_and_persist(tmp_path):
    db = tmp_path / "review.sqlite"
    panel.init_db(db)

    def review_fn(model, text):
        return [{"locator": "L2", "kind": "clarity", "checkable": False,
                 "statement": f"{model} 说第二行不清楚。"}]

    out = panel.panel("paper1", RENDERED, ["m1", "m2", "m3"], review_fn, db=db)
    # 三模型同一 (locator, kind) → 去重成一条
    assert len(out["comments"]) == 1

    import sqlite3
    con = sqlite3.connect(db)
    try:
        rows = con.execute("SELECT * FROM review_comments").fetchall()
        assert len(rows) == 1
        # 表里没有 score 列
        cols = [d[1] for d in con.execute("PRAGMA table_info(review_comments)").fetchall()]
        assert "score" not in cols
    finally:
        con.close()


def test_review_comments_append_only(tmp_path):
    db = tmp_path / "review.sqlite"
    panel.init_db(db)
    import sqlite3
    con = sqlite3.connect(db)
    try:
        con.execute(
            "INSERT INTO review_comments (comment_id, paper_id, locator, kind, "
            "checkable, statement, created_at) VALUES "
            "('rc1','p1','L2','clarity',0,'x','now')")
        con.commit()
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("DELETE FROM review_comments WHERE comment_id='rc1'")
            con.commit()
    finally:
        con.close()
