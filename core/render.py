r"""模板渲染器。见 IMPLEMENTATION.md §5.2。

这是唯一允许调用 Metric.unwrap / Agg.unwrap 的地方（I3）。
tests/test_invariants.py::test_unwrap_callsites 用 ast 全仓库校验这一点。

渲染流程（顺序不能变）：
  1. 扫描全文所有 \cite{...} / [@...]，逐个查 corpus；任一 key 不存在 → UnknownCitationError，
     不产出任何输出（I4）
  2. 替换所有 {{...}} 模板，来源是结果库；**记录每个替换结果在输出文本里的字符区间（span）**，
     连同 run_id 进 provenance
  3. 裸数字扫描：对配置章节做正则扫描，**排除步骤 2 的替换 span**，
     只在"非替换区域"里找数字；命中未被模板替换的数字 → BareNumberError（I3）
     —— span 排除是这步能工作的前提：替换后的正文里也全是数字，
        不按 span 排除会把刚渲染出来的合法数字误判为裸数字
  4. 写出 build/；build/provenance.json 含每个替换点的 run_id 与 span

支持的模板：
  {{run:<run_id>.<metric>}}            单次 run 的指标
  {{agg:<tag>.<metric>}}               一组 run 的聚合（tag → run_ids 由 tags 映射提供）
  {{agg:<tag>.<metric>|mean±std}}      指定格式
  {{lit:<value>|cite=<key>}}           从文献引用的数字，必须带 cite key
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent


class RenderError(Exception):
    pass


class UnknownCitationError(RenderError):
    pass


class BareNumberError(RenderError):
    pass


_TEMPLATE_RE = re.compile(r"\{\{(run|agg|lit):([^}|]+)(?:\|([^}]*))?\}\}")
_CITE_RE = re.compile(r"\\cite\{([^}]+)\}")
_ATCITE_RE = re.compile(r"\[@([^\]]+)\]")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?%?")


def _fmt_num(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _load_config(config_path: Optional[Path]) -> dict:
    if config_path is None:
        return {"scan_sections": ["Results", "Abstract"], "scan_tables": True,
                "allow_bare": []}
    import yaml

    return yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}


# ---------------------------------------------------------------------------
# 引用收集与校验
# ---------------------------------------------------------------------------
def _collect_cites(text: str) -> List[str]:
    keys: List[str] = []
    for m in _CITE_RE.finditer(text):
        keys.extend(k.strip() for k in m.group(1).split(","))
    for m in _ATCITE_RE.finditer(text):
        keys.extend(k.strip() for k in m.group(1).split(";"))
    # {{lit:...|cite=key}} 里的 cite key 也算
    for m in _TEMPLATE_RE.finditer(text):
        kind, _arg, fmt = m.group(1), m.group(2), m.group(3)
        if kind == "lit" and fmt:
            mm = re.search(r"cite=([^\s,|]+)", fmt)
            if mm:
                keys.append(mm.group(1).strip())
    # 去重保序
    seen, out = set(), []
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _check_citations(text: str, corpus_path: Optional[Path]) -> None:
    cites = _collect_cites(text)
    if not cites:
        return
    if corpus_path is None:
        raise UnknownCitationError(
            f"正文含引用 {cites} 但未提供 corpus，无法校验（I4）"
        )
    from core import corpus

    unknown = [k for k in cites if not corpus.has_resolved_id(corpus_path, k)]
    if unknown:
        raise UnknownCitationError(
            f"以下引用 key 在 corpus 中不存在或未解析出 DOI/arXiv ID（I4）：{unknown}"
        )


# ---------------------------------------------------------------------------
# 模板替换（记录 span）
# ---------------------------------------------------------------------------
def _resolve_template(kind: str, arg: str, fmt: Optional[str],
                      results_path: Optional[Path],
                      tags: Optional[Dict[str, List[str]]]):
    """返回 (替换文本, provenance 记录 dict)。"""
    from core import results

    if kind == "run":
        run_id, metric = arg.split(".", 1)
        m = results.get_metric(run_id.strip(), metric.strip(), db=results_path)
        v = m.unwrap()
        text = _fmt_num(v)
        return text, {"kind": "run", "run_ids": [run_id.strip()],
                      "metric": metric.strip(), "value": v}

    if kind == "agg":
        tag, metric = arg.split(".", 1)
        tag, metric = tag.strip(), metric.strip()
        if not tags or tag not in tags:
            raise RenderError(f"agg 模板的 tag {tag!r} 没有对应 run_ids 映射")
        run_ids = tags[tag]
        a = results.agg(run_ids, metric, db=results_path)
        stats = a.unwrap()
        spec = (fmt or "mean±std").strip()
        if spec in ("mean", "均值"):
            text = _fmt_num(stats.mean)
        elif spec in ("mean±ci", "mean+-ci"):
            half = stats.ci_high - stats.mean
            text = f"{_fmt_num(stats.mean)} ± {_fmt_num(half)}"
        else:  # mean±std（默认）
            text = f"{_fmt_num(stats.mean)} ± {_fmt_num(stats.std)}"
        return text, {"kind": "agg", "tag": tag, "run_ids": list(run_ids),
                      "metric": metric, "mean": stats.mean, "std": stats.std,
                      "n": stats.n, "ci_low": stats.ci_low, "ci_high": stats.ci_high}

    if kind == "lit":
        value = arg.strip()
        cite = None
        if fmt:
            mm = re.search(r"cite=([^\s,|]+)", fmt)
            cite = mm.group(1).strip() if mm else None
        if not cite:
            raise RenderError(f"lit 模板必须带 cite=<key>：{{{{lit:{arg}|...}}}}")
        return value, {"kind": "lit", "value_literal": value, "cite": cite}

    raise RenderError(f"未知模板类型：{kind}")


def _replace_templates(text: str, results_path, tags):
    out_parts: List[str] = []
    spans: List[dict] = []
    pos = 0
    cursor = 0  # 输出文本长度游标
    for m in _TEMPLATE_RE.finditer(text):
        out_parts.append(text[pos:m.start()])
        cursor += m.start() - pos
        repl, prov = _resolve_template(m.group(1), m.group(2), m.group(3),
                                       results_path, tags)
        start = cursor
        out_parts.append(repl)
        cursor += len(repl)
        prov["span"] = [start, cursor]
        prov["template"] = m.group(0)
        spans.append(prov)
        pos = m.end()
    out_parts.append(text[pos:])
    return "".join(out_parts), spans


# ---------------------------------------------------------------------------
# 裸数字扫描
# ---------------------------------------------------------------------------
def _scan_regions(text: str, config: dict) -> List[tuple]:
    regions: List[tuple] = []
    headings = [(m.start(), m.end(), len(m.group(1)), m.group(2).strip())
                for m in _HEADING_RE.finditer(text)]
    wanted = set(s.strip().lower() for s in config.get("scan_sections", []))
    for i, (h_start, h_end, level, title) in enumerate(headings):
        if title.lower() in wanted:
            region_start = h_end
            region_end = len(text)
            for (n_start, _ne, _nl, _nt) in headings[i + 1:]:
                region_end = n_start
                break
            regions.append((region_start, region_end))
    if config.get("scan_tables"):
        for m in re.finditer(r"^[ \t]*\|.*$", text, re.MULTILINE):
            regions.append((m.start(), m.end()))
    return regions


def _allowed_spans(text: str, config: dict) -> List[tuple]:
    spans = []
    for pat in config.get("allow_bare", []):
        try:
            rx = re.compile(pat, re.MULTILINE)
        except re.error:
            continue
        for m in rx.finditer(text):
            spans.append((m.start(), m.end()))
    return spans


def _overlaps(a: tuple, b: tuple) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _comment_spans(text: str) -> List[tuple]:
    """HTML 注释 <!-- ... --> 的区间。注释不会进最终 PDF，故排除在裸数字扫描外。"""
    return [(m.start(), m.end()) for m in _COMMENT_RE.finditer(text)]


def _cite_spans(text: str) -> List[tuple]:
    """\\cite{...} / [@...] 命令的字符区间。引用 key 里的数字（如 ang2012csp）
    不是正文数字，必须排除在裸数字扫描之外。"""
    spans = []
    for m in _CITE_RE.finditer(text):
        spans.append((m.start(), m.end()))
    for m in _ATCITE_RE.finditer(text):
        spans.append((m.start(), m.end()))
    return spans


def _scan_bare_numbers(text: str, replaced_spans: List[dict], config: dict) -> None:
    regions = _scan_regions(text, config)
    if not regions:
        return
    repl = [tuple(s["span"]) for s in replaced_spans]
    allowed = _allowed_spans(text, config) + _cite_spans(text) + _comment_spans(text)
    violations = []
    for (r_start, r_end) in regions:
        for m in _NUMBER_RE.finditer(text, r_start, r_end):
            nspan = (m.start(), m.end())
            if any(_overlaps(nspan, rs) for rs in repl):
                continue  # 是模板替换出来的合法数字
            if any(_overlaps(nspan, a) for a in allowed):
                continue  # 命中白名单
            violations.append((m.group(0), _line_of(text, m.start())))
    if violations:
        detail = "; ".join(f"{v!r}@L{ln}" for v, ln in violations)
        raise BareNumberError(
            f"扫描章节里发现未经模板替换的裸数字（I3）：{detail}。"
            f"数字必须写成 {{{{run:...}}}} / {{{{agg:...}}}} / {{{{lit:...|cite=...}}}} 模板。"
        )


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def render_file(src_path: Path, out_dir: Path,
                corpus_path: Optional[Path] = None,
                results_path: Optional[Path] = None,
                config_path: Optional[Path] = None,
                tags: Optional[Dict[str, List[str]]] = None) -> dict:
    """渲染单个文件。成功则写 out_dir/<name> 与 out_dir/provenance.json，返回 provenance。"""
    src_path = Path(src_path)
    text = src_path.read_text(encoding="utf-8")
    config = _load_config(config_path)

    # 1. 引用校验（失败则不产出任何输出）
    _check_citations(text, corpus_path)

    # 2. 模板替换 + 记录 span
    rendered, spans = _replace_templates(text, results_path, tags)

    # 3. 裸数字扫描（排除替换 span）
    _scan_bare_numbers(rendered, spans, config)

    # 4. 写出
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / src_path.name).write_text(rendered, encoding="utf-8")
    provenance = {
        "source": str(src_path),
        "output": str(out_dir / src_path.name),
        "replacements": spans,
    }
    (out_dir / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return provenance


def _main(argv: List[str]) -> int:  # pragma: no cover - CLI
    paper_dir = REPO_ROOT / "paper"
    build_dir = REPO_ROOT / "build"
    corpus_path = REPO_ROOT / "data" / "corpus.sqlite"
    results_path = REPO_ROOT / "data" / "results.sqlite"
    config_path = REPO_ROOT / "config" / "render.yaml"
    tags_path = paper_dir / "tags.json"
    tags = json.loads(tags_path.read_text(encoding="utf-8")) if tags_path.exists() else None

    sources = sorted(paper_dir.glob("*.md"))
    if not sources:
        print("paper/ 下没有 .md 源文件。")
        return 0
    for src in sources:
        try:
            render_file(src, build_dir,
                        corpus_path=corpus_path if corpus_path.exists() else None,
                        results_path=results_path if results_path.exists() else None,
                        config_path=config_path, tags=tags)
            print(f"渲染 {src.name} → build/{src.name}")
        except RenderError as e:
            print(f"渲染失败 {src.name}: {e}")
            return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main(sys.argv[1:]))
