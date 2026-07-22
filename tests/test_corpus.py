"""文献库测试。见 IMPLEMENTATION.md §5.5。"""
import sqlite3

import pytest


@pytest.fixture
def corpus_db(tmp_path):
    from core import corpus

    p = tmp_path / "corpus.sqlite"
    corpus.init_db(p)
    return p


def test_add_paper_with_doi(corpus_db):
    from core import corpus

    corpus.add_paper(corpus_db, key="lawhern2018eegnet",
                     title="EEGNet", authors="Lawhern, V.", year=2018,
                     doi="10.1088/1741-2552/aace8c")
    assert corpus.exists(corpus_db, "lawhern2018eegnet")
    assert corpus.has_resolved_id(corpus_db, "lawhern2018eegnet")


def test_add_paper_with_arxiv(corpus_db):
    from core import corpus

    corpus.add_paper(corpus_db, key="foo2020", title="Foo", authors="A", year=2020,
                     arxiv_id="2001.01234")
    assert corpus.has_resolved_id(corpus_db, "foo2020")


def test_reject_without_id(corpus_db):
    from core import corpus

    with pytest.raises(sqlite3.IntegrityError):
        corpus.add_paper(corpus_db, key="noid", title="No", authors="A", year=2020)


def test_bibtex(corpus_db):
    from core import corpus

    corpus.add_paper(corpus_db, key="k1", title="T", authors="A, B", year=2019,
                     doi="10.1/x", venue="J")
    bib = corpus.bibtex(corpus_db, ["k1"])
    assert "@article{k1" in bib
    assert "doi = {10.1/x}" in bib


def test_support_check_unverifiable_for_closed(corpus_db):
    from core import corpus

    corpus.add_paper(corpus_db, key="paywalled", title="T", authors="A", year=2019,
                     doi="10.1/y", oa_status="closed")
    v = corpus.support_check(corpus_db, "该方法优于基线", "paywalled")
    assert v.verdict == "unverifiable"


def test_has_resolved_id_missing_key(corpus_db):
    from core import corpus

    assert corpus.has_resolved_id(corpus_db, "ghost") is False
