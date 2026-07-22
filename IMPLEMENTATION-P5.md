# 科研 agent 第五期 — 跨课题复用与多契约并行

> 前置：一至四期完成，`make full` 能端到端跑通。
> 本期做两件事：让第二个课题读得到第一个课题的经验；让多个契约共享预算并行搜索。
> I1–I27 全部继续生效。

---

## 0. 先说危险在哪

这两个功能看起来是工程便利，实际上是**整套系统最容易自毁的地方**。动手前必须先理解为什么。

### 危险一：跨课题读经验会击穿预注册

课题 A 探索完发现"方法 X 在数据集 D 上不 work"。课题 B 读到这条，于是不试 X。
看起来是省了钱。但此时 B 的搜索空间是被 A 的结果**预先裁剪过**的，
而 B 的契约里没有声明这件事——B 的预注册是假的。

更糟的版本：如果 A 曾经开过 D 的测试集，那条经验里就含有测试集信息。
B 继承它，B 的 holdout 从第一天起就不干净了。

### 危险二：多契约并行是组合层面的多重比较

单个契约诚实地只开一次测试集。但如果你在同一个数据集上跑 10 个契约，
你就在同一个测试集上做了 10 次比较。**每个契约都是诚实的，组合不是。**
最后你只发表成功的那一个，那就是 selective reporting——
和 p-hacking 的区别只在于粒度。

### 危险三：动态预算重分配是自适应决策

"A 中期看起来好，把 B 的预算转给 A" ——这是一个基于中间结果的判断。
如果这个判断规则不是预先写死的，它就是一个自由度，会系统性地偏向"看起来好"的方向。

**本期的全部设计，都是围绕怎么在获得这两个功能的同时不失去前四期建立的保证。**

---

## 1. 新增不变式

| # | 不变式 | 强制手段 |
|---|---|---|
| I28 | 每个 holdout 有跨课题的**全局**开启预算 | 中央 registry + 锁；超限时 `issue_test_token` 拒绝签发 |
| I29 | 跨课题继承的经验必须写进目标契约的 `prior_exposure`，且在冻结时固定 | `freeze()` 校验；之后新增的继承一律拒绝 |
| I30 | 只有 `env_constraint` / `bug_pattern` 类经验可自由跨课题 | 经验分类在写入时确定，跨课题查询按类过滤 |
| I31 | portfolio 内任一契约产出论文时，必须披露兄弟契约及其结局 | checker 新增 C18；`sibling_disclosure` 段落必填 |
| I32 | 预算重分配策略在 portfolio 创建时冻结并哈希 | 与契约同一套 freeze 机制 |
| I33 | 被 kill 的契约不删除，结局必须记录 | `portfolio_members` append-only |
| I34 | 同一 holdout 被多次开启时，论文必须报告次数并应用多重比较校正 | checker C19 |

**I28 是本期的核心。** 前四期保证了"一个课题只开一次测试集"，
本期要保证的是"**一个数据集在你整个研究生涯里只被开有限次**"。

---

## 2. 明确不要做的事

- ❌ **不要**做"智能调度"。看谁有希望就多给资源，是自适应决策，是偏倚源。调度器应该是笨的
- ❌ **不要**让跨课题经验自动注入 prompt。它必须先进契约的 `prior_exposure`，经人工卡点确认
- ❌ **不要**把 `perf_deadend` 类经验开放给未声明的课题
- ❌ **不要**在 portfolio 里删除失败的契约，哪怕它"明显是浪费"
- ❌ **不要**为了并行方便把多个契约的 runs 写进同一个 DB
- ❌ **不要**在 holdout 预算耗尽时提供 override 开关。耗尽了就是耗尽了，去找新数据集或新划分

---

## 3. 目录新增

```
research-agent/
├── registry/                    # 中央注册表，跨课题唯一共享的东西
│   ├── __init__.py
│   ├── holdout.py               # 数据集级 holdout 预算
│   ├── lessons.py               # 跨课题经验查询
│   ├── portfolio.py             # portfolio 定义与冻结
│   └── lock.py                  # 文件锁，防并发写坏
├── schedule/
│   ├── __init__.py
│   ├── scheduler.py             # 笨调度器
│   ├── milestone.py             # 里程碑规则执行
│   └── supervisor.py            # 多进程编排
├── schema/
│   ├── registry.sql
│   └── portfolio.sql
└── tests/
    ├── test_holdout_budget.py
    ├── test_cross_lessons.py
    └── test_portfolio.py
```

**存储布局改动：**

```
data/
├── registry.sqlite              # 中央：holdout 预算、portfolio、经验索引
└── contracts/
    ├── <contract_id_1>/
    │   └── runs.sqlite          # 每个契约独立
    └── <contract_id_2>/
        └── runs.sqlite
```

