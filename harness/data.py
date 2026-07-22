"""MOABB 数据包装。见 IMPLEMENTATION.md §5.3。

MOABB 的数据集类名在不同版本间变过（BNCI2014001 vs BNCI2014_001），
且不同版本 paradigm API 略有差异。**以实际安装的包为准**：
本模块对类名做多别名尝试，并在拿不到时给出清晰报错，而不是照抄某个版本。

moabb / mne 属重依赖，缺失时本模块的函数在被调用时才报错（延迟 import），
因此 `import harness.data` 本身不需要 moabb（harness_hash 等仍可用）。

建议起步数据集：BNCI2014_001（BCI IV-2a，运动想象，4 类，chance=0.25，被试 9 名）。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

# 数据集名 → 可能的 MOABB 类名别名（新旧版本）
_DATASET_ALIASES = {
    "BNCI2014_001": ["BNCI2014_001", "BNCI2014001"],
    "BNCI2014001": ["BNCI2014_001", "BNCI2014001"],
    "BNCI2014_004": ["BNCI2014_004", "BNCI2014004"],
    "BNCI2014004": ["BNCI2014_004", "BNCI2014004"],
}


def get_paradigm(name: str) -> Any:
    """name ∈ {motor_imagery, p300, ssvep}。返回 MOABB paradigm 实例。"""
    import moabb.paradigms as mp

    key = name.lower()
    if key in ("motor_imagery", "motorimagery", "mi"):
        # 4 类运动想象用 MotorImagery；若只想左右手用 LeftRightImagery
        if hasattr(mp, "MotorImagery"):
            return mp.MotorImagery()
        return mp.LeftRightImagery()
    if key == "p300":
        return mp.P300()
    if key == "ssvep":
        return mp.SSVEP()
    raise ValueError(f"未知 paradigm：{name}")


def get_dataset(name: str) -> Any:
    """按名解析 MOABB 数据集类（多别名尝试），返回实例。"""
    import moabb.datasets as md

    candidates = _DATASET_ALIASES.get(name, [name])
    for cls_name in candidates:
        cls = getattr(md, cls_name, None)
        if cls is not None:
            return cls()
    raise ValueError(
        f"MOABB 中找不到数据集 {name}（尝试过 {candidates}）。"
        f"请 `python -c 'import moabb.datasets as d; print(dir(d))'` 确认实际类名。"
    )


def n_classes(dataset, paradigm) -> int:
    """从 dataset/paradigm 读类别数，用于 chance level（不要写死 0.5）。"""
    # MOABB 数据集通常有 event_id / events 描述
    ev = getattr(dataset, "event_id", None)
    if ev:
        return len(ev)
    events = getattr(paradigm, "events", None)
    if events:
        return len(events)
    raise ValueError("无法从 dataset/paradigm 推断类别数")


def data_fingerprint(dataset, paradigm) -> str:
    """稳定指纹，进 runs.data_sha。由数据集 code、被试列表、paradigm 名、事件构成。"""
    payload = {
        "dataset": getattr(dataset, "code", dataset.__class__.__name__),
        "subjects": list(getattr(dataset, "subject_list", []) or []),
        "paradigm": paradigm.__class__.__name__,
        "events": sorted(list(getattr(dataset, "event_id", {}) or {})),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def dev_test_subjects(dataset, test_fraction: float = 0.4):
    """把被试确定性地切成 dev / test 两组（按 subject id 排序后切分）。

    这是把"两级评测"落到数据上的最简方式：test 组的被试在搜索期永不可见。
    """
    subs = sorted(list(getattr(dataset, "subject_list", []) or []))
    if not subs:
        raise ValueError("数据集没有 subject_list")
    n_test = max(1, int(round(len(subs) * test_fraction)))
    test = subs[-n_test:]
    dev = subs[:-n_test] or subs[:1]
    return dev, test
