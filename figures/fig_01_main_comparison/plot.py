"""主对比图。见 IMPLEMENTATION-P3.md §4.4。

数字来自结果库导出的 data.json（run_ids 记在 manifest.json）。
本脚本确定性：给定 data.json，重跑 byte-identical。
"""
from __future__ import annotations

import json
from pathlib import Path

from figures._lib import deterministic_setup

HERE = Path(__file__).resolve().parent


def render(out_path: Path) -> None:
    deterministic_setup()
    import matplotlib.pyplot as plt

    data = json.loads((HERE / "data.json").read_text(encoding="utf-8"))
    fig, ax = plt.subplots()
    x = list(range(len(data["groups"])))
    ax.bar(x, data["means"], yerr=data["stds"], capsize=5,
           color=["#9aa0a6", "#1a73e8"], width=0.6)
    ax.set_xticks(x)
    ax.set_xticklabels(data["groups"])
    ax.set_ylabel(data["ylabel"])
    ax.set_title(data["title"])
    ax.set_ylim(0.0, 1.0)
    fig.tight_layout()
    fig.savefig(out_path, format="pdf", metadata={"CreationDate": None})
    plt.close(fig)