一个契约一个 runs DB。隔离干净、崩溃影响面小、并发写不打架。
中央 registry 用 WAL + 文件锁，写操作短且少。

---

## 4. 数据集级 Holdout 预算（先做这个）

### 4.1 Schema

```sql
CREATE TABLE holdouts (
    holdout_id      TEXT PRIMARY KEY,   -- "bnci2014001:cross_subject:v1"
    dataset_key     TEXT NOT NULL,
    split_protocol  TEXT NOT NULL,
    split_spec_sha  TEXT NOT NULL,      -- 划分本身的指纹，改了就是新 holdout
    max_openings    INTEGER NOT NULL,
    note            TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE holdout_openings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    holdout_id   TEXT NOT NULL REFERENCES holdouts(holdout_id),
    contract_id  TEXT NOT NULL,
    run_id       TEXT NOT NULL,
    opened_at    TEXT NOT NULL
);

CREATE TRIGGER holdout_openings_no_delete BEFORE DELETE ON holdout_openings
BEGIN SELECT RAISE(ABORT, 'holdout openings are permanent'); END;
```

`split_spec_sha` 很重要：**换一个划分就是一个新的 holdout，预算重置**。
这是唯一合法的"再开一次"方式——你确实换了一批没见过的数据。
但换划分必须是有理由的（新增被试、新采集的 session），
不能是"把 test 里的被试重新洗一遍"。加一条 note 字段强制写理由。

### 4.2 API

```python
def register_holdout(dataset_key, split_protocol, split_spec, max_openings, note) -> str: ...

def remaining_openings(holdout_id) -> int: ...

def acquire_opening(holdout_id, contract_id) -> OpeningTicket:
    """加锁。余额为 0 时抛 HoldoutExhausted，不提供 force 参数。"""

def opening_history(holdout_id) -> list[Opening]: ...
```

**改动第一期的 `results.issue_test_token`**：签发前必须先 `acquire_opening()`，
拿不到 ticket 就不签 token。这是把契约级的 I7 提升到全局级。

### 4.3 `max_openings` 该设多少

建议默认 `1`，并且**这个数字应该由你和导师在项目开始时定，写进 note 里**。

如果设 > 1，checker 必须要求论文：
- 报告该 holdout 被开过几次（C19）
- 说明多重比较的处理方式（Bonferroni / Holm / 或明确声明是探索性结果）
- 列出兄弟开启对应的 contract_id

> 大部分情况下你会想设 1，然后在预算耗尽时去 MOABB 里换一个数据集。
> 158 个开放数据集,够你用很久。

---

## 5. 跨课题经验复用

### 5.1 经验分类（决定可见性）

第二期的 `lessons.kind` 从 4 类扩到明确的两组：

| 组 | kind | 跨课题可见 | 说明 |
|---|---|---|---|
| **环境类** | `env_constraint` | 自由 | "该数据集 event code 与文档不符"、"MOABB 某参数默认值是 X" |
| | `bug_pattern` | 自由 | "该库的 seed 设置对 dataloader 不生效" |
| | `tooling` | 自由 | "batch>64 在这张卡上必 OOM" |
| **结果类** | `perf_deadend` | **受限** | "方法 X 在数据集 D 上不 work" |
| | `insight` | **受限** | "性能主要来自预处理而非模型" |

环境类描述的是**世界的事实**，与你的假设无关，共享它不影响任何统计保证。
结果类描述的是**你的实验结果**，共享它等于把 A 的探索结果注入 B 的先验。

第二期写入 lessons 时就要定 kind。如果 Claude Code 之前实现得比较粗，本期先补一次迁移：
让一个人工卡点过一遍历史 lessons 重新分类，不要用 LLM 自动分。

### 5.2 查询 API

```python
def query_lessons(kinds: list[str], dataset_key: str | None = None,
                  exclude_contract: str | None = None) -> list[Lesson]:
    """环境类：随便查。
    结果类：只在 fill_contract / topic_selection 阶段可查，
           且查到的每一条必须进入 ContractDraft.prior_exposure。"""
```

### 5.3 `Contract.prior_exposure`

第一期的 Contract schema 新增字段：

```python
prior_exposure: list[PriorExposure] = []

class PriorExposure(BaseModel):
    lesson_id: int
    source_contract_id: str
    kind: str
    text: str
    acknowledged_at: str
```

**`freeze()` 时固定。冻结之后再想继承任何结果类经验，只能开新版本契约。**

这样预注册仍然是完整的——"我事先知道 A 课题发现 X 不 work"本身就是预注册内容的一部分。
诚实地写下来，比假装不知道要好得多。

