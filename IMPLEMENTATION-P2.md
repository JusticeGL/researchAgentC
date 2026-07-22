# 科研 agent 第二期 — 实验循环

> 前置：第一期（`IMPLEMENTATION.md`）全部完成，`make reproduce` 能跑通。
> 本期把 agent 接进来，做的是**实验搜索**，仍然不碰选题和写作。
> 第一期的 §1 不变式、§2 禁止事项、§8 `CLAUDE.md` 全部继续生效，本期只增不减。

---

## 0. 一句话目标

在第一期的底座上接一个自动实验循环，使得：

1. agent 能自己提改动、写代码、训练、评估，几十到几百次
2. 它**改不了**评测，改了立刻被发现且该次实验作废
3. 它**不会**重复走已经走过的方向
4. 它**不能**因为一次高分就宣布成功——命中阈值只触发验证，不触发停止
5. 整个过程结束时，测试集仍然只被打开过零次或一次

本期结束时的产物：一棵有几十个节点的实验树、一份被压缩过的经验台账、以及一个**通过了确认协议的结论**——或者一个诚实的"在预算内没找到"。后者同样算成功。

---

## 1. 新增不变式（累加在第一期 I1–I8 之上）

| # | 不变式 | 强制手段 |
|---|---|---|
| I9 | agent 只能写 `solution/`；`harness/` `core/` `contracts/` 只读 | 容器 mount + 每步 diff 检查，双保险 |
| I10 | 每个树节点至少对应一条 run 记录 | `tree_nodes.run_ids`（JSON list）非空；JSON 列做不了 SQL 外键，写入时在应用层校验每个 run_id 都存在于 `runs` |
| I11 | **命中阈值不得终止搜索** | 阈值判定函数的返回类型里根本没有 `DONE`，只有 `CONFIRM_PENDING` |
| I12 | 与历史提案高度相似的改动不得直接执行 | dedup 闸门在 interpreter 之前 |
| I13 | 训练期出站网络受限 | 容器网络策略 + 违规写 `sentry_events` |
| I14 | 进入新 run 的上下文不含全量日志 | `build_context()` 有 token 上限断言 |
| I15 | 台账压缩不得丢失证据 | compact 前后 evidence run_id 的并集必须相等 |

**I11 是本期的核心。** 它是防 p-hacking 的唯一机制，实现上要做到"想跳过都跳不过"：

```python
class SearchVerdict(Enum):
    CONTINUE = "continue"
    CONFIRM_PENDING = "confirm_pending"
    BUDGET_EXHAUSTED = "budget_exhausted"
    # 刻意没有 DONE / SUCCESS。终止只能由确认协议状态机给出。
```

---

## 2. 明确不要做的事（累加）

- ❌ **不要**给搜索循环加"提前成功退出"的快捷路径，哪怕加了 `--yes-i-am-sure` 开关
- ❌ **不要**让 agent 能读到 test split 的任何东西，包括 shape、类别分布、样本数
- ❌ **不要**把全量 runs 表塞进 prompt。台账是被压缩的，不是被截断的
- ❌ **不要**用 LLM 做去重判断。用本地 embedding，便宜且确定
- ❌ **不要**把 AIDE 的 `Journal` 当作真相来源。真相在我们的 SQLite 里，AIDE 的树只是它的内部状态
- ❌ **不要**为了让 agent"跑得顺"而放宽 sentry 的检测规则。误报是可以接受的成本
- ❌ **不要**在本期加选题、写作、审稿、消融自动设计

---

## 3. 目录新增

```
research-agent/
├── adapters/
│   ├── __init__.py
│   ├── aide_adapter.py      # AIDE ↔ 我们的 contract / results
│   └── evaluator.py         # 替换 AIDE 的指标提取路径
├── loop/
│   ├── __init__.py
│   ├── tree.py              # 我们自己的树记录
│   ├── ledger.py            # lessons 台账 + compact
│   ├── dedup.py             # 提案去重闸门
│   ├── context.py           # 上下文构建器
│   ├── confirm.py           # 确认协议状态机
│   ├── sentry.py            # 作弊探测 + 熔断
│   ├── cost.py              # 成本预估
│   └── run_loop.py          # 主入口
├── sandbox/
│   ├── Dockerfile
│   └── run_node.sh          # 单节点执行脚本
├── schema/
│   ├── tree.sql
│   ├── ledger.sql
│   └── sentry.sql
└── tests/
    ├── test_boundary.py
    ├── test_confirm.py
    ├── test_dedup.py
    ├── test_ledger.py
    └── test_sentry.py
```

