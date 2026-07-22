-- Claim registry 持久化。见 IMPLEMENTATION-P3.md §4.1。
-- source_kind 只有三种：contract | ablation | approved。CHECK 约束把"第四种"
-- （如"我从结果里看出来的"）在 DB 层面就变成不可表达（I17）。

CREATE TABLE IF NOT EXISTS claims (
    claim_id      TEXT PRIMARY KEY,
    contract_id   TEXT NOT NULL,
    source_kind   TEXT NOT NULL CHECK (source_kind IN ('contract','ablation','approved')),
    source_ref    TEXT NOT NULL,
    evidence      TEXT,                 -- JSON：runs_tag / stat 等
    template      TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending|held|not_held|inconclusive
    registered_at TEXT NOT NULL
);

-- status 由 hypothesis_held 确定性回填，不可手写为结论；这里只约束取值域。
CREATE TRIGGER IF NOT EXISTS claims_status_domain
BEFORE UPDATE OF status ON claims
WHEN NEW.status NOT IN ('pending','held','not_held','inconclusive')
BEGIN SELECT RAISE(ABORT, 'illegal claim status'); END;
