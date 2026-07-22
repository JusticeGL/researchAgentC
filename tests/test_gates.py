"""人工卡点测试。见 IMPLEMENTATION.md §5.7。"""
import pytest

from tests.test_invariants import _minimal_contract


@pytest.fixture
def audit_db(tmp_path):
    from core import gates

    p = tmp_path / "audit.sqlite"
    gates.init_db(p)
    return p


def test_reject_requires_reason(audit_db):
    from core import gates

    with pytest.raises(ValueError):
        gates.record_decision("g1", "contract_review", "c.v1", "question",
                              "reject", reason=None, db=audit_db)
    # approve 不需要理由
    gates.record_decision("g1", "contract_review", "c.v1", "question",
                          "approve", db=audit_db)


def test_review_contract_all_approve(audit_db):
    from core import gates

    c = _minimal_contract()
    ok = gates.review_contract(c, decisions=None, db=audit_db)  # 默认逐字段 approve
    assert ok is True
    hist = gates.get_history(f"{c.contract_id}.v{c.version}", db=audit_db)
    # 每个待审字段一条记录
    assert len(hist) == len(gates.CONTRACT_REVIEW_FIELDS)


def test_review_contract_one_reject_blocks(audit_db):
    from core import gates

    c = _minimal_contract()
    decisions = {"success_threshold": {"decision": "reject", "reason": "阈值太低"}}
    ok = gates.review_contract(c, decisions=decisions, db=audit_db)
    assert ok is False


def test_no_bulk_approve_api():
    """不接受整体批准：不得存在 approve_all / bulk_approve 之类的入口。"""
    from core import gates

    for forbidden in ("approve_all", "bulk_approve", "approve_gate"):
        assert not hasattr(gates, forbidden)
