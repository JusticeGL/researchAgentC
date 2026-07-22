"""渲染器测试。见 IMPLEMENTATION.md §5.2。"""
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RENDER_CFG = REPO_ROOT / "config" / "render.yaml"


@pytest.fixture
def corpus_db(tmp_path):
    from core import corpus

    p = tmp_path / "corpus.sqlite"
    corpus.init_db(p)
    return p


def test_render_run_template(tmp_path, db_path, base_run_fields):
    from core import render, results

    rid = results.record_run(metrics={"test_acc": 0.812}, **base_run_fields())
    src = tmp_path / "p.md"
    src.write_text(f"## Results\nAccuracy was {{{{run:{rid}.test_acc}}}} overall.\n",
                   encoding="utf-8")
    prov = render.render_file(src, tmp_path / "build", results_path=db_path,
                              config_path=RENDER_CFG)
    out = (tmp_path / "build" / "p.md").read_text(encoding="utf-8")
    assert "0.812" in out
    assert prov["replacements"][0]["run_ids"] == [rid]
    assert "span" in prov["replacements"][0]


def test_render_agg_span_excludes_rendered_numbers(tmp_path, db_path, base_run_fields):
    """核心测试：agg 渲染出的 '0.7 ± 0.1' 落在 Results 章节，
    但因在替换 span 内，裸数字扫描必须放行（否则合法论文全被拒）。"""
    from core import render, results

    rids = [
        results.record_run(metrics={"acc": v}, **base_run_fields(seed=i))
        for i, v in enumerate([0.6, 0.7, 0.8])
    ]
    src = tmp_path / "p.md"
    src.write_text("## Results\nOur method reached {{agg:main.acc}} across seeds.\n",
                   encoding="utf-8")
    prov = render.render_file(src, tmp_path / "build", results_path=db_path,
                              config_path=RENDER_CFG, tags={"main": rids})
    out = (tmp_path / "build" / "p.md").read_text(encoding="utf-8")
    assert "±" in out
    agg_rec = prov["replacements"][0]
    assert agg_rec["kind"] == "agg"
    assert agg_rec["n"] == 3


def test_render_lit_requires_cite(tmp_path, corpus_db):
    from core import corpus, render

    corpus.add_paper(corpus_db, key="smith2019", title="T", authors="Smith", year=2019,
                     doi="10.1/z")
    src = tmp_path / "p.md"
    src.write_text("## Results\nPrior work reported {{lit:0.72|cite=smith2019}} here.\n",
                   encoding="utf-8")
    render.render_file(src, tmp_path / "build", corpus_path=corpus_db,
                       config_path=RENDER_CFG)
    out = (tmp_path / "build" / "p.md").read_text(encoding="utf-8")
    assert "0.72" in out


def test_render_bare_number_outside_span_rejected(tmp_path, db_path):
    from core import render

    src = tmp_path / "p.md"
    src.write_text("## Results\nWe hit 0.99 accuracy.\n", encoding="utf-8")
    with pytest.raises(render.BareNumberError):
        render.render_file(src, tmp_path / "build", results_path=db_path,
                           config_path=RENDER_CFG)


def test_render_bare_number_allowed_outside_scan_section(tmp_path):
    """非扫描章节（如 Introduction）里的裸数字不触发。"""
    from core import render

    src = tmp_path / "p.md"
    src.write_text("## Introduction\nBack in 1999 things were 0.5 different.\n",
                   encoding="utf-8")
    # 不应抛异常
    render.render_file(src, tmp_path / "build", config_path=RENDER_CFG)


def test_render_whitelist_year_allowed(tmp_path):
    from core import render

    src = tmp_path / "p.md"
    src.write_text("## Results\nData collected in 2018 was used.\n", encoding="utf-8")
    # 2018 命中年份白名单，不触发
    render.render_file(src, tmp_path / "build", config_path=RENDER_CFG)


def test_render_unknown_cite_rejected(tmp_path, corpus_db):
    from core import render

    src = tmp_path / "p.md"
    src.write_text("## Results\nAs in \\cite{ghost}.\n", encoding="utf-8")
    with pytest.raises(render.UnknownCitationError):
        render.render_file(src, tmp_path / "build", corpus_path=corpus_db,
                           config_path=RENDER_CFG)
