-- 选题流水线。见 IMPLEMENTATION-P4.md §4。
-- I22：填不满契约必填字段的想法降级为 incomplete，不进排名（应用层 ideation/fill.py 强制，
--      这里 status 只有 complete|incomplete 两种，DB 兜底）。
-- I27：不计算、不存储、不展示聚合评分 —— axis_scores 的 axis 只能是五个轴之一，
--      结构上无法写入 'overall'/'total' 这类合并分（CHECK 约束强制）。
-- ideas / drafts 只增不改（append-only；status 允许 UPDATE，禁止 DELETE）。

CREATE TABLE IF NOT EXISTS ideas (
    idea_id      TEXT PRIMARY KEY,
    model        TEXT NOT NULL,     -- 产生它的生成器模型
    seed         TEXT,              -- 种子文献 key 或领域描述
    text         TEXT NOT NULL,     -- 原始想法文本
    query_hint   TEXT,              -- 可选：新颖性检索提示
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS idea_drafts (
    idea_id         TEXT PRIMARY KEY,
    status          TEXT NOT NULL CHECK (status IN ('complete', 'incomplete')),
    missing_fields  TEXT NOT NULL,  -- JSON list；complete 时为 []
    draft           TEXT NOT NULL,  -- ContractDraft 的 JSON
    novelty_verdict TEXT,           -- novel|incremental|done|unknown
    created_at      TEXT NOT NULL,
    FOREIGN KEY (idea_id) REFERENCES ideas (idea_id)
);

CREATE TABLE IF NOT EXISTS axis_scores (
    idea_id    TEXT NOT NULL,
    axis       TEXT NOT NULL CHECK (axis IN
                 ('novelty', 'feasibility', 'measurability', 'data_access', 'effect_size')),
    value      REAL,               -- 该轴分数（各轴独立，永不合并）
    label      TEXT,               -- 如 novel|incremental|done / feasible|infeasible
    rationale  TEXT,
    evidence   TEXT,               -- JSON list（如 effect_size 的依据 corpus key）
    created_at TEXT NOT NULL,
    PRIMARY KEY (idea_id, axis),
    FOREIGN KEY (idea_id) REFERENCES ideas (idea_id)
);

CREATE TRIGGER IF NOT EXISTS ideas_no_delete BEFORE DELETE ON ideas
BEGIN SELECT RAISE(ABORT, 'ideas is append-only'); END;

CREATE TRIGGER IF NOT EXISTS idea_drafts_no_delete BEFORE DELETE ON idea_drafts
BEGIN SELECT RAISE(ABORT, 'idea_drafts is append-only'); END;
