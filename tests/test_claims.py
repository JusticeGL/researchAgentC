"""Claim registry 测试。见 IMPLEMENTATION-P3.md §4.1、§5。

验收核心：test_harking_attempt_blocked。
"""
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CLAIMS_YAML = REPO_ROOT / "paper" / "claims.yaml"
PATTERNS = REPO_ROOT / "config" / "claims_patterns.yaml"


def _registry():
    from writing import claims

    return claims.load_registry(CLAIMS_YAML)


def _patterns():
    from writing import claims

    return claims.load_patterns(PATTERNS)


# ---------------------------------------------------------------------------
# 验收核心
# ---------------------------------------------------------------------------
def test_harking_attempt_blocked(sample_contract):
    """正文插一句契约里没有的强论断，checker 必须 FAIL，且这句无法通过注册。"""
    from writing import claims

    reg = _registry()
    text = (
        "## Results\n"
        "本方法在 within_session 协议下取得提升 [claim:primary]。\n"
        "我们还发现该方法在低信噪比被试上尤其有效。\n"   # HARKing：无 [claim:id]
    )
    violations = claims.check_paper(text, reg, sample_contract, audit=[],
                                    patterns=_patterns())
    # 1) 这句被抓成"未注册的强论断"
    unreg = [v for v in violations if v.code == "I16_UNREGISTERED"]
    assert unreg, "HARKing 句未被检出"
    assert any("尤其有效" in v.detail or "发现" in v.detail for v in unreg)

    # 2) 它无法通过注册：唯一"来源"是"从结果里看出来的"，不属于三种 kind
    with pytest.raises(claims.ClaimRegistryError):
        claims.make_claim("sneaky", kind="observed", ref="results")


def test_registry_valid_against_contract(sample_contract):
    from writing import claims

    reg = _registry()
    violations = claims.validate_registry(reg, sample_contract, audit=[])
    assert violations == [], f"示例 registry 应对示例契约合法，却有：{violations}"


def test_claim_without_source_cannot_register():
    from writing import claims

    with pytest.raises(claims.ClaimRegistryError):
        claims.make_claim("x", kind="manual", ref="whatever")
    with pytest.raises(claims.ClaimRegistryError):
        claims.make_claim("x", kind="", ref="")


def test_strong_claim_without_marker_detected():
    from writing import claims

    text = "本方法显著优于所有 baseline。"   # 强论断，无标记
    hits = claims.scan_paper_claims(text, _patterns())
    assert len(hits) == 1
    text_ok = "本方法显著优于所有 baseline [claim:primary]。"
    assert claims.scan_paper_claims(text_ok, _patterns()) == []


def test_contract_ref_must_be_field(sample_contract):
    from writing import claims

    reg = claims.ClaimRegistry([
        claims.make_claim("c1", "contract", "nonexistent_field")])
    v = claims.validate_registry(reg, sample_contract, audit=[])
    assert any(x.code == "I17_CONTRACT_REF" for x in v)


def test_ablation_not_preregistered_rejected(sample_contract):
    from writing import claims

    reg = claims.ClaimRegistry([
        claims.make_claim("m", "ablation", "a99")])   # a99 不在契约里
    v = claims.validate_registry(reg, sample_contract, audit=[])
    assert any(x.code == "I17_ABLATION_REF" for x in v)


def test_approved_claim_requires_audit_record(sample_contract):
    from writing import claims

    reg = claims.ClaimRegistry([
        claims.make_claim("newc", "approved", "newc")])
    # 无 audit 记录 → FAIL
    v = claims.validate_registry(reg, sample_contract, audit=[])
    assert any(x.code == "I17_APPROVED_NO_AUDIT" for x in v)
    # 有 claim_approval 批准记录 → 通过
    audit = [{"gate_type": "claim_approval", "subject_id": "newc",
              "field": "newc", "decision": "approve"}]
    v2 = claims.validate_registry(reg, sample_contract, audit=audit)
    assert not any(x.code == "I17_APPROVED_NO_AUDIT" for x in v2)


def test_unknown_marker_flagged(sample_contract):
    from writing import claims

    reg = _registry()
    text = "结论见 [claim:ghost]。"
    v = claims.markers_not_registered(text, reg)
    assert any(x.code == "I16_MARKER_UNKNOWN" for x in v)


def test_db_rejects_fourth_kind(tmp_path):
    """DB 层 CHECK 也兜底：非三种 kind 无法落库。"""
    import sqlite3

    from writing import claims

    db = tmp_path / "claims.sqlite"
    claims.init_db(db)
    con = sqlite3.connect(db)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO claims (claim_id, contract_id, source_kind, source_ref, "
            "registered_at) VALUES ('x','c','observed','r','t')")
        con.commit()
    con.close()


def test_persist_and_status(tmp_path, sample_contract):
    from writing import claims

    db = tmp_path / "claims.sqlite"
    claims.init_db(db)
    reg = _registry()
    claims.persist(reg, sample_contract.contract_id, db)
    claims.set_status(db, "primary", "held")
    import sqlite3

    con = sqlite3.connect(db)
    row = con.execute("SELECT status FROM claims WHERE claim_id='primary'").fetchone()
    con.close()
    assert row[0] == "held"
