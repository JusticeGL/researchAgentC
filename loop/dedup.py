"""提案去重闸门。见 IMPLEMENTATION-P2.md §4.3。

用**本地 embedding**（便宜且确定），**不调 LLM 做去重判断**。
命中时不直接拒绝，而是把最近邻的**结果**回灌给 agent；
连续两次命中同一个最近邻 → 硬拒绝，该分支标 abandoned。

embedding 优先用 sentence-transformers（all-MiniLM-L6-v2 量级）；
不可用时退回确定性的 char n-gram 哈希向量（离线、可复现，几百节点毫秒级）。
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import List, Optional, Sequence

import numpy as np

_ST_MODEL = None
_ST_TRIED = False
_DIM = 512
_MODEL_NAME = os.environ.get("RA_DEDUP_MODEL", "all-MiniLM-L6-v2")


@dataclass
class DedupResult:
    is_duplicate: bool
    similarity: float
    nearest_node_id: Optional[str]
    nearest_description: Optional[str]
    hard_reject: bool
    feedback: str


def _backend() -> str:
    """auto（默认，有 ST 就用）| st（强制真实句向量）| fallback（强制离线哈希）。"""
    return os.environ.get("RA_DEDUP_BACKEND", "auto").lower()


def _set_hf_offline(flag: bool) -> None:
    """切换 HF 离线模式。直接改 huggingface_hub 的模块常量 —— 它在 import 时就把
    HF_HUB_OFFLINE 读进常量，import 之后再设环境变量无效，故必须直接改常量。"""
    val = "1" if flag else "0"
    os.environ["HF_HUB_OFFLINE"] = val
    os.environ["TRANSFORMERS_OFFLINE"] = val
    for modname in ("huggingface_hub.constants", "transformers.utils.hub"):
        try:
            import importlib

            mod = importlib.import_module(modname)
            if hasattr(mod, "HF_HUB_OFFLINE"):
                mod.HF_HUB_OFFLINE = flag
        except Exception:
            pass


def _try_load_st():
    global _ST_MODEL, _ST_TRIED
    if _backend() == "fallback":
        return None
    if _ST_TRIED:
        return _ST_MODEL
    _ST_TRIED = True
    # transformers 默认会探测并导入 TensorFlow；本机那份旧 TF 与 numpy 2.0 不兼容会崩，
    # 显式只用 torch 后端。
    os.environ.setdefault("USE_TF", "0")
    os.environ.setdefault("USE_FLAX", "0")
    try:
        from sentence_transformers import SentenceTransformer

        # 先走本地 HF 缓存（离线，秒级，不触发受限网络下漫长的重试回退）。
        _set_hf_offline(True)
        try:
            _ST_MODEL = SentenceTransformer(_MODEL_NAME)
        except Exception:
            # 冷启动：缓存里没有 → 允许联网下载一次。
            _set_hf_offline(False)
            _ST_MODEL = SentenceTransformer(_MODEL_NAME)
    except Exception:
        _ST_MODEL = None
    return _ST_MODEL


def reset_backend_cache() -> None:
    """测试用：清掉已缓存的模型探测结果，便于切换后端。"""
    global _ST_MODEL, _ST_TRIED
    _ST_MODEL = None
    _ST_TRIED = False


def _fallback_embed(text: str) -> np.ndarray:
    """确定性 char 3-gram 哈希向量，L2 归一化。"""
    v = np.zeros(_DIM, dtype=np.float64)
    t = text.lower().strip()
    if not t:
        return v
    padded = f"  {t}  "
    for i in range(len(padded) - 2):
        gram = padded[i:i + 3]
        h = int(hashlib.md5(gram.encode("utf-8")).hexdigest(), 16)
        v[h % _DIM] += 1.0
    norm = np.linalg.norm(v)
    return v / norm if norm > 0 else v


def embed_text(text: str) -> np.ndarray:
    model = _try_load_st()
    if model is not None:
        vec = np.asarray(model.encode([text])[0], dtype=np.float64)
        norm = np.linalg.norm(vec)
        return vec / norm if norm > 0 else vec
    return _fallback_embed(text)


def using_sentence_transformers() -> bool:
    return _try_load_st() is not None


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape:
        return 0.0
    return float(np.clip(np.dot(a, b), -1.0, 1.0))


def check_duplicate(proposal: str, existing: Sequence, threshold: float = 0.92,
                    last_hit_node_id: Optional[str] = None) -> DedupResult:
    """existing: 一组带 .node_id / .change_description / .dev_score / .status 的节点。

    返回 DedupResult。命中阈值即 is_duplicate=True，并附带最近邻结果的回灌文本；
    若这次的最近邻与上次命中相同（last_hit_node_id）→ hard_reject=True。
    """
    if not existing:
        return DedupResult(False, 0.0, None, None, False,
                           "无历史节点，直接执行。")
    pv = embed_text(proposal)
    best_sim = -1.0
    best = None
    for node in existing:
        sim = _cosine(pv, embed_text(node.change_description))
        if sim > best_sim:
            best_sim, best = sim, node

    is_dup = best_sim >= threshold
    hard_reject = bool(is_dup and last_hit_node_id is not None
                       and last_hit_node_id == best.node_id)
    if not is_dup:
        feedback = (f"与最近邻 {best.node_id} 相似度 {best_sim:.2f}（< {threshold}），"
                    f"视为新方向，可执行。")
    else:
        score_repr = "n/a" if best.dev_score is None else f"{best.dev_score:.4f}"
        feedback = (
            f"你提的改动与节点 {best.node_id} 相似度 {best_sim:.2f}。"
            f"那次的改动是「{best.change_description}」，"
            f"结果是 dev_score={score_repr}（{best.status}）。"
            f"要么换个方向，要么明确说明这次为什么会不同。"
        )
        if hard_reject:
            feedback += "（连续第二次命中同一最近邻 → 硬拒绝，该分支标 abandoned。）"
    return DedupResult(is_dup, best_sim, best.node_id, best.change_description,
                       hard_reject, feedback)
