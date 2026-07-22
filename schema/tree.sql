-- 实验树。见 IMPLEMENTATION-P2.md §4.2。
-- I10：每个树节点至少对应一条 run 记录。run_ids 是 JSON list，SQLite 无法对它做外键，
--      因此"非空且每个 run_id 都在 runs 里"必须在应用层（loop/tree.py）写入时强制。
-- 树 append-only（只增不改结构；status/debug_attempts 允许 UPDATE，禁止 DELETE）。

CREATE TABLE IF NOT EXISTS tree_nodes (
    node_id            TEXT PRIMARY KEY,
    contract_id        TEXT NOT NULL,
    parent_node_id     TEXT,
    change_description TEXT NOT NULL,     -- 一句话，用于去重
    change_embedding   BLOB,              -- 句向量（sentence-transformers 或离线回退）
    patch              TEXT NOT NULL,     -- 相对父节点的 diff
    run_ids            TEXT NOT NULL,     -- JSON list，至少一个
    status             TEXT NOT NULL,     -- ok|buggy|abandoned|fluke|confirmed
    debug_attempts     INTEGER NOT NULL DEFAULT 0,
    expansion_count    INTEGER NOT NULL DEFAULT 0,   -- 供 UCB 探索项
    dev_score          REAL,              -- 仅 dev，用于搜索排序；永不进论文
    created_at         TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS tree_nodes_no_delete BEFORE DELETE ON tree_nodes
BEGIN SELECT RAISE(ABORT, 'tree is append-only'); END;