---

## 4. 模块规格

### 4.1 `sandbox/` — 边界强制（先做这个）

后面所有东西都依赖它。做不好，其余全是装饰。

**挂载策略**（`run_node.sh`）：

```
harness/       → :ro
core/          → :ro
contracts/     → :ro
data/moabb/    → :ro          MOABB 缓存，只读
solution/      → :rw          唯一可写
artifacts/<node_id>/ → :rw    输出目录
```

**网络**：训练期默认 `--network=none`。需要下载数据集时单独一个准备阶段，在 agent 启动前完成。
如果确实需要联网（比如拉预训练权重），用白名单代理，并把每次出站请求写 `sentry_events`。

> 参考依据：ML 工程 agent 的评测里，评测器锁定能消除自然行为下常见的篡改尝试且开销可控；
> 而在开放网络下，agent 会去检索答案而不是推导答案。所以默认断网不是洁癖，是必需。

**每步 diff 检查**（双保险，防止 mount 配置出错）：

```python
def assert_boundary_intact(before_tree_hash: dict, after: dict) -> None:
    """比对 harness/ core/ contracts/ 的文件 hash。
    任一变化 → 抛 BoundaryViolation，触发熔断（不是作废单次 run，是停机）。"""
```

**验收**：写一个故意写 `harness/evaluate.py` 的测试脚本，跑进沙箱，断言它失败且 `assert_boundary_intact` 报警。

### 4.2 `loop/tree.py` — 实验树

```sql
CREATE TABLE tree_nodes (
    node_id            TEXT PRIMARY KEY,
    contract_id        TEXT NOT NULL,
    parent_node_id     TEXT,
    change_description TEXT NOT NULL,     -- 一句话，用于去重
    change_embedding   BLOB,              -- sentence-transformers 向量
    patch              TEXT NOT NULL,     -- 相对父节点的 diff
    run_ids            TEXT NOT NULL,     -- JSON list，至少一个
    status             TEXT NOT NULL,     -- ok|buggy|abandoned|fluke|confirmed
    debug_attempts     INTEGER NOT NULL DEFAULT 0,
    dev_score          REAL,              -- 仅 dev，用于搜索排序
    created_at         TEXT NOT NULL
);
CREATE TRIGGER tree_nodes_no_delete BEFORE DELETE ON tree_nodes
BEGIN SELECT RAISE(ABORT, 'tree is append-only'); END;
```

> **`run_ids` 是 JSON list 列，SQLite 无法对它做外键约束（I10）。** 因此"每个节点至少一条、
> 且每个 run_id 都在 `runs` 里"必须在 `tree.py` 写入时用应用层校验强制——不要以为写了
> `REFERENCES` 就有保证（SQLite 默认连 `foreign_keys` pragma 都不开）。

> **`dev_score` 只在搜索内部使用。它不是一个 `Metric`，也永远不能进论文。**
> 论文里的数字必须走 `runs` → `Metric` → `render` 那条路。
> 加一个测试：断言 `dev_score` 这个字段名不出现在 `core/render.py` 和 `paper/` 里。

**节点选择策略**（`adapters/policy.py`）：

```python
def select_node(tree, cfg) -> Node | None:
    """UCB 风格：score(n) = normalized(n.dev_score) + c * sqrt(log(N) / (1 + n.expansion_count))
    - 只从 status in (ok, confirmed) 的节点里选
    - fluke 节点不选，但保留在树里（它是有信息量的负面结果）
    - 返回 None 表示应该 draft 一个新根节点"""
```

`c` 写进配置，默认 0.5。这个参数值不重要，**有一个显式的探索项**才重要。

### 4.3 `loop/dedup.py` — 提案去重闸门

```python
def check_duplicate(proposal: str, contract_id: str, threshold: float = 0.92
                    ) -> DedupResult:
    """embed 后与全部历史 change_description 比对。
    返回 nearest 节点及其结果摘要。"""
```

**命中时不直接拒绝**，而是把最近邻的**结果**回灌给 agent：

> 你提的改动与节点 `n_0042` 相似度 0.94。那次的改动是「<change>」，
> 结果是 `<metric> = <value>`（`<status>`）。
> 要么换个方向，要么明确说明这次为什么会不同。

