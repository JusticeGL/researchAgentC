# 科研 agent 第四期 — 选题、审稿与闭环

> 前置：一、二、三期完成，能从手写契约走到打包交付。
> 本期补上剩下的两块，把入口和出口接起来。
> I1–I21、前三期的禁止事项、`CLAUDE.md` 全部继续生效。

**先说清楚：本期是整套系统里价值最低的一期。** 选题的价值中等（因为你导师能给的更多），
LLM 审稿的价值最低（因为第一期就做好的确定性检查器已经覆盖了它能查出的大部分问题）。
做它的理由是闭环，不是能力。如果你时间紧，跳过本期直接用系统产论文，损失很小。

---

## 0. 一句话目标

1. 从一个模糊领域或一组种子文献出发，产出**能填满契约 schema** 的候选课题
2. 让新颖性和可行性变成**计算**，而不是模型意见
3. 审稿意见必须指向具体位置，可核查的自动核查，聚合分数直接丢弃
4. 端到端跑通一次：模糊领域 → 选题 → 契约 → 实验 → 消融 → 写作 → 审稿 → 交付

---

## 1. 新增不变式（累加在 I1–I21 之上）

| # | 不变式 | 强制手段 |
|---|---|---|
| I22 | 填不满契约必填字段的想法不得进入候选池 | 填充层输出 `ContractDraft`，缺字段即降级为 `incomplete`，不进排名 |
| I23 | 新颖性判定必须基于检索 | `novelty_evidence` 非空且每条能在 corpus 解析出 ID，否则该轴不评分 |
| I24 | 生成器之间互不可见 | 独立进程 + 独立 context，代码层面没有共享 state |
| I25 | 可行性打分对照真实资源 | 从结果库取真实 run 时长、从 harness 取真实数据集规模 |
| I26 | 没有 locator 的审稿意见直接丢弃 | `Objection.locator` 必填且必须能在论文里定位 |
| I27 | 不计算、不存储、不展示聚合评分 | schema 里没有这个字段 |

**I22 是本期的核心机制。** 和前三期一样，用"结构上做不到"代替"规定不许做"：
一个想法如果说不清楚用哪个数据集、什么划分协议、成功阈值是多少、什么条件下算失败，
那它就不是一个可执行的研究计划，**系统不让它进入候选池**。

---

## 2. 明确不要做的事（累加）

- ❌ **不要**让生成器之间互相看到对方的想法。依赖式采样会让每轮解的熵持续下降，多样性恰恰是这一步唯一需要的东西
- ❌ **不要**做多智能体"讨论"环节。已有理论结果表明，各 agent 收到相同输入时标准辩论构成一个鞅——期望正确率不随轮次提升；控制住聚合策略后辩论与独立作答无显著差异
- ❌ **不要**把新颖性交给 LLM 判断。它对新颖性的直觉是整个系统里最不可靠的东西
- ❌ **不要**把五个轴合并成一个总分。合并分数会被行文流畅度主导
- ❌ **不要**给审稿 agent 打总分的能力
- ❌ **不要**用 Stanford 那个"预测 ICLR 录用"的排序器。它学的是 NLP 会议口味，对 BCI 没有意义

---

## 3. 目录新增

```
research-agent/
├── ideation/
│   ├── __init__.py
│   ├── generate.py       # 独立生成器
│   ├── novelty.py        # 检索式新颖性门
│   ├── feasibility.py    # 机械可行性计算
│   ├── score.py          # 分轴独立打分
│   ├── redteam.py        # 对抗环节
│   └── fill.py           # 契约填充层
├── review/
│   ├── __init__.py
│   ├── objection.py      # 意见 schema + locator 校验
│   ├── panel.py          # 多模型审稿编排
│   └── autocheck.py      # checkable 意见转断言
├── schema/
│   ├── ideas.sql
│   └── review.sql
└── tests/
    ├── test_ideation.py
    └── test_review.py
```

---

## 4. 选题流水线

### 4.1 `ideation/generate.py` — 独立生成

