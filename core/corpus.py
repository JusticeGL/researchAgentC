"""文献库。见 IMPLEMENTATION.md §5.5。

不变式 I5：入库文献必须有 DOI 或 arXiv ID（SQLite trigger 强制，解析失败即拒绝）。

search() 走 OpenAlex/Crossref 解析 ID（免 key 的公共 API）。
PaperQA2 开源版不含 Grobid 全文解析、非本地全文检索和引用遍历，
故 support_check 对 oa_status != "open" 的文献只能返回 unverifiable —— 这是预期行为。
unverifiable 的数量必须在 checker 报告里单列，由人决定是否放行。

联网失败时 search 抛异常；离线场景请用 add_paper 直接入库（仍受 I5 约束）。
"""
from __future__ import annotations

import re
import sqlite3
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

_SCHEMA = Path(__file__).resolve().parent.parent / "schema" / "corpus.sql"

Paper = namedtuple(
    "Paper",
    ["key", "doi", "arxiv_id", "title", "authors", "year", "venue",
     "abstract", "oa_status", "fulltext_path"],
)
Verdict = namedtuple("Verdict", ["verdict", "evidence"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db(path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(_SCHEMA.read_text(encoding="utf-8"))
    con.commit()
    con.close()


def _connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def add_paper(db: Path, key: str, title: str, authors: str, year: int,
              doi: Optional[str] = None, arxiv_id: Optional[str] = None,
              venue: Optional[str] = None, abstract: Optional[str] = None,
              oa_status: str = "unknown", fulltext_path: Optional[str] = None,
              query: Optional[str] = None) -> str:
    """直接入库一条文献。I5：doi 与 arxiv_id 全空 → trigger ABORT。"""
    con = _connect(db)
    try:
        con.execute(
            "INSERT INTO papers (key, doi, arxiv_id, title, authors, year, venue, "
            "abstract, oa_status, fulltext_path, retrieved_at, query) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (key, doi, arxiv_id, title, authors, year, venue, abstract,
             oa_status, fulltext_path, _now(), query),
        )
        con.commit()
    finally:
        con.close()
    return key


def get(db: Path, key: str) -> Paper:
    con = _connect(db)
    try:
        row = con.execute("SELECT * FROM papers WHERE key=?", (key,)).fetchone()
        if row is None:
            raise KeyError(f"文献不存在：{key}")
        return Paper(row["key"], row["doi"], row["arxiv_id"], row["title"],
                     row["authors"], row["year"], row["venue"], row["abstract"],
                     row["oa_status"], row["fulltext_path"])
    finally:
        con.close()


def exists(db: Path, key: str) -> bool:
    con = _connect(db)
    try:
        return con.execute("SELECT 1 FROM papers WHERE key=?", (key,)).fetchone() is not None
    finally:
        con.close()


def has_resolved_id(db: Path, key: str) -> bool:
    """I4/C2：该 key 存在且已解析出 DOI 或 arXiv ID。"""
    con = _connect(db)
    try:
        row = con.execute("SELECT doi, arxiv_id FROM papers WHERE key=?", (key,)).fetchone()
        if row is None:
            return False
        return bool(row["doi"]) or bool(row["arxiv_id"])
    finally:
        con.close()


def bibtex(db: Path, keys: List[str]) -> str:
    out = []
    for key in keys:
        p = get(db, key)
        entry_type = "article"
        fields = [f"  title = {{{p.title}}}", f"  author = {{{p.authors}}}",
                  f"  year = {{{p.year}}}"]
        if p.venue:
            fields.append(f"  journal = {{{p.venue}}}")
        if p.doi:
            fields.append(f"  doi = {{{p.doi}}}")
        if p.arxiv_id:
            fields.append(f"  eprint = {{{p.arxiv_id}}}")
        out.append(f"@{entry_type}{{{key},\n" + ",\n".join(fields) + "\n}}")
    return "\n\n".join(out)


# ---------------------------------------------------------------------------
# 检索（联网）
# ---------------------------------------------------------------------------
_ARXIV_RE = re.compile(r"(\d{4}\.\d{4,5})")


def _make_key(authors: str, year: int, title: str) -> str:
    first = re.sub(r"[^a-z]", "", (authors.split(",")[0].split()[-1:] or ["anon"])[0].lower()) or "anon"
    word = re.sub(r"[^a-z0-9]", "", (title.lower().split() or ["paper"])[0])
    return f"{first}{year}{word}"[:40]


def search(db: Path, query: str, k: int = 20, mailto: str = "research-agent@example.org"
           ) -> List[str]:
    """走 OpenAlex 检索，解析出 DOI/arXiv ID 后入库；返回 key 列表。

    只有能解析出 ID 的条目才入库（I5）；解析不出 ID 的直接丢弃，不存"疑似"。
    """
    import requests  # 延迟导入，离线单元测试不触发

    url = "https://api.openalex.org/works"
    params = {"search": query, "per-page": min(k, 50), "mailto": mailto}
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    results = resp.json().get("results", [])

    keys: List[str] = []
    for w in results[:k]:
        doi = (w.get("doi") or "").replace("https://doi.org/", "") or None
        arxiv_id = None
        # OpenAlex 有时把 arXiv 放在 ids / locations 里
        for loc in (w.get("locations") or []):
            landing = (loc.get("landing_page_url") or "")
            m = _ARXIV_RE.search(landing)
            if m:
                arxiv_id = m.group(1)
                break
        if not doi and not arxiv_id:
            continue  # 解析不出 ID，丢弃
        title = w.get("title") or "Untitled"
        authorships = w.get("authorships") or []
        authors = ", ".join(
            a.get("author", {}).get("display_name", "?") for a in authorships[:8]
        ) or "Unknown"
        year = w.get("publication_year") or 0
        venue = ((w.get("primary_location") or {}).get("source") or {}).get("display_name")
        oa = (w.get("open_access") or {})
        oa_status = "open" if oa.get("is_oa") else "closed"
        abstract = _reconstruct_abstract(w.get("abstract_inverted_index"))
        key = _make_key(authors, year, title)
        # 去重：同 key 已存在则跳过
        if exists(db, key):
            keys.append(key)
            continue
        try:
            add_paper(db, key=key, title=title, authors=authors, year=year,
                      doi=doi, arxiv_id=arxiv_id, venue=venue, abstract=abstract,
                      oa_status=oa_status, query=query)
            keys.append(key)
        except sqlite3.IntegrityError:
            continue
    return keys


def _reconstruct_abstract(inv_index) -> Optional[str]:
    if not inv_index:
        return None
    positions = {}
    for word, idxs in inv_index.items():
        for i in idxs:
            positions[i] = word
    return " ".join(positions[i] for i in sorted(positions))


def support_check(db: Path, claim: str, key: str, entail_fn=None,
                  use_cache: bool = True) -> Verdict:
    """引用蕴含检查。见 IMPLEMENTATION-P3.md §4.8。

    1. 取证据文本：oa_status==open 且有全文 → 用全文；否则用 abstract；都没有 → unverifiable。
    2. 一次 LLM 调用（entail_fn）判 supported/partial/unsupported/unverifiable + 证据片段。
       entail_fn(claim, evidence_text) -> (verdict, evidence)。
       **离线/未注入 entail_fn 时保持诚实：验证不了就 unverifiable**，不伪装成 supported。
    3. 结果写 claim_support 表缓存（claim_hash + key）。

    付费墙文献（oa_status != open、无全文）客观上验证不了 → unverifiable，
    由 checker 单列、人工签字放行（C15/I21），不静默当作 supported，也不删句子。
    """
    import hashlib

    VALID = {"supported", "partial", "unsupported", "unverifiable"}
    claim_hash = hashlib.sha256(claim.encode("utf-8")).hexdigest()

    if use_cache:
        cached = _cached_support(db, claim_hash, key)
        if cached is not None:
            return cached

    p = get(db, key)
    evidence_text = None
    if p.oa_status == "open" and p.fulltext_path and Path(p.fulltext_path).exists():
        evidence_text = Path(p.fulltext_path).read_text(encoding="utf-8", errors="ignore")
    elif p.abstract:
        evidence_text = p.abstract

    if evidence_text is None:
        verdict, evidence = "unverifiable", (
            f"oa_status={p.oa_status}，无开放全文也无摘要，无法验证。")
    elif entail_fn is None:
        verdict, evidence = "unverifiable", (
            "未注入蕴含检查器（离线），无法判定；不伪装为 supported。")
    else:
        verdict, evidence = entail_fn(claim, evidence_text)
        if verdict not in VALID:
            raise ValueError(f"entail_fn 返回非法 verdict：{verdict!r}")

    con = _connect(db)
    try:
        con.execute(
            "INSERT OR REPLACE INTO claim_support "
            "(claim_hash, key, verdict, evidence, checked_at) VALUES (?, ?, ?, ?, ?)",
            (claim_hash, key, verdict, evidence, _now()),
        )
        con.commit()
    finally:
        con.close()
    return Verdict(verdict, evidence)


def _cached_support(db: Path, claim_hash: str, key: str) -> Optional[Verdict]:
    con = _connect(db)
    try:
        row = con.execute(
            "SELECT verdict, evidence FROM claim_support WHERE claim_hash=? AND key=?",
            (claim_hash, key)).fetchone()
        return Verdict(row["verdict"], row["evidence"]) if row else None
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()
