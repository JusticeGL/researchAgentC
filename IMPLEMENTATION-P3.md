# 科研 agent 第三期 — 消融、写作与交付

> 前置：第一期（底座）、第二期（实验循环）完成，`make check` 在一次真实搜索后仍全绿。
> 本期把实验结果变成一份**可投稿、可复现、且无法 HARKing** 的论文。
> 前两期的 I1–I15、禁止事项、`CLAUDE.md` 全部继续生效。

---

## 0. 一句话目标

让系统能写论文，但**写不出契约里没有的结论**。

具体地：

1. 消融只能来自契约里预注册的那些，临时新增必须经人工卡点并留痕
2. 论文里每一个强论断都必须能追溯到契约的某个字段或某个预注册消融——**追溯不到就在技术上写不出来**
3. 假设不成立时，系统能正常产出一篇负面结果论文，而不是卡死或粉饰
4. 每张图可以一键重跑，输出 byte-identical
5. 交付物是一个 `make reproduce` 能跑出全部数字的仓库，外加一份自动生成的 AI 参与度披露

---

## 1. 新增不变式（累加在 I1–I15 之上）

| # | 不变式 | 强制手段 |
|---|---|---|
| I16 | 强论断必须注册且必须有 source | claim registry + checker 硬失败 |
| I17 | claim 的 source 只能是契约字段、预注册消融、或经卡点批准的新 claim | registry 校验，无第四种 |
| I18 | 负面结果必须可写且走完整流程 | 三套模板 + 端到端测试 |
| I19 | 每张图可重跑且 byte-identical | manifest + hash 比对 |
| I20 | 消融必须预注册或经批准 | `ablation_id` 外键到契约或 audit |
| I21 | `unverifiable` 引用不得被静默放行 | checker 单列报告，需人工签字 |

**I16 + I17 是本期的核心。** 它们把 HARKing 从"不该做的事"变成"做不到的事"。

---

## 2. 明确不要做的事（累加）

- ❌ **不要**给写作 agent 一个叫"找出本文的主要创新点"的任务。它的任务是**报告契约里的假设是否成立**
- ❌ **不要**让 LLM 生成 Abstract 里的第一个结论句。那句话由模板 + `hypothesis_held` 确定性渲染
- ❌ **不要**在看到结果之后才决定做哪些消融。要新增，走卡点
- ❌ **不要**用 LLM 画图。图由确定性脚本从结果库生成
- ❌ **不要**把 `unverifiable` 的引用当作 `supported` 处理，也不要因为验证不了就删掉那句话——报告给人，让人决定
- ❌ **不要**在本期做选题和 LLM 审稿

---

## 3. 目录新增

```
research-agent/
├── loop/
│   └── ablation.py          # 消融执行
├── writing/
│   ├── __init__.py
│   ├── claims.py            # claim registry + 校验
│   ├── compose.py           # 写作 agent 编排
│   ├── negative.py          # 负面结果路径
│   └── disclosure.py        # AI_CONTRIBUTION.md 生成
├── figures/
│   ├── _lib.py              # 统一样式 + 确定性设置
│   └── fig_XX_<name>/
│       ├── plot.py
│       ├── manifest.json
│       └── out.pdf
├── paper/
│   ├── claims.yaml          # claim registry
│   ├── templates/
│   │   ├── positive.md
│   │   ├── negative.md
│   │   └── inconclusive.md
│   └── src/                 # agent 填充的正文
├── schema/
│   └── claims.sql
└── tests/
    ├── test_claims.py
    ├── test_negative_path.py
    ├── test_figures.py
    └── test_ablation.py
```

---

## 4. 模块规格

### 4.1 `writing/claims.py` — Claim Registry（先做这个）

**这是本期唯一真正重要的机制。** 后面所有东西都建立在它之上。

**`paper/claims.yaml`**

```yaml
claims:
  - id: primary
    source:
      kind: contract           # contract | ablation | approved
      ref: hypothesis          # 契约里的字段名
    evidence:
      runs_tag: main_confirmed # 结果库里的一组 run
      stat: paired_t
    template: >
      {{method}} 在 {{dataset}} 的 {{split}} 协议下将 {{metric}}
      从 {{agg:baseline.metric}} 提升到 {{agg:main.metric}}
    status: pending            # 由 hypothesis_held 填，不可手写

  - id: mech_a1
    source:
      kind: ablation
      ref: a1                  # 必须是 contract.preregistered_ablations 里的 id
    evidence:
      runs_tag: ablation_a1
    template: >
      移除 {{component}} 后 {{metric}} 下降 {{agg:abl_a1.delta}}，
      说明性能来自 {{mechanism}} 而非 {{confound}}
```