```python
def generate(seed_papers: list[str] | None, domain: str | None,
             models: list[str], n_per_model: int, temperature: float
             ) -> list[RawIdea]:
    """每个 (model, seed) 组合一个独立调用。
    不共享 context，不传入其他人的输出，不做多轮。
    价值全在跨模型的分布差异，不在对话。"""
```

**I24 的实现**：每个生成器是一个独立的函数调用，参数里根本没有"其他想法"这一项。
测试 `test_generators_are_isolated` 用 `inspect.signature` 断言这一点，
并断言 `generate()` 内部没有任何跨调用的可变状态。

建议配置：3 个模型 × 8 个种子 = 24 个原始想法。种子文献建议 5–15 篇，你自己挑。

### 4.2 去重

**直接复用第二期的 `loop/dedup.py`**，换个语料。同一个 embedding 模型、同一个阈值逻辑。
不要写第二套。

### 4.3 `ideation/novelty.py` — 检索式新颖性门

```python
def novelty_gate(idea: RawIdea, k: int = 15) -> NoveltyReport:
    """1. 从 idea 抽 2-3 个检索 query（这一步可以用 LLM）
    2. 走 core.corpus.search()，取 top-k 最近邻
    3. 对每篇最近邻，问一次：这篇是否已经做了 idea 描述的事？
       输出 done | partial | different + 一句理由
    4. 返回 verdict（novel | incremental | done）+ evidence（corpus key 列表）

    evidence 为空 → verdict 强制为 unknown，该想法此轴不评分，
    并在报告里标注"检索未命中，需人工判断"。不要因为检索空就判定为 novel。"""
```

**最后那句是重点。** "检索不到"和"不存在"是两回事。BCI 的很多工作在 IEEE 系付费墙后面，
你的 corpus 未必覆盖。让系统诚实地说"我不知道"，比让它猜"很新颖"有用得多。

### 4.4 `ideation/feasibility.py` — 机械可行性

**这一轴大部分可以算，不用问模型：**

```python
def feasibility(draft: ContractDraft) -> FeasibilityReport:
    """机械部分：
    - 数据集是否在 harness 支持列表里            → 是/否
    - 被试数 / 每被试试次数 / 通道数 / 采样率     → 从 MOABB metadata 读
    - 单次训练时间估计                            → 结果库里同量级模型的历史中位数
    - 总成本估计                                  → 复用第二期 loop/cost.py
    - 与 draft.budget 比对                        → 超了直接 infeasible

    需要 LLM 的只有一项：实现难度（是否依赖难以获取的组件/未开源的方法）。
    报告里把机械部分和 LLM 部分分开呈现。"""
```

五个轴里，**新颖性、可行性、可度量性、数据可得性四个轴的主体都是计算**，
只有"预期效应量"完全靠模型估计——而且要求它必须给出依据文献的 corpus key。

### 4.5 `ideation/fill.py` — 契约填充层（核心）

```python
def fill_contract(idea: RawIdea, novelty: NoveltyReport) -> ContractDraft:
    """把想法填进第一期的 Contract schema。
    必填字段任一为空 → status = "incomplete"，附上缺失字段列表。
    incomplete 的想法不进排名，但保留在 ideas 表里。"""
```

必填清单（缺一不可）：

```
datasets            具体到 MOABB 里的数据集名
split_protocol      within_session | cross_session | cross_subject
paradigm
baselines           至少一个，且能指出用哪个已有实现
primary_metric
success_threshold   一个具体数字
direction
stat_plan.n_seeds
kill_criteria       至少一条
preregistered_ablations  至少一条，且每条写明"它能证伪什么"
novelty_evidence    非空
```

> **`success_threshold` 和 `kill_criteria` 是最容易填不出来的两项，这是特征不是 bug。**
> 一个说不出"多少算成功、什么情况算失败"的想法，不是一个研究问题，是一个方向感慨。
> 让它停在 incomplete，报告给人，人来补——或者放弃它。

`reproduced_run_ids` 此时必然为空（还没跑过），这是唯一允许留空的必填字段，
它在实验阶段第一步被填上，`freeze()` 时校验非空（第一期 §5.4 规定了打破循环依赖的顺序：
该字段被排除在 `content_hash()` 之外，故冻结前 hash 即已确定，回填 run_id 不改变它）。

