"""边界强制测试。见 IMPLEMENTATION-P2.md §4.1、§6。"""
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_boundary_hashes_cover_protected_dirs():
    from loop import sentry

    h = sentry.boundary_hashes(REPO_ROOT)
    assert any(k.startswith("harness/") for k in h)
    assert any(k.startswith("core/") for k in h)
    # 稳定：再算一次相等
    assert sentry.boundary_hashes(REPO_ROOT) == h


def test_agent_cannot_write_harness():
    from loop import sentry

    before = {"harness/evaluate.py": "aaa", "core/results.py": "bbb"}
    after = dict(before)
    after["harness/evaluate.py"] = "HACKED"
    with pytest.raises(sentry.BoundaryViolation):
        sentry.assert_boundary_intact(before, after)


def test_boundary_intact_when_unchanged():
    from loop import sentry

    h = {"harness/x.py": "a", "core/y.py": "b"}
    sentry.assert_boundary_intact(h, dict(h))  # 不抛


def test_agent_cannot_write_outside_solution():
    from loop import sentry

    written = [
        str(REPO_ROOT / "solution" / "model.py"),          # 合法
        str(REPO_ROOT / "artifacts" / "n1" / "out.json"),  # 合法
        str(REPO_ROOT / "harness" / "evaluate.py"),        # S1 halt
        str(REPO_ROOT / "core" / "results.py"),            # S1 halt
        "/tmp/somewhere_else.txt",                          # S2 halt
    ]
    events = sentry.check_written_paths(written, node_id="n1", repo_root=REPO_ROOT)
    rules = {e.rule for e in events}
    assert "S1" in rules
    assert "S2" in rules
    assert sentry.has_halt(events)


def test_boundary_violation_halts_loop():
    """故意"写" harness 的场景：assert_boundary_intact 报警且是 halt 级。"""
    from loop import sentry

    before = sentry.boundary_hashes(REPO_ROOT)
    after = dict(before)
    # 模拟某个 harness 文件被改
    key = next(k for k in after if k.startswith("harness/"))
    after[key] = "tampered"
    with pytest.raises(sentry.BoundaryViolation):
        sentry.assert_boundary_intact(before, after)


def test_network_disabled_during_training():
    from loop import sentry

    # (a) 沙箱脚本默认断网
    run_node = (REPO_ROOT / "sandbox" / "run_node.sh").read_text(encoding="utf-8")
    assert "--network=none" in run_node

    # (b) sentry S7 抓训练脚本里的联网模块
    code = "import requests\ndef train():\n    requests.get('http://x')\n"
    events = sentry.scan_code(code)
    assert any(e.rule == "S7" for e in events)