**规则（全部由 checker 硬校验）：**

1. 正文里每一个强论断句必须携带 `[claim:<id>]` 标记
2. 强论断的判定用 `config/claims_patterns.yaml` 里的正则（"优于"/"首次"/"显著"/"SOTA"/"证明"/"significantly"/"outperform"/"novel"…），列表只增不减
3. 每个 `[claim:id]` 的 id 必须在 registry 里
4. registry 里每条的 `source.kind` 只有三种：
   - `contract` — `ref` 必须是冻结契约里存在的字段
   - `ablation` — `ref` 必须是 `contract.preregistered_ablations` 里的 id
   - `approved` — 必须在 `audit` 表里有对应的 `claim_approval` 卡点记录
5. **没有第四种。**「我从结果里看出来的」在 schema 层面不可表达

```python
def validate_registry(registry, contract, audit) -> list[Violation]:
    """任一违规都是 checker 的 FAIL，不是 warning。"""

def scan_paper_claims(text, patterns) -> list[UnregisteredClaim]:
    """找出所有匹配强论断模式但没有 [claim:id] 标记的句子。
    这些是 agent 试图偷偷加结论的地方。"""
```

**测试 `test_harking_attempt_blocked`（本期验收核心）：**

```python
def test_harking_attempt_blocked():
    """在正文里插一句契约里没有的强论断，比如
    「我们还发现该方法在低信噪比被试上尤其有效」。
    断言：checker FAIL，报告指出这句话没有注册的 claim，
    且无法通过注册（因为它的 source 不属于三种之一）。"""
```

### 4.2 `paper/templates/` — 三套骨架

`hypothesis_held` 由确认协议的终态确定，**不由任何 LLM 判断**：

| 终态 | test 结果 | hypothesis_held | 模板 |
|---|---|---|---|
| `DONE` | 达到 threshold | `True` | positive |
| `DONE` | 未达 threshold | `False` | negative |
| `GATE_POST_SEARCH` → 直接写负面 | 未开测试集 | `False` | negative |
| `BUDGET_EXHAUSTED` 且未确认 | 未开测试集 | `None` | inconclusive |

> 第二行是最重要也最容易被忽略的情况：**dev 上确认了，test 上没成立**。
> 这恰恰是最有价值的负面结果，它说明搜索过程存在过拟合。
> 系统必须能自然地写出这篇论文。

每个模板的骨架固定（章节顺序、Abstract 首句、Limitations 必填项），agent 填的是解释性文字，不是结论。

`negative.md` 的骨架里必须包含：
- 假设是什么，为什么当时认为它合理（来自契约的 `novelty_note`）
- 做了多少次实验、消耗多少预算（来自结果库，模板渲染）
- 哪些方向被排除（来自台账里 `kind=deadend` 的条目）
- dev/test gap 的量化（如果适用）

**第二期攒下的 `fluke` 记录和 `deadend` 台账，在这里变成 Limitations 章节的素材。** 这是当时多花记录成本的回报。

### 4.3 `loop/ablation.py` — 消融

```python
def plan_ablations(contract) -> list[AblationPlan]:
    """只从 contract.preregistered_ablations 生成。不推断，不补充。"""

def run_ablation(plan, node, contract) -> list[str]:
    """走和主实验完全相同的 harness / results 路径。
    每个 run 带 ablation_id。
    需要 CONFIRM_SEEDS（n_seeds 个种子），但不需要 CONFIRM_TRANSFER。"""
```

**新增卡点 `ablation_extension`**：如果写作阶段发现证据不足，需要额外消融——

```bash
python -m core.gates review ablation_extension
```

表单要求填：新消融的 id、它要检验什么、**它可能证伪什么**。最后一项为空则拒绝。
批准后写 `audit`，此后这个 ablation_id 可以作为 claim 的 `source.kind: approved`。

> 这个流程刻意麻烦。加消融应该有摩擦，否则它就退化成"补数据支持已有结论"。

### 4.4 `figures/` — 确定性图表

**`figures/_lib.py`** 统一处理确定性：

```python
def deterministic_setup():
    """matplotlib 的 byte-identical 输出需要显式处理：
    - os.environ['SOURCE_DATE_EPOCH'] = '0'      去掉 PDF 时间戳
    - matplotlib.rcParams['svg.hashsalt'] = 'fixed'
    - rcParams['pdf.compression'] = 0             便于 diff
    - 固定字体（不要依赖系统字体查找顺序）
    这几条不做，重跑就不会 byte-identical，I19 无法满足。"""
```