**连续两次命中同一个最近邻 → 硬拒绝**，该分支标 `abandoned`，回 `select_node`。

用本地 sentence-transformers 模型（`all-MiniLM-L6-v2` 量级即可），不调 API。几百个节点的比对是毫秒级。

### 4.4 `loop/ledger.py` — 经验台账

```sql
CREATE TABLE lessons (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id   TEXT NOT NULL,
    text          TEXT NOT NULL,        -- 一句话
    kind          TEXT NOT NULL,        -- deadend|constraint|insight|bug_pattern
    evidence      TEXT NOT NULL,        -- JSON list of run_id
    created_at    TEXT NOT NULL,
    superseded_by INTEGER REFERENCES lessons(id)
);
```

- active = `superseded_by IS NULL`，上限默认 40 条
- 超限触发 `compact()`：一次 LLM 调用，输入全部 active，输出合并后 ≤ 40 条
- 被合并的旧条目**不删除**，写 `superseded_by`
- **`compact()` 后必须断言：新 active 集合的 evidence run_id 并集 ⊇ 旧集合的并集**。丢证据即失败重试

只有 active lessons 进上下文。历史 run 的查询走 `results.query_runs()`，是 agent 的一个工具，不是 prompt 里的一段文本。

### 4.5 `loop/context.py` — 上下文构建器

```python
def build_context(node, contract, budget_tokens: int) -> str:
    """按优先级注入，超预算从低优先级砍起：
    P1 契约关键字段：question / hypothesis / primary_metric / threshold /
                     direction / split_protocol / kill_criteria
    P2 active lessons 全部
    P3 父节点完整代码
    P4 该分支祖先链的结果表（node_id, change_description, dev_score），最多 K=15 条
    P5 兄弟节点的 change_description（供 agent 自己避重）

    结束前 assert token_count <= budget_tokens。"""
```

**不注入**：全量 runs 表、其他分支的详细内容、历史 stdout、上一次的完整报错（只给最后 50 行）。

### 4.6 `loop/confirm.py` — 确认协议（本期最重要）

状态机：

```
                      ┌──────────────┐
                 ┌───▶│  SEARCHING   │◀──┐
                 │    └──────┬───────┘   │ fluke: 该节点标 fluke，
                 │           │           │ 经验入台账，回到搜索
                 │  dev 分数越过阈值      │
                 │           ▼           │
                 │  ┌────────────────┐   │
                 │  │ CONFIRM_SEEDS  │───┘
                 │  └────────┬───────┘
                 │    n_seeds 个新种子，仍在 dev
                 │    通过条件：agg.mean − ci_half ≥ threshold
                 │           ▼
                 │  ┌────────────────────┐
                 │  │ CONFIRM_TRANSFER   │──┐ 不通过：标 fluke
                 │  └────────┬───────────┘  │ 回 SEARCHING
                 │    契约里预先指定的第二数据集或第二划分协议
                 │           ▼               │
                 │  ┌────────────────────┐  │
                 │  │  GATE_PRE_TEST     │  │  人工卡点
                 │  └────────┬───────────┘  │  「确认要开测试集了」
                 │           │ approve       │
                 │           ▼               │
                 │  ┌────────────────────┐  │
                 │  │    TEST_ONCE       │  │  redeem token, eval_test
                 │  └────────┬───────────┘  │  整个契约生命周期唯一一次
                 │           ▼               │
                 │  ┌────────────────────┐  │
                 │  │       DONE         │  │
                 │  └────────────────────┘  │
                 │                          │
    预算耗尽 ─────┴──▶ ┌──────────────────┐ │
                      │ GATE_POST_SEARCH │◀┘
                      └────────┬─────────┘
                        人工决定：仍进 TEST_ONCE / 直接写负面结果 / 加预算
```

关键实现要求：

- `TEST_ONCE` 的唯一入口是 `GATE_PRE_TEST` 或 `GATE_POST_SEARCH` 的人工 approve。**代码里不存在其他到达 `TEST_ONCE` 的边**
- `CONFIRM_SEEDS` 的通过条件用**置信区间下界**，不是均值。默认 95% CI
- 每次状态转移写 `audit` 表
- `fluke` 是有价值的结果：转移到 `SEARCHING` 时必须往台账写一条 `kind=deadend` 的记录