### 4.6 `ideation/score.py` — 分轴独立打分

```python
def score(draft, novelty, feasibility) -> AxisScores:
    """五个轴，五次独立调用（或直接取机械计算结果），不合并。
    novelty        ← novelty_gate 的 verdict，机械
    feasibility    ← feasibility 报告，主体机械
    measurability  ← 契约填充完整度，完全机械（缺几个字段就扣几分）
    data_access    ← 数据集是否公开可下载，机械
    effect_size    ← LLM 估计，必须附依据文献的 corpus key
    """
```

**输出是一张五列的表，不是一个数。** 排序由人在卡点上做，或者用一个显式的、写在配置里的
加权公式——但那个权重必须是你自己写的，不是模型给的。

### 4.7 `ideation/redteam.py` — 对抗环节（放最后）

```python
def red_team(draft, corpus) -> list[RedTeamReport]:
    """三个独立调用，用不同模型，互相看不到：
    Q1「给出这个课题必然失败的最强理由」
    Q2「找出已经做过它的论文」— 必须返回 corpus key，找不到就明确说找不到
    Q3「指出这个计划最弱的一环」

    不汇总，不投票，不打分。三份报告原样呈给人。"""
```

对抗而非共识。这一步的作用是给人提供反面材料，不是让系统自己下结论。

### 4.8 卡点

新增 `topic_selection`，逐字段过一遍 ContractDraft，附带：
- 五轴分数表
- novelty evidence 的 top-5 文献标题
- 三份 red team 报告
- 成本预估（乐观/中位/悲观）

批准后进第一期已有的 `novelty_verdict` 卡点（只问一个问题：这想法是不是已经有人做过），
然后 `freeze()`。

---

## 5. LLM 审稿

### 5.1 `review/objection.py`

```python
class Objection(BaseModel):
    locator: str
    # 必填，且必须能在论文里定位：
    #   "L142" | "Table 2" | "Fig 3" | "claim:mech_a1" | "§4.2"
    # 校验：locator 必须能解析到 build/ 里的一个实际位置，否则丢弃
    kind: Literal["factual", "unsupported", "missing_control", "clarity", "novelty"]
    checkable: bool
    statement: str
    suggested_check: str | None   # checkable=True 时必填
```

**没有合法 locator 的意见直接丢弃**，不是降权，是丢弃。理由：无法定位的意见无法处理，
留着只会稀释人的注意力。

**`review` 表里没有 score 字段。** LLM 审稿人的校准很差，且更多地在给行文流畅度打分。
不要存它，不要算它，不要展示它。

### 5.2 `review/autocheck.py`

```python
def autocheck(objections: list[Objection]) -> list[Verdict]:
    """对每条 checkable=True 的意见，把 suggested_check 转成一个断言并跑：
    - "表 2 的数字与正文不一致"     → 从 provenance.json 比对
    - "没有报告方差"                 → 查 Agg.n
    - "baseline 与所引论文不符"      → 查 corpus + reproduced_run_ids
    - "claim X 没有对应证据"         → 查 claim registry
    通过 → 意见不成立，驳回并记录
    不通过 → 意见成立，进人工清单的最高优先级
    转不成断言的 → 降级为 checkable=False"""
```

**这是本模块唯一真正有价值的部分。** 它把一部分审稿意见变成了自动化验证，
人只需要看剩下的主观部分——而那部分本来就该人看。

### 5.3 `review/panel.py`

3 个模型，各审一遍，不互相看。输出合并成一张按 `(checkable, kind)` 分组的清单，
去重（同一 locator + 同一 kind 视为重复），进 `review_comments` 卡点。

---

## 6. 测试

三个验收核心：

```python
def test_generators_are_isolated():
    """断言 generate() 的签名里没有"其他想法"这类参数，
    且函数内部没有跨调用的可变状态。
    这是本期唯一在防的架构错误。"""

def test_incomplete_idea_cannot_enter_pool():
    """构造一个说不出 success_threshold 的想法，
    断言它被标 incomplete、不进排名、但保留在 ideas 表里。"""

def test_objection_without_locator_discarded():
    """构造一条"整体写作质量有待提高"的意见，
    断言它被丢弃且不出现在人工清单里。"""
```

