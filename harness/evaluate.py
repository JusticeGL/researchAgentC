"""两级评测。见 IMPLEMENTATION.md §5.3。

evaluate_dev  搜索期随便调，只能碰 dev 划分。
evaluate_test 需要一次性 token，redeem 后 token 作废；调用栈写进 holdout_access.caller。

两者内部都必须：
  - 固定 seed（numpy / torch / random 全设）
  - 调 MOABB evaluation
  - 把结果直接写进 results 库并返回 run_id —— **不返回裸 float**

MOABB/MNE 属重依赖，延迟 import；缺失时调用这些函数才报错。
harness_hash / 预算 / 指纹等不受影响。
"""
from __future__ import annotations

import hashlib
import os
import random
from pathlib import Path
from typing import Dict, Optional

import numpy as np

import harness
from harness import data as hdata
from harness.budget import budget

REPO_ROOT = Path(__file__).resolve().parent.parent
_SOLUTION_DIR = REPO_ROOT / "solution"


# ---------------------------------------------------------------------------
# 指纹 / 环境
# ---------------------------------------------------------------------------
def set_seed(seed: int) -> None:
    os.environ["PYTHONHASHSEED"] = str(seed)
    random.seed(seed)
    np.random.seed(seed)
    try:
        import torch

        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
    except ImportError:
        pass


def solution_code_sha() -> str:
    """solution/ 的内容指纹（无 git 也能算）。"""
    h = hashlib.sha256()
    if _SOLUTION_DIR.exists():
        for p in sorted(_SOLUTION_DIR.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                h.update(p.relative_to(_SOLUTION_DIR).as_posix().encode())
                h.update(p.read_bytes())
    return h.hexdigest()


def env_hash() -> str:
    import sys

    parts = [sys.version]
    for pkg in ("numpy", "scikit-learn", "moabb", "mne"):
        try:
            from importlib.metadata import version

            parts.append(f"{pkg}=={version(pkg)}")
        except Exception:
            parts.append(f"{pkg}==?")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def config_hash(pipelines: Dict) -> str:
    names = sorted(pipelines.keys())
    return hashlib.sha256("|".join(names).encode()).hexdigest()


# ---------------------------------------------------------------------------
# pipelines
# ---------------------------------------------------------------------------
def _flatten(X):
    return np.asarray(X).reshape(len(X), -1)


def make_null_pipeline():
    """只输出常数（多数类）标签的 pipeline，准确率应在 1/n_classes 附近。"""
    from sklearn.dummy import DummyClassifier
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import FunctionTransformer

    return Pipeline([
        ("flatten", FunctionTransformer(_flatten)),
        ("clf", DummyClassifier(strategy="most_frequent")),
    ])


def make_csp_lda(n_components: int = 8):
    """真实 baseline：CSP + LDA（运动想象经典 pipeline）。"""
    from mne.decoding import CSP
    from sklearn.discriminant_analysis import LinearDiscriminantAnalysis
    from sklearn.pipeline import Pipeline

    return Pipeline([
        ("csp", CSP(n_components=n_components)),
        ("lda", LinearDiscriminantAnalysis()),
    ])


def default_pipelines():
    return {"null": make_null_pipeline(), "csp_lda": make_csp_lda()}


# ---------------------------------------------------------------------------
# 评测核心
# ---------------------------------------------------------------------------
def _evaluation_for(split_protocol: str, paradigm, datasets, overwrite=True):
    from moabb.evaluations import (
        CrossSessionEvaluation,
        CrossSubjectEvaluation,
        WithinSessionEvaluation,
    )

    cls = {
        "within_session": WithinSessionEvaluation,
        "cross_session": CrossSessionEvaluation,
        "cross_subject": CrossSubjectEvaluation,
    }[split_protocol]
    return cls(paradigm=paradigm, datasets=datasets, overwrite=overwrite, suffix="ra")


def _run_and_record(pipelines, contract, seed, phase, subjects, results_path,
                    node_id=None) -> Dict[str, str]:
    """跑 MOABB 评测并把每个 pipeline 的结果写进结果库，返回 {pipeline_name: run_id}。"""
    from core import results as results_mod

    set_seed(seed)
    paradigm = hdata.get_paradigm(contract.paradigm)
    dataset = hdata.get_dataset(contract.datasets[0])
    dataset.subject_list = list(subjects)  # 限定被试（dev / test 隔离）
    data_sha = hdata.data_fingerprint(dataset, paradigm)

    ev = _evaluation_for(contract.split_protocol, paradigm, [dataset])
    with budget(contract, node_id=node_id) as usage:
        df = ev.process(pipelines)  # MOABB 返回 DataFrame

    ch = contract.content_hash or contract.content_hash_value()
    hh = harness.harness_hash()
    csha = solution_code_sha()
    cfgh = config_hash(pipelines)
    envh = env_hash()

    out: Dict[str, str] = {}
    for name in pipelines:
        sub = df[df["pipeline"] == name]
        overall = float(sub["score"].mean())
        subj_metrics = {
            str(row["subject"]): {contract.primary_metric: float(row["score"])}
            for _, row in sub.iterrows()
        }
        rid = results_mod.record_run(
            db=results_path,
            metrics={contract.primary_metric: overall},
            subject_metrics=subj_metrics,
            contract_id=contract.contract_id, contract_hash=ch, harness_hash=hh,
            code_sha=csha, config_hash=cfgh, data_sha=data_sha, env_hash=envh,
            seed=seed, split=contract.split_protocol, phase=phase, status="ok",
            wall_clock_s=usage.wall_clock_s, gpu_hours=usage.gpu_hours,
            cost_usd=usage.cost_usd,
        )
        out[name] = rid
    return out


def evaluate_dev(pipelines: Dict, contract, seed: int,
                 results_path: Optional[Path] = None, node_id=None) -> Dict[str, str]:
    """搜索期评测。只能碰 dev 划分。返回 {pipeline_name: run_id}（不返回裸 float）。"""
    dataset = hdata.get_dataset(contract.datasets[0])
    dev_subjects, _ = hdata.dev_test_subjects(dataset)
    return _run_and_record(pipelines, contract, seed, "dev", dev_subjects,
                           results_path, node_id=node_id)


def evaluate_test(pipelines: Dict, contract, seed: int, token: str,
                  results_path: Optional[Path] = None, node_id=None) -> Dict[str, str]:
    """测试集评测。需一次性 token；redeem 后 token 作废（I7）。整个契约生命周期唯一一次。"""
    import inspect

    from core import results as results_mod

    caller = " / ".join(
        f"{fr.function}@{Path(fr.filename).name}:{fr.lineno}"
        for fr in inspect.stack()[1:4]
    )
    results_mod.redeem_test_token(token, caller=f"evaluate_test | {caller}",
                                  db=results_path)
    dataset = hdata.get_dataset(contract.datasets[0])
    _, test_subjects = hdata.dev_test_subjects(dataset)
    return _run_and_record(pipelines, contract, seed, "test", test_subjects,
                           results_path, node_id=node_id)