### 4.7 `loop/sentry.py` — 作弊探测

对每个节点的 `patch` 做静态检查 + 对执行做运行时检查：

| 规则 | 动作 |
|---|---|
| S1 写了 `harness/` `core/` `contracts/` | **熔断停机** |
| S2 写文件到 `solution/` 和 `artifacts/<node>/` 之外 | **熔断停机** |
| S3 裸 `except:` 或 `except Exception: pass` 包住评测调用 | run 作废 + 人工审阅 |
| S4 函数体只有 `return <literal>` 且函数名含 metric/score/acc | run 作废 |
| S5 dev 阶段代码里出现 test split 相关标识符 | run 作废 + 人工审阅 |
| S6 `sys.path` / `importlib` 操作指向 harness | run 作废 + 人工审阅 |
| S7 训练脚本里出现 `requests` / `urllib` / `socket` | run 作废 |
| S8 `harness_hash()` 与 run 记录不符 | run 作废 |

```sql
CREATE TABLE sentry_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id    TEXT NOT NULL,
    rule       TEXT NOT NULL,
    severity   TEXT NOT NULL,       -- halt|invalidate
    detail     TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

**误报是可接受的成本。** 如果 S3 挡住了一个合法的异常处理，agent 会重写一遍——代价是几分钟。反过来漏报的代价是整篇论文作废。

### 4.8 `adapters/aide_adapter.py` — AIDE 集成

**先读实际安装的包。** 本文档不假定 AIDE 的内部 API，只规定我们这边的接口。
`pip install -U aideml` 后读 `aide/agent.py`、`aide/journal.py`、`aide/interpreter.py`，确认三个扩展点的实际形态，然后写适配层。

需要替换的三处：

1. **执行环境** — AIDE 默认在一个工作目录里跑脚本。改成调 `sandbox/run_node.sh`，把 `solution/` 作为工作区，`harness/` 只读可见
2. **指标提取** — AIDE 从 stdout 或返回值里解析一个 float。改成：脚本调 `harness.evaluate_dev()`，后者写 `runs` 表并返回 `run_id`；适配层从 `runs` 读回 `dev_score` 交给 AIDE 的搜索用
3. **提案钩子** — 在 AIDE 决定 draft/improve 之后、执行之前，插 `dedup.check_duplicate()`

**AIDE 的 `Journal` 不是真相来源。** 每个 AIDE 节点执行完，我们往 `tree_nodes` 写一条自己的记录。如果两边不一致，以我们的为准。这样 AIDE 版本升级或换成别的搜索器时，历史数据不受影响。

配置映射（写进 `config/loop.yaml`）：

```yaml
aide:
  num_drafts: 3            # 初始草案数
  max_debug_depth: 3       # 对应 L2 上限
  debug_prob: 0.5
search:
  ucb_c: 0.5
  max_nodes: 60            # 硬上限，与预算共同约束
dedup:
  threshold: 0.92
  hard_reject_after: 2
ledger:
  max_active: 40
context:
  budget_tokens: 24000
  ancestor_rows: 15
confirm:
  n_seeds: 5
  ci: 0.95
```

### 4.9 `loop/cost.py` — 成本预估

契约批准时（第一期的 `contract_review` 卡点）就要展示这个数字：

```python
def estimate(contract, cfg) -> CostEstimate:
    """
    LLM 成本  ≈ max_nodes × (draft + debug_depth) × avg_tokens × 单价
    训练成本  ≈ max_nodes × single_run_minutes × gpu_hourly
    确认成本  ≈ n_seeds × single_run_minutes × gpu_hourly × 预计确认次数
    返回三档：乐观 / 中位 / 悲观
    """
```

`single_run_minutes` 从第一期跑过的 baseline run 里取真实中位数，不要拍脑袋。

**熔断**：任一维度（USD / GPU-hours / wall-clock）达到契约预算的 100% → 转 `BUDGET_EXHAUSTED`。达到 80% → 提醒。

---

## 5. 测试

`tests/test_confirm.py` 里这一条是本期的验收核心：

```python
def test_confirm_rejects_pure_noise_improvement():
    """构造一个只有随机种子不同、实际什么都没改的"改动"，
    人为让它在单个 seed 上越过阈值（挑一个高分种子）。
    断言：确认协议把它打回，节点被标 fluke，台账多了一条 deadend。

    这个测试验证的是整套防 p-hacking 机制。它过了，
    说明你的系统不会把噪声当成发现。"""