其余：

```python
test_novelty_empty_evidence_yields_unknown_not_novel()
test_feasibility_uses_real_run_times_from_results()
test_axis_scores_never_merged_into_single_number()
test_no_score_field_in_review_schema()
test_checkable_objection_becomes_assertion()
test_redteam_calls_are_independent()
test_dedup_reuses_phase2_module()          # 别写第二套
```

---

## 7. 分阶段执行

**Phase 17 — 生成与去重**
`ideation/generate.py`，复用 `loop/dedup.py`。
验收：`test_generators_are_isolated` 绿；能从一个领域描述产出 24 个去重后的原始想法。

**Phase 18 — 新颖性与可行性**
`ideation/novelty.py` + `ideation/feasibility.py`。
验收：新颖性报告的 evidence 全部能解析出 DOI/arXiv ID；可行性报告里机械部分与 LLM 部分分开呈现。

**Phase 19 — 填充、打分、红队、卡点**
`ideation/fill.py` + `score.py` + `redteam.py` + `topic_selection` 卡点。
验收：`test_incomplete_idea_cannot_enter_pool` 绿；能在 CLI 里过完一个 draft 并 freeze。

**Phase 20 — 审稿**
`review/`。
验收：`test_objection_without_locator_discarded` 绿；autocheck 能驳回至少一条假意见。

**Phase 21 — 闭环**
`make full`：领域描述 → 选题 → 契约 → 实验 → 消融 → 写作 → 审稿 → 打包，一次跑通。
中间的五个人工卡点照常触发。
验收：全程 `make check` 每阶段结束都绿；产出物在干净机器上 `make reproduce` 通过。

---

## 8. 给 Claude Code 的提示词

> 读 IMPLEMENTATION-P4.md，前置 P1–P3 已完成。按 §7 从 Phase 17 开始。
>
> 四条硬约束：
> 1. 生成器之间不共享任何状态。不要"优化"成一个批量调用共享 context
> 2. 检索不到不等于新颖。evidence 为空时 verdict 必须是 unknown
> 3. 五个轴不合并成总分。不要加 `overall_score` 字段
> 4. review 的 schema 里不要有 score 字段
>
> 去重直接 import `loop/dedup.py`，不要写第二套实现。

---

## 9. 系统建成后：它给不了你的东西

四期做完，你会有一套完整的自动科研流水线。写在这里，是为了让你别对它有错误期待。

**它保证的**：论文里的数字都是真跑出来的；引用都是真存在的；结论没有从结果反推；
测试集只被打开过一次；每个数字都能追溯到一次可复现的实验；负面结果能被正常写出来。

**它保证不了的**：

- **它不能判断一个问题值不值得做。** 契约设计得差，系统会非常严谨地执行一个差的研究。
  严谨性和重要性是两回事，这套东西只管前者。
- **它不能保证结论有意义**，只能保证结论不是假的。降低的是造假和自欺的概率，
  不是提高发现的概率。
- **引用蕴含检查在付费墙文献上仍然失效。** BCI 的核心期刊大量在 IEEE 系，
  你会长期看到一堆 `unverifiable`。这是现实，不是 bug。
- **它对"这个想法是否已经有人做过"的判断上限，等于你 corpus 的覆盖上限。**
  中文文献、会议 poster、未开源的工业界工作，它都看不到。
- **选题仍然应该主要由你和你导师完成。** 第四期的选题模块最好的用法是
  "帮我把一个模糊方向拆成 20 个可执行的契约草案供筛选"，而不是"帮我想课题"。

如果这套系统只帮你做到一件事，那就是：**你投出去的每一篇论文，
自己都能一键复现，且知道每个数字从哪来。** 在一个大量已发表结果无法复现的领域里，
这件事本身就有分量。

---

## 10. 之后可以考虑的（不在计划内）

- 跨课题的台账复用：让第二个课题能读到第一个课题的 `deadend` 记录
- 多契约并行搜索与预算分配
- 把确定性检查器单独开源——它对任何做 ML 实验的人都有用，且不依赖这套系统的其余部分