**每张图一个目录**，`manifest.json`：

```json
{
  "figure_id": "fig_01_main_comparison",
  "script_sha": "…",
  "run_ids": ["…", "…"],
  "contract_hash": "…",
  "output_sha256": "…",
  "generated_at": "…"
}
```

```bash
make figures        # 重跑全部，更新 out.pdf
make figures-check  # 重跑到临时目录，比对 sha256，不一致则 FAIL
```

`figures-check` 进 `make check`。

> 如果某张图确实无法 byte-identical（比如依赖了某个不确定的库），
> **不要放宽检查**——把那张图改成可确定的，或者换一种画法。

### 4.5 `writing/compose.py` — 写作编排

**输入 schema（agent 只能看到这些）：**

```python
class WritingInput(BaseModel):
    contract: Contract              # 冻结的
    hypothesis_held: bool | None    # 已确定，不由 agent 判断
    claim_registry: ClaimRegistry   # 已校验
    figures: list[FigureManifest]
    results_summary: str            # 由确定性代码生成的结果表，不是 agent 查库
    lessons: list[Lesson]           # 供写 Limitations
    template: Literal["positive", "negative", "inconclusive"]
```

**agent 的任务描述必须是这句话，不要改写：**

> 契约里的假设是 `<hypothesis>`，实验结论是 `<held>`。
> 按 `<template>` 的骨架，填写解释性文字。
> 你不能提出契约里没有的结论。你不能写任何数字——数字用 `{{run:...}}` / `{{agg:...}}` 模板。
> 你不能写任何引用——引用用 `\cite{key}`，key 必须来自提供的文献库。

**不要**给它 `results` 库的查询工具。它拿到的是已经生成好的 `results_summary`。
理由：给了查询工具，它就会去翻数据找故事。这正是要防的。

### 4.6 `writing/disclosure.py` — 披露文档

`AI_CONTRIBUTION.md` **确定性生成**，来源全部是 audit 表和结果库：

```markdown
# AI 参与度披露

## 各阶段分工
| 阶段 | 执行者 | 人工卡点 |
| 选题 | 人工 | — |
| 实验搜索 | agent (<model>) | 契约审批、新颖性裁决 |
| 消融 | agent | 消融启动 |
| 写作 | agent (<model>) | 审稿意见处理 |

## 使用的模型
<从 runs / audit 汇总>

## 人工决策记录
<audit 表里所有 reject / edit 的摘要>

## 契约
contract_id / version / content_hash / frozen_at

## 复现
make reproduce
```

多数会议和期刊现在要求披露 AI 参与程度，部分明确限制。**投稿前先查目标期刊的具体政策**，这份文档是原料，不是终稿。

### 4.7 `core/checker.py` 强化

第一期的 C1–C10 从骨架变成真检查，并新增：

```
[ ] C11 所有强论断句都有 [claim:id] 标记
[ ] C12 所有 claim 的 source 合法（contract / ablation / approved）
[ ] C13 所有 ablation run 的 ablation_id 已预注册或经批准
[ ] C14 make figures-check 通过
[ ] C15 unverifiable 引用清单已生成，且有人工签字记录
[ ] C16 论文声明的 hypothesis_held 与确认协议终态一致
[ ] C17 Limitations 章节非空；若台账（active lessons）非空则必须至少引用一条，
        台账为空的干净正结果可豁免该引用要求（但章节仍必须非空）
```

**C15 的设计**：`unverifiable` 引用不阻断，但必须列出来，且需要一次 `python -m core.gates review citation_unverifiable` 签字。签字记录进 audit。
理由：付费墙文献验证不了是客观现实（PaperQA2 开源版不含全文检索），但不能假装它们被验证过。

### 4.8 引用蕴含检查落地

第一期的 `support_check` 骨架现在真正实现：

```python
def support_check(claim: str, key: str) -> Verdict:
    """1. 从 corpus 取被引文献。oa_status=open 且有全文 → 用全文相关段落；
       否则用 abstract；都没有 → unverifiable
    2. 一次 LLM 调用，输出 supported / partial / unsupported / unverifiable + 证据片段
    3. 结果写 claim_support 表缓存（claim_hash + key）"""
```

只对**强论断**的引用跑（背景性陈述不跑，成本不划算）。
`unsupported` → checker FAIL。`partial` → 强论断上 FAIL，背景陈述上 PASS。