```

其余：

```python
# test_boundary.py
test_agent_cannot_write_harness()
test_agent_cannot_write_outside_solution()
test_boundary_violation_halts_loop()
test_network_disabled_during_training()

# test_confirm.py
test_threshold_hit_returns_confirm_pending_not_done()
test_no_code_path_reaches_test_once_without_human_approval()  # ast 遍历状态机
test_confirm_seeds_uses_ci_lower_bound()
test_test_token_unused_after_full_search()

# test_dedup.py
test_near_duplicate_gets_result_feedback()
test_repeated_near_duplicate_hard_rejected()

# test_ledger.py
test_compact_preserves_evidence_union()
test_only_active_lessons_enter_context()

# test_sentry.py
test_bare_except_around_eval_detected()
test_hardcoded_metric_return_detected()
test_test_split_identifier_in_dev_detected()

# test_context.py
test_context_under_token_budget()
test_context_excludes_full_runs_table()
```

`test_no_code_path_reaches_test_once_without_human_approval` 用 `ast` 静态分析 `loop/confirm.py`，找出所有给状态赋值为 `TEST_ONCE` 的语句，断言它们都在检查了人工 approve 的分支里。这个测试有点笨，但它挡的是最贵的错误。

---

## 6. 分阶段执行

**Phase 6 — 边界**
`sandbox/` + `loop/sentry.py`。
验收：`test_boundary.py` + `test_sentry.py` 全绿；故意写 harness 的脚本会失败并触发停机。

**Phase 7 — 树与台账**
`loop/tree.py` + `loop/ledger.py` + `loop/dedup.py` + `loop/context.py`。
此时还没有 agent，用手工构造的假节点测。
验收：`test_dedup.py` `test_ledger.py` `test_context.py` 全绿。

**Phase 8 — 确认协议**
`loop/confirm.py`。同样先不接 agent，用注入的假分数走完整个状态机。
验收：`test_confirm.py` 全绿，**特别是 `test_confirm_rejects_pure_noise_improvement`**。

**Phase 9 — AIDE 适配**
`adapters/`。先在一个玩具任务上跑通（比如 MOABB 里最小的那个数据集 + CSP/LDA baseline），确认三处替换生效。
验收：能跑出 5 个真实节点，每个节点在 `tree_nodes` 和 `runs` 里都有记录，`dev_score` 来自结果库。

**Phase 10 — 真实跑一次**
用第一期手写的那份契约，跑一次 60 节点上限的完整搜索。
验收：
- 跑完后 `make check` 仍然全绿
- `holdout_access` 表里 0 条或 1 条记录
- 台账被 compact 过至少一次且 evidence 未丢
- `sentry_events` 里的每一条都被人看过
- 产出一个 `audit/search_report.md`：树的形状、预算消耗、fluke 数量、最终状态

---

## 7. 给 Claude Code 的提示词

> 读 IMPLEMENTATION-P2.md，前置是 IMPLEMENTATION.md 已完成。
> 按 §6 的阶段顺序执行，从 Phase 6 开始。每个 Phase 结束停下来跑 `make test` 并汇报。
>
> 三条硬约束，任何时候不要违反：
> 1. 不要给搜索循环加提前成功退出的路径。命中阈值只能进确认协议。
> 2. 不要放宽 sentry 规则来让 agent 跑得顺。误报是可接受的。
> 3. AIDE 的内部 API 以实际安装的包为准，本文档的描述可能过时；发现不符时改文档。
>
> 如果某个实现让你觉得"这样太啰嗦了，简化一下"——先问我。本期的啰嗦是刻意的。

---

## 8. 本期仍然不做

- 选题阶段。契约继续手写
- 论文写作与渲染的自动化（第一期的 `render.py` 够用）
- LLM 审稿
- 消融自动设计
- 多契约并行

---

## 9. 第三期预告（不要现在做）

写作阶段。核心难点不是生成文字，是**贡献声明必须继承自冻结的契约**，而不是从结果反推。
写作 agent 的输入里会有 `contract.hypothesis` 和 `hypothesis_held: bool`，并且**必须支持负面结果模板**。
本期的 `fluke` 记录和台账里的 `deadend` 条目，到那时会变成论文的 limitations 章节素材——现在多花的记录成本，那时会还回来。
