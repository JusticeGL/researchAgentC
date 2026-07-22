"""真实句向量（sentence-transformers）去重的 live 测试。见 IMPLEMENTATION-P2.md §4.3。

需要装了 sentence-transformers 且能加载 all-MiniLM-L6-v2（首次需联网下载，之后走本地缓存）。
不可用时整文件 skip —— 与 MOABB live 用例一致的处理方式。

价值点：真实句向量能识别**语义等价但字面不同**的改写（同义句），
这是离线 char-3gram 哈希回退做不到的。
"""
from collections import namedtuple

import pytest

FakeNode = namedtuple("FakeNode", ["node_id", "change_description", "dev_score", "status"])


@pytest.fixture(autouse=True)
def _force_st(monkeypatch):
    monkeypatch.setenv("RA_DEDUP_BACKEND", "st")
    monkeypatch.setenv("USE_TF", "0")
    monkeypatch.setenv("USE_FLAX", "0")
    from loop import dedup

    dedup.reset_backend_cache()
    if not dedup.using_sentence_transformers():
        pytest.skip("sentence-transformers / all-MiniLM-L6-v2 不可用（需安装+联网下载）")
    yield
    dedup.reset_backend_cache()


def test_st_backend_active():
    from loop import dedup

    assert dedup.using_sentence_transformers() is True
    v = dedup.embed_text("hello world")
    assert v.shape[0] == 384          # MiniLM 维度


def test_semantic_paraphrase_detected():
    """同义改写（字面不同）也应被判为近重复 —— 真实句向量的核心能力。"""
    from loop import dedup

    nodes = [FakeNode("n_0001", "Increase the number of CSP components from 4 to 8",
                      0.71, "ok")]
    # 语义等价但用词不同的改写
    r = dedup.check_duplicate("Raise CSP component count from four to eight",
                              nodes, threshold=0.6)
    assert r.nearest_node_id == "n_0001"
    assert r.similarity >= 0.6
    assert "n_0001" in r.feedback


def test_distinct_direction_low_similarity():
    from loop import dedup

    nodes = [FakeNode("n_0001", "Increase the number of CSP components from 4 to 8",
                      0.71, "ok")]
    r = dedup.check_duplicate("Switch the optimizer to a cosine learning-rate schedule",
                              nodes, threshold=0.6)
    assert r.similarity < 0.6
    assert r.is_duplicate is False
