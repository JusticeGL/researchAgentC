"""图表确定性测试。见 IMPLEMENTATION-P3.md §4.4、§6 Phase 14。"""
from pathlib import Path

import pytest

pytest.importorskip("matplotlib")


def test_figure_rerun_byte_identical(tmp_path):
    """同一张图重跑两次，sha256 必须一致（I19）。"""
    from figures import _lib

    fig_dir = _lib.figure_dirs()[0]
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    _lib.render_to(fig_dir, a)
    _lib.render_to(fig_dir, b)
    assert _lib.sha256_file(a) == _lib.sha256_file(b)


def test_build_then_check_all_pass():
    from figures import _lib

    _lib.build_all()
    ok, details = _lib.check_all()
    assert ok, details


def test_at_least_two_figures():
    from figures import _lib

    assert len(_lib.figure_dirs()) >= 2


def test_figure_manifest_run_ids_exist(db_path, base_run_fields):
    """manifest.run_ids 里的 run_id 必须真的在结果库里。"""
    from core import results
    from figures import _lib

    rid = results.record_run(db=db_path, metrics={"acc": 0.7},
                             **base_run_fields(phase="dev"))
    fig_dir = _lib.figure_dirs()[0]
    # 存在的 run_id → 无缺失
    import json

    man = json.loads((fig_dir / "manifest.json").read_text(encoding="utf-8"))
    man_backup = dict(man)
    man["run_ids"] = [rid]
    (fig_dir / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
    try:
        assert _lib.missing_run_ids(fig_dir, db_path) == []
        # 不存在的 run_id → 报缺失
        man["run_ids"] = ["nonexistent_run"]
        (fig_dir / "manifest.json").write_text(
            json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
        assert _lib.missing_run_ids(fig_dir, db_path) == ["nonexistent_run"]
    finally:
        (fig_dir / "manifest.json").write_text(
            json.dumps(man_backup, ensure_ascii=False, indent=2), encoding="utf-8")
