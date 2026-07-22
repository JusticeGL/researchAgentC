"""提案去重测试。见 IMPLEMENTATION-P2.md §4.3、§6。

这些单测强制用离线确定性回退（RA_DEDUP_BACKEND=fallback），保证快速可复现；
真实句向量的语义去重见 tests/test_dedup_live.py。
"""
from collections import namedtuple

import pytest

FakeNode = namedtuple("FakeNode", ["node_id", "change_description", "dev_score", "status"])


@pytest.fixture(autouse=True)
def _force_fallback(monkeypatch):
    from loop import dedup

    monkeypatch.setenv("RA_DEDUP_BACKEND", "fallback")
    dedup.reset_backend_cache()
    yield
    dedup.reset_backend_cache()


def _nodes():
    return [
        FakeNode("n_0001", "把 CSP 的分量数从 4 提高到 8", 0.71, "ok"),
        FakeNode("n_0002", "用 Riemann 切空间特征替换 CSP", 0.68, "ok"),
    ]


def test_new_direction_not_flagged():
    from loop import dedup

    r = dedup.check_duplicate("改用图神经网络对通道建模", _nodes(), threshold=0.92)
    assert r.is_duplicate is False
    assert r.hard_reject is False


def test_near_duplicate_gets_result_feedback():
    from loop import dedup

    # 与 n_0001 几乎同义
    r = dedup.check_duplicate("把 CSP 的分量数从 4 提高到 8", _nodes(), threshold=0.92)
    assert r.is_duplicate is True
    assert r.nearest_node_id == "n_0001"
    # 回灌里带上了那次的结果
    assert "n_0001" in r.feedback
    assert "0.71" in r.feedback


def test_repeated_near_duplicate_hard_rejected():
    from loop import dedup

    prop = "把 CSP 的分量数从 4 提高到 8"
    r1 = dedup.check_duplicate(prop, _nodes(), threshold=0.92)
    assert r1.is_duplicate and not r1.hard_reject
    # 第二次又命中同一最近邻 → 硬拒绝
    r2 = dedup.check_duplicate(prop, _nodes(), threshold=0.92,
                               last_hit_node_id=r1.nearest_node_id)
    assert r2.hard_reject is True


def test_embedding_deterministic():
    from loop import dedup

    a = dedup.embed_text("相同文本")
    b = dedup.embed_text("相同文本")
    assert (a == b).all()
