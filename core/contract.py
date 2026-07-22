"""契约。见 IMPLEMENTATION.md §5.4。

不变式 I8：契约冻结后不可变。冻结时算 content_hash，任何修改必须新建版本，旧版本保留。

生命周期（打破循环依赖，见 §5.4 说明）：
  1. 填好除 reproduced_run_ids 外的全部字段
  2. content_hash() 得到最终 hash（此后不再变；hash 排除 reproduced_run_ids）
  3. 用该 hash 作为 contract_hash 跑 baseline 复现，record_run
  4. 把 run_id 回填进 baselines[*].reproduced_run_ids —— 不改变 hash
  5. freeze() 校验非空并落盘

为兼容本机 Python 3.9，字段用 typing.Optional/List 而非 PEP604 的 `X | None`。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class BaselineSpec(BaseModel):
    """baseline 数字必须是自己跑出来的，不是抄论文的（reproduced_run_ids）。"""

    model_config = ConfigDict(extra="forbid")  # 可变：允许回填 reproduced_run_ids

    name: str
    cite_key: str                        # 指向 corpus 的引用 key
    reproduced_run_ids: List[str] = Field(default_factory=list)


class StatPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    n_seeds: int
    test: str                            # 如 paired_t
    correction: str                      # 如 none|bonferroni|holm
    min_effect_size: float


class Budget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    gpu_hours: float
    usd: float
    wall_clock_h: float
    per_node_gpu_hours: float


class AblationSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    description: str
    falsifies: str                       # 每条必须写明"它能证伪什么"


class Contract(BaseModel):
    """冻结的科研契约。始终 frozen=True：所有"修改"走 freeze / new_version 产生新实例。

    reproduced_run_ids 的回填靠直接改 baselines[i]（BaselineSpec 可变），
    不需要修改 Contract 本身，故 Contract 永久不可变不影响生命周期。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_id: str
    version: int
    parent_version: Optional[int]

    question: str
    hypothesis: str

    datasets: List[str]
    split_protocol: Literal["within_session", "cross_session", "cross_subject"]
    paradigm: str

    baselines: List[BaselineSpec]
    primary_metric: str
    success_threshold: float
    direction: Literal["maximize", "minimize"]

    stat_plan: StatPlan
    budget: Budget
    kill_criteria: List[str]
    preregistered_ablations: List[AblationSpec]
    novelty_evidence: List[str]
    novelty_note: str

    frozen_at: Optional[str] = None
    content_hash: Optional[str] = None

    # ------------------------------------------------------------------
    def _canonical_payload(self) -> dict:
        """除 frozen_at / content_hash / baselines[*].reproduced_run_ids 外的全部字段。"""
        data = self.model_dump()
        data.pop("frozen_at", None)
        data.pop("content_hash", None)
        for b in data.get("baselines", []):
            b.pop("reproduced_run_ids", None)
        return data

    def content_hash_value(self) -> str:
        """规范化 JSON 的 SHA256。不依赖 frozen 状态，冻结前即可调用。

        （方法名与字段 content_hash 区分开，避免遮蔽。）
        """
        payload = self._canonical_payload()
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def freeze(self, out_dir: Optional[Path] = None, write: bool = True) -> "Contract":
        """校验 baseline 已复现 → 写 content_hash + frozen_at → 落盘 → 返回新的冻结实例。"""
        for b in self.baselines:
            if not b.reproduced_run_ids:
                raise ValueError(
                    f"baseline {b.name!r} 的 reproduced_run_ids 为空，"
                    f"freeze 拒绝：baseline 数字必须自己跑出来（§5.4）"
                )
        h = self.content_hash_value()
        from datetime import datetime, timezone

        frozen = self.model_copy(
            update={"content_hash": h, "frozen_at": datetime.now(timezone.utc).isoformat()}
        )
        if write:
            out_dir = Path(out_dir) if out_dir else (
                Path(__file__).resolve().parent.parent / "contracts"
            )
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"{frozen.contract_id}.v{frozen.version}.json"
            path.write_text(
                json.dumps(frozen.model_dump(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return frozen

    def new_version(self, **changes) -> "Contract":
        """基于当前版本开新版本。旧文件永不删除。新版本的 hash / frozen 重置。"""
        update = dict(changes)
        update.setdefault("version", self.version + 1)
        update["parent_version"] = self.version
        update["frozen_at"] = None
        update["content_hash"] = None
        return self.model_copy(update=update)

    @classmethod
    def load(cls, path: Path) -> "Contract":
        return cls(**json.loads(Path(path).read_text(encoding="utf-8")))
