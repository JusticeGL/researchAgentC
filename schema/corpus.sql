-- 文献库 schema。见 IMPLEMENTATION.md §5.5。
-- 不变式 I5：入库文献必须有 DOI 或 arXiv ID（trigger 强制）。

CREATE TABLE IF NOT EXISTS papers (
    key           TEXT PRIMARY KEY,   -- bibtex key, e.g. lawhern2018eegnet
    doi           TEXT,
    arxiv_id      TEXT,
    title         TEXT NOT NULL,
    authors       TEXT NOT NULL,
    year          INTEGER NOT NULL,
    venue         TEXT,
    abstract      TEXT,
    oa_status     TEXT,               -- open|closed|unknown
    fulltext_path TEXT,
    retrieved_at  TEXT NOT NULL,
    query         TEXT
);

CREATE TABLE IF NOT EXISTS claim_support (
    claim_hash TEXT NOT NULL,
    key        TEXT NOT NULL REFERENCES papers(key),
    verdict    TEXT NOT NULL,         -- supported|partial|unsupported|unverifiable
    evidence   TEXT,
    checked_at TEXT NOT NULL,
    PRIMARY KEY (claim_hash, key)
);

-- I5：没有 DOI 也没有 arXiv ID 的文献拒绝入库
CREATE TRIGGER IF NOT EXISTS papers_require_id BEFORE INSERT ON papers
WHEN NEW.doi IS NULL AND NEW.arxiv_id IS NULL
BEGIN SELECT RAISE(ABORT, 'paper must have doi or arxiv_id'); END;
