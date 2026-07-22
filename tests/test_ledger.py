"""经验台账测试。见 IMPLEMENTATION-P2.md §4.4、§6。"""
import pytest


@pytest.fixture
def ledger_db(tmp_path):
    from loop import ledger

    p = tmp_path / "ledger.sqlite"
    ledger.init_db(p)
    return p


def test_add_and_active(ledger_db):
    from loop import ledger

    ledger.add_lesson(ledger_db, "c_test", "CSP 分量 >8 无益", "deadend", ["r1"])
    active = ledger.active_lessons(ledger_db, "c_test")
    assert len(active) == 1
    assert active[0].evidence == ["r1"]


def test_invalid_kind_rejected(ledger_db):
    from loop import ledger

    with pytest.raises(ValueError):
        ledger.add_lesson(ledger_db, "c_test", "x", "not_a_kind", ["r1"])


def test_compact_preserves_evidence_union(ledger_db):
    from loop import ledger

    # 造 45 条 > 上限 40
    for i in range(45):
        ledger.add_lesson(ledger_db, "c_test", f"lesson {i}", "insight", [f"r{i}"])
    changed = ledger.compact(ledger_db, "c_test", max_active=40)
    assert changed is True

    active = ledger.active_lessons(ledger_db, "c_test")
    assert len(active) <= 40
    union = set()
    for l in active:
        union |= set(l.evidence)
    # 45 条证据 run_id 全部保留
    assert {f"r{i}" for i in range(45)}.issubset(union)


def test_compact_rejects_evidence_loss(ledger_db):
    from loop import ledger

    for i in range(45):
        ledger.add_lesson(ledger_db, "c_test", f"lesson {i}", "insight", [f"r{i}"])

    def lossy_compactor(lessons):
        # 故意丢掉证据
        return [{"text": "merged", "kind": "insight", "evidence": ["r0"]}]

    with pytest.raises(ledger.LedgerCompactionError):
        ledger.compact(ledger_db, "c_test", max_active=40, compactor=lossy_compactor)


def test_compact_noop_under_limit(ledger_db):
    from loop import ledger

    for i in range(5):
        ledger.add_lesson(ledger_db, "c_test", f"l{i}", "insight", [f"r{i}"])
    assert ledger.compact(ledger_db, "c_test", max_active=40) is False


def test_superseded_lessons_not_deleted(ledger_db):
    import sqlite3

    from loop import ledger

    for i in range(45):
        ledger.add_lesson(ledger_db, "c_test", f"l{i}", "insight", [f"r{i}"])
    ledger.compact(ledger_db, "c_test", max_active=40)
    con = sqlite3.connect(ledger_db)
    total = con.execute("SELECT COUNT(*) FROM lessons").fetchone()[0]
    superseded = con.execute(
        "SELECT COUNT(*) FROM lessons WHERE superseded_by IS NOT NULL").fetchone()[0]
    con.close()
    assert superseded == 45          # 旧条目仍在
    assert total > 45                # 新增了合并条目


def test_only_active_lessons_enter_context(ledger_db):
    """active_lessons 只返回未被 supersede 的条目 —— 保证进 context 的都是 active。"""
    from loop import ledger

    for i in range(45):
        ledger.add_lesson(ledger_db, "c_test", f"l{i}", "insight", [f"r{i}"])
    ledger.compact(ledger_db, "c_test", max_active=40)
    active = ledger.active_lessons(ledger_db, "c_test")
    assert all(l.superseded_by is None for l in active)
    assert len(active) <= 40