`negative.md` 和 `positive.md` 模板新增一段：如果 `prior_exposure` 非空，
Limitations 里必须说明搜索空间是被先验裁剪过的，以及裁剪依据。

### 5.4 新增卡点 `prior_exposure_review`

选题阶段填完契约草案后触发。展示：
- 系统检索到的、与本课题相关的结果类经验
- 每条来自哪个契约
- 逐条选择「继承并声明」/「忽略」

**忽略也要记录。** 忽略意味着你打算重新验证它,这是合法且有价值的选择。

---

## 6. Portfolio 与调度

### 6.1 Schema

```sql
CREATE TABLE portfolios (
    portfolio_id     TEXT PRIMARY KEY,
    policy_json      TEXT NOT NULL,      -- 见 6.2
    policy_hash      TEXT NOT NULL,
    total_gpu_hours  REAL NOT NULL,
    total_usd        REAL NOT NULL,
    frozen_at        TEXT NOT NULL,
    created_at       TEXT NOT NULL
);

CREATE TABLE portfolio_members (
    portfolio_id  TEXT NOT NULL REFERENCES portfolios(portfolio_id),
    contract_id   TEXT NOT NULL,
    initial_gpu_hours REAL NOT NULL,
    joined_at     TEXT NOT NULL,
    PRIMARY KEY (portfolio_id, contract_id)
);

CREATE TABLE member_events (          -- append-only，记录每次分配变更与结局
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id TEXT NOT NULL,
    contract_id  TEXT NOT NULL,
    event        TEXT NOT NULL,       -- allocated|killed|exhausted|confirmed|negative|inconclusive
    delta_gpu_hours REAL,
    reason       TEXT NOT NULL,
    milestone_id TEXT,
    created_at   TEXT NOT NULL
);
CREATE TRIGGER member_events_no_delete BEFORE DELETE ON member_events
BEGIN SELECT RAISE(ABORT, 'portfolio history is permanent'); END;
```

### 6.2 冻结的分配策略

```yaml
# portfolio.yaml — 创建时冻结并哈希，之后不可改
portfolio_id: bci-2026-q3
total_gpu_hours: 400
total_usd: 600
initial_allocation: equal        # equal | weighted | manual
milestones:
  - id: m1
    at_fraction: 0.30            # 各成员消耗自身初始预算的 30% 时触发
    rule: kill_bottom_k
    k: 1
    criterion: best_dev_score    # 判据必须在这里写死
    redistribute: equal_among_survivors
  - id: m2
    at_fraction: 0.65
    rule: kill_bottom_k
    k: 1
    criterion: best_dev_score
    redistribute: equal_among_survivors
```

**这就是临床试验的 pre-specified interim analysis。**
"根据中期结果调整投入"从一个临场判断，变成一个预注册的规则。
判据、时点、动作全部写死，执行时没有自由度。

`milestone.py` 执行时：读策略 → 算判据 → 执行动作 → 写 `member_events` → 继续。
**没有人工介入的余地，也不需要。** 如果你想推翻规则，只能开新 portfolio，
而旧 portfolio 的历史永久保留。

### 6.3 调度器

```python
class Scheduler:
    def next_slot(self) -> Slot | None:
        """轮转 + 剩余预算加权。就这么简单。
        不看 dev_score，不看趋势，不做任何"谁有希望"的判断。
        智能调度 = 自适应 = 偏倚。"""
```

`supervisor.py` 负责多进程：每个契约一个子进程，各自跑第二期的 `run_loop`，
共用一个 GPU 信号量。中央 registry 的写操作走 `registry/lock.py` 的文件锁。

### 6.4 兄弟披露（I31）

`writing/disclosure.py` 新增段落，checker C18 校验：

```markdown
## 同期研究声明

本工作属于 portfolio `bci-2026-q3`，该 portfolio 同期包含 3 个研究契约，
共享 400 GPU 小时预算，分配策略于 2026-XX-XX 冻结（policy_hash: …）。

| 契约 | 状态 | 结局 |
| c_a1b2 (本文) | 完成 | confirmed |
| c_c3d4 | 于里程碑 m1 终止 | killed |
| c_e5f6 | 预算耗尽 | inconclusive |

使用的 holdout `bnci2014001:cross_subject:v1` 在本 portfolio 内被开启 1 次。
```

这段是自动生成的，从 `member_events` 和 `holdout_openings` 里读，agent 碰不到。

> 这段话会让审稿人看到你还试过另外两个方向且都失败了。
> 这**不是**在削弱你的论文——它是在告诉读者这个结果不是从 20 次尝试里挑出来的那次。
> 在一个大量结果无法复现的领域里，这段话增加而不是减少你的可信度。

