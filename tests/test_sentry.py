"""作弊探测测试。见 IMPLEMENTATION-P2.md §4.7、§6。"""
from pathlib import Path

import pytest


@pytest.fixture
def sentry_db(tmp_path):
    from loop import sentry

    p = tmp_path / "sentry.sqlite"
    sentry.init_db(p)
    return p


def test_bare_except_around_eval_detected():
    from loop import sentry

    code = (
        "def run():\n"
        "    try:\n"
        "        r = evaluate_dev(pipe, contract, seed)\n"
        "    except Exception:\n"
        "        pass\n"
    )
    events = sentry.scan_code(code)
    assert any(e.rule == "S3" for e in events)


def test_bare_except_naked_around_eval_detected():
    from loop import sentry

    code = (
        "def run():\n"
        "    try:\n"
        "        evaluate_test(pipe, contract, seed, token)\n"
        "    except:\n"
        "        x = 1\n"
    )
    events = sentry.scan_code(code)
    assert any(e.rule == "S3" for e in events)


def test_hardcoded_metric_return_detected():
    from loop import sentry

    code = "def get_score():\n    return 0.99\n"
    events = sentry.scan_code(code)
    assert any(e.rule == "S4" for e in events)


def test_normal_function_not_flagged_s4():
    from loop import sentry

    code = "def compute_score(x):\n    return sum(x) / len(x)\n"
    events = sentry.scan_code(code)
    assert not any(e.rule == "S4" for e in events)


def test_test_split_identifier_in_dev_detected():
    from loop import sentry

    code = "def train():\n    acc = model.score(X_test, y_test)\n"
    events = sentry.scan_code(code)
    assert any(e.rule == "S5" for e in events)


def test_importlib_harness_detected():
    from loop import sentry

    code = "import importlib\nm = importlib.import_module('harness.evaluate')\n"
    events = sentry.scan_code(code)
    assert any(e.rule == "S6" for e in events)


def test_harness_hash_mismatch_detected():
    from loop import sentry

    assert sentry.check_harness_hash("a" * 64, "a" * 64) == []
    events = sentry.check_harness_hash("a" * 64, "b" * 64)
    assert any(e.rule == "S8" for e in events)


def test_scan_patch_extracts_added_lines():
    from loop import sentry

    patch = (
        "--- a/solution/m.py\n"
        "+++ b/solution/m.py\n"
        "@@ -1,2 +1,4 @@\n"
        " def train():\n"
        "+    import socket\n"
        "+    return 1.0\n"
    )
    events = sentry.scan_patch(patch)
    assert any(e.rule == "S7" for e in events)


def test_record_events_persists(sentry_db):
    import sqlite3

    from loop import sentry

    events = sentry.scan_code("def score():\n    return 1.0\n")
    sentry.record_events(sentry_db, "n1", events)
    con = sqlite3.connect(sentry_db)
    n = con.execute("SELECT COUNT(*) FROM sentry_events WHERE node_id='n1'").fetchone()[0]
    con.close()
    assert n >= 1


def test_sentry_events_append_only(sentry_db):
    import sqlite3

    from loop import sentry

    sentry.record_events(sentry_db, "n1", [sentry.SentryEvent("S4", "invalidate", "x")])
    con = sqlite3.connect(sentry_db)
    with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError)):
        con.execute("DELETE FROM sentry_events")
        con.commit()
    con.close()
