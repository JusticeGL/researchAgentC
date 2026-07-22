# researchAgentC

一套面向 BCI/EEG 方向的**自动科研流水线底座**：从一个模糊领域或一份手写契约出发，
经选题 → 契约冻结 → 实验搜索 → 消融 → 写作 → 审稿 → 打包，产出一份**能一键复现**的论文交付包。

> 它的全部价值在于一组**结构性不变式**（I1–I27）。任何时候实现方便性与不变式冲突，牺牲方便性。
> 设计细节见 [`IMPLEMENTATION.md`](IMPLEMENTATION.md)（及 P2/P3/P4）与 [`CLAUDE.md`](CLAUDE.md)。

---

## 它保证什么，不保证什么

**保证**（降低造假与自欺的概率）：

- 论文里的数字都是**真跑出来**的，每个都能追溯到一次可复现的实验；
- 引用都**真实存在**（有 DOI/arXiv ID），裸数字进不了正文；
- 结论不是从结果**反推**出来的（claim registry 反 HARKing）；
- 测试集**只被打开一次**；负面结果能被正常写出来；
- 图表**逐字节可复现**（byte-identical）。

**不保证**（这套东西管不了的）：

- 不能判断一个问题**值不值得做**——契约设计得差，系统会非常严谨地执行一个差的研究；
- 不能保证结论**有意义**，只能保证结论不是假的；
- 引用蕴含检查在**付费墙文献**上仍会失效（大量 `unverifiable`，由人签字放行，不静默删句）；
- "是否已有人做过"的判断上限 = 你的 corpus 覆盖上限。

选题仍应主要由你和导师完成；本系统的选题模块最好的用法是"把一个模糊方向拆成 N 个可执行契约草案供筛选"。

---

## 核心理念：用"结构上做不到"代替"规定不许做"

不变式不是靠自觉，而是靠机制强制。例如：

| 不变式 | 强制手段 |
|---|---|
| I2/I3 指标不进字符串、裸数字不进正文 | 渲染器只认 `{{run:…}}`/`{{agg:…}}`/`{{lit:…\|cite=…}}` 模板，其余数字报错 |
| I8 契约冻结后不可变 | `content_hash` + 结果库/契约 append-only（SQLite 触发器禁止 DELETE/UPDATE） |
| I11 搜索裁决永不含 DONE/SUCCESS | `loop/confirm.py` + AST 测试强制 `TEST_ONCE` 只能在人工 approve 分支赋值 |
| I16/I17 反 HARKing | claim 的 `source.kind` 只有 contract/ablation/approved 三种，"从结果看出来的"在 schema 层无法表达 |
| I19 图表可复现 | `figures/_lib.py` 固定 matplotlib 输出；`make figures-check` 比对 sha256 |
| I22 想法填不满契约不进池 | 缺任一必填字段即 `incomplete`，不进排名 |
| I24 生成器互不可见 | `ideation/generate` 签名里没有"其他想法"参数，每次独立调用 |
| I26/无 score | 审稿意见无合法 locator 直接丢弃；review schema 无 score 字段 |

---

## 目录结构

```
core/       契约 / 结果库 / 文献库 / 渲染器 / 确定性检查器 / 人工卡点
harness/    MOABB 数据与评测包装（只增不改，改动会作废历史 run）
loop/       实验循环：sandbox 哨兵 / 实验树 / 台账 / 去重 / 确认协议 / 成本
adapters/   proposer/evaluator/aide 适配器（LLM 后端依赖注入）
writing/    claim registry / 模板 / 写作编排 / disclosure / 交付打包
figures/    确定性图表（byte-identical）
ideation/   选题流水线：生成 / 新颖性 / 可行性 / 填充 / 打分 / 红队
review/     LLM 审稿：意见 schema / locator 校验 / autocheck / 编排
schema/     全部 SQLite schema（含 append-only 触发器与 CHECK 约束）
config/     loop / render / claims 等配置
paper/      论文模板与 claim registry
tests/      测试（离线全绿；live 用例按需 skip）
run_full.py 端到端闭环入口（make full）
```

---

## 安装

要求 Python 3.11+（仓库同时兼容 3.9）。

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e .
```

可选依赖（缺失时相关能力/测试优雅降级或 skip，不阻塞离线全绿）：

- **MOABB + MNE**：真跑 BCI baseline（需 `~/mne_data` + 联网下载数据集）；
- **sentence-transformers**：语义去重（否则用确定性 char-3gram 哈希回退）；
- **aideml + LLM key**（`OPENAI_API_KEY`/`ANTHROPIC_API_KEY`）：真实 proposer 与审稿。

---

## 命令

```bash
make test           # 跑全部测试（可 make test PY=.venv/bin/python）
make check          # 确定性检查器（含 figures-check）
make render         # 渲染论文（core.render）
make figures        # 重跑全部图，更新 out.pdf 与 manifest
make figures-check  # 重跑到临时目录比对 sha256（I19 byte-identical）
make reproduce      # 从零复现全部数字（tests/test_smoke_e2e.py）
make package        # 生成可复现交付包
make full           # 闭环：领域→选题→契约→实验→消融→写作→审稿→打包
```

> `make full` 在缺 LLM key/aideml/数据集时走**离线确定性 demo**（假模型 + 直接写基线），
> 但走的是真实 ideation/gates/render/checker/review/package 代码路径——闭环装配是被验证过的。

---

## 四期概览

| 期 | 内容 | 关键交付 |
|---|---|---|
| 一 | 底座 | 契约冻结/不可变、两级评测、文献库 I5、渲染器裸数字扫描、确定性检查器、人工卡点 |
| 二 | 实验循环 | sandbox+sentry（S1–S8）、实验树/台账/去重/上下文、UCB、多阶段确认协议、run_loop |
| 三 | 消融/写作/交付 | claim registry、三套模板、消融、byte-identical 图表、写作编排+support_check+C11–C17、负面路径、disclosure、package |
| 四 | 选题/审稿/闭环 | ideation/（I22–I25、I27）、review/（I26、无 score）、run_full.py + make full |

---

## 测试状态

- conda base 3.9：**169 passed, 3 skipped**；
- `.venv` 3.11：**161 passed, 8 skipped**。

skip 项全部是外部 live 依赖（LLM key / aideml / sentence-transformers / MOABB 联网下载），
不阻塞离线全绿。

---

## 约定

改动前请读 `IMPLEMENTATION.md §1`（不变式）与 `§2`（禁止事项）；若改动会削弱某条不变式，
先停下来问。硬规则见 [`CLAUDE.md`](CLAUDE.md)。