---

## 7. 测试

三个验收核心：

```python
def test_holdout_exhausted_blocks_token_issuance():
    """max_openings=1 的 holdout 被 A 开过之后，
    B 契约的 issue_test_token 必须失败，且没有任何 force / override 路径。
    用 ast 遍历 registry/holdout.py 断言不存在绕过分支。"""

def test_perf_lesson_requires_prior_exposure_declaration():
    """从 A 契约取一条 perf_deadend，在 B 的选题阶段使用它。
    断言：B 的 ContractDraft.prior_exposure 里必须出现这条，
    否则 freeze() 失败。"""

def test_paper_without_sibling_disclosure_fails_check():
    """portfolio 成员的论文缺少同期研究声明段落 → checker FAIL。"""
```

其余：

```python
# holdout
test_new_split_spec_creates_new_holdout()
test_holdout_openings_append_only()
test_multi_opening_requires_correction_statement()

# cross lessons
test_env_lessons_freely_queryable()
test_perf_lessons_blocked_outside_ideation()
test_prior_exposure_frozen_with_contract()
test_ignored_lesson_recorded_in_audit()

# portfolio
test_policy_frozen_and_hashed()
test_milestone_rule_has_no_manual_override()
test_killed_contract_not_deleted()
test_scheduler_ignores_dev_score()      # 断言 next_slot 不读 dev_score
test_concurrent_writes_do_not_corrupt_registry()
```

`test_scheduler_ignores_dev_score` 用 ast 断言 `Scheduler` 的实现里
根本没有引用 `dev_score` 或任何性能字段。笨是设计目标，要能验证。

---

## 8. 分阶段执行

**Phase 22 — Holdout 预算**
`registry/holdout.py` + `registry/lock.py` + 改造 `issue_test_token`。
先做这个，它独立于其余功能且价值最高——**即使你永远不做多契约并行，
这个模块单独用也值得**。
验收：`test_holdout_budget.py` 全绿；已有的 P1–P4 测试仍全绿。

**Phase 23 — 存储布局迁移**
一契约一 DB + 中央 registry。写一个迁移脚本处理已有数据。
验收：老契约能在新布局下 `make reproduce` 通过。

**Phase 24 — 经验分类与跨课题查询**
lessons 重分类（人工过一遍）+ `registry/lessons.py` + `Contract.prior_exposure` +
`prior_exposure_review` 卡点。
验收：`test_cross_lessons.py` 全绿。

**Phase 25 — Portfolio**
`registry/portfolio.py` + `schedule/`。
先做单进程模拟（假装并行，实际串行），把状态机和里程碑逻辑验对。
验收：`test_portfolio.py` 全绿。

**Phase 26 — 真并行**
`schedule/supervisor.py` 多进程 + GPU 信号量 + 并发写测试。
验收：3 个契约真并行跑一次小规模搜索（每个 10 节点），registry 无损坏，
兄弟披露段落正确生成。

---

## 9. 给 Claude Code 的提示词

> 读 IMPLEMENTATION-P5.md，前置 P1–P4 已完成。按 §8 从 Phase 22 开始。
>
> 五条硬约束：
> 1. `acquire_opening()` 没有 force / override / admin 参数。耗尽就是耗尽
> 2. 调度器不读任何性能指标。写完后自己用 ast 验一遍
> 3. 里程碑规则执行时没有人工介入点。想改规则只能开新 portfolio
> 4. 结果类经验跨课题使用必须落到 `prior_exposure`，冻结时固定
> 5. 一契约一个 runs DB。不要为了并行方便合并
>
> §0 解释了这三个功能为什么危险。如果某处实现让你觉得"加个开关会方便很多"——
> 那个开关十有八九就是本期在防的东西。先问我。

---

## 10. 什么时候不该用这些功能

诚实地说，大部分情况下你不需要它们：

**跨课题经验复用**：只有当你在同一个数据集上做第三个、第四个课题时才有价值。
前两个课题的时候，你自己记得住。过早引入它，你会付出 `prior_exposure` 的声明成本
却拿不到任何节省。

**多契约并行**：只有当你的 GPU 明显闲置、且你手上确实有 3 个以上想认真做的方向时才划算。
如果只有 2 个，串行做完第一个再做第二个更简单，也更容易保持专注。

**但 holdout 预算（Phase 22）例外——这个应该尽早做，而且即使只有一个课题也该做。**
它挡住的是最隐蔽也最常见的一种自欺：
在同一个测试集上反复试探，每次都觉得"这次是新想法所以不算"。

它是这一期里唯一一个我会建议你无论如何都装上的东西。