---

## 5. 测试

两个验收核心：

```python
# tests/test_claims.py
def test_harking_attempt_blocked():
    """插入一句契约里没有的强论断，断言 checker FAIL 且无法注册。"""

# tests/test_negative_path.py
def test_negative_result_path_completes():
    """构造 hypothesis_held=False 的场景（用第二期跑出来的真实数据，
    人为把 threshold 调高到达不到），走完整流程：
    选模板 → 写作 → 渲染 → make check 全绿 → make package 出包。

    如果这条路走不通，说明系统只能输出"我们成功了"，
    那它的每一份输出都不可信。"""
```

其余：

```python
test_claim_without_source_cannot_register()
test_strong_claim_without_marker_detected()
test_approved_claim_requires_audit_record()
test_ablation_not_preregistered_rejected()
test_ablation_extension_requires_falsification_field()
test_figure_rerun_byte_identical()
test_figure_manifest_run_ids_exist()
test_unverifiable_citations_listed_and_require_signoff()
test_unsupported_citation_fails_check()
test_hypothesis_held_matches_confirm_terminal_state()
test_writing_agent_has_no_results_query_tool()   # 检查工具清单
test_disclosure_generated_from_audit_only()
```

---

## 6. 分阶段执行

**Phase 11 — Claim registry**
`writing/claims.py` + `paper/claims.yaml` + `config/claims_patterns.yaml`。
先做这个，因为它约束后面所有东西。
验收：`test_claims.py` 全绿，**特别是 `test_harking_attempt_blocked`**。

**Phase 12 — 模板体系**
三套模板骨架 + `hypothesis_held` 的确定性推导。
验收：三条终态路径都能选出正确模板；`test_hypothesis_held_matches_confirm_terminal_state` 绿。

**Phase 13 — 消融**
`loop/ablation.py` + `ablation_extension` 卡点。
用第二期的真实搜索结果跑一次预注册消融。
验收：`test_ablation.py` 全绿；消融 run 在结果库里带正确的 `ablation_id`。

**Phase 14 — 图表**
`figures/_lib.py` + 至少两张真图（主对比 + 一张消融）。
验收：`make figures-check` 连跑三次都通过（确定性要经得起重复）。

**Phase 15 — 写作与引用检查**
`writing/compose.py` + `support_check` 落地 + checker C11–C17。
验收：能产出一篇 positive 论文，`make check` 全绿。

**Phase 16 — 负面路径与打包**
`writing/negative.py` + `writing/disclosure.py` + `make package`。
验收：**`test_negative_result_path_completes` 绿**；`make package` 产出的目录在一台干净机器上 `make reproduce` 能跑通。

---

## 7. 给 Claude Code 的提示词

> 读 IMPLEMENTATION-P3.md，前置是 P1、P2 已完成。按 §6 从 Phase 11 开始。
> 每个 Phase 结束停下来跑 `make test` 和 `make check`，汇报后等确认。
>
> 四条硬约束：
> 1. claim registry 的 `source.kind` 只有三种，不要加第四种，不要加"other"或"manual"
> 2. 写作 agent 不给结果库查询工具，只给生成好的 results_summary
> 3. 图表确定性不达标就改图，不要放宽检查
> 4. `unverifiable` 引用不要静默放行，也不要自动删除对应句子
>
> 如果某处让你觉得"这样限制太死，agent 写不出好文章"——这是预期的。
> 本期的目标不是写出好文章，是写不出假文章。文章质量靠人改。

---

## 8. 本期仍然不做

- 选题阶段（C1）
- LLM 审稿（C4 的 LLM 部分）
- 多契约并行 / 跨课题知识积累

---

## 9. 第四期预告

剩下的两件事，价值排序很清楚：

**选题流水线**（价值中等）。抄 Stanford 那套四步骨架，检索走已有的 corpus，
排名器换成分轴独立打分，输出层强制填契约 schema——填不满的字段就是这个想法还不能进实验循环的证据。
预计工作量 2–3 天，是整套系统里唯一"自己写比 fork 快"的模块。

**LLM 审稿**（价值最低，放最后）。强制每条意见指向具体行号或表号，
只采信可核查的反对意见，聚合分数直接丢弃。
到这一步你会发现，第一期就做好的确定性检查器已经覆盖了 LLM 审稿能查出的大部分问题，
剩下的部分——文章讲得清不清楚、贡献值不值得——本来就该你导师来说。
