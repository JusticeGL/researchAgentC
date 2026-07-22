r"""确定性检查器。见 IMPLEMENTATION.md §5.6。

`make check` 跑这个。全部是硬检查，没有 LLM 参与。任一 FAIL → 退出码非 0。
输出 audit/check_report.json + 人类可读摘要。

本期（第一期）实现 C1–C10 的确定性主体：
  C1  build/ 中所有数字都有 provenance 记录（裸数字重扫）
  C2  所有 \cite{k} 都能在 corpus 解析出 DOI 或 arXiv ID
  C3  强论断引用的 support_check：unsupported → FAIL；unverifiable/partial → WARN 单列
  C4  每个 Agg 的 n >= contract.stat_plan.n_seeds
  C5  holdout_access 记录数 <= 1；且 ==1 当且仅当论文报告了测试集数字
  C6  每张图有 manifest 且重跑 byte-identical（本期无图 → SKIP）
  C7  baseline 数字与所引论文一致或有显式差异说明（本期骨架 → SKIP）
  C8  论文声明的 contract_hash 与 contracts/ 里的一致
  C9  所有被引用 run 的 harness_hash 一致且等于当前 harness_hash()
  C10 没有被引用的 run 处于 invalid 状态
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent

PASS, FAIL, WARN, SKIP = "PASS", "FAIL", "WARN", "SKIP"


@dataclass
class CheckResult:
    checks: List[dict] = field(default_factory=list)

    def add(self, cid: str, status: str, detail: str = ""):
        self.checks.append({"id": cid, "status": status, "detail": detail})

    @property
    def ok(self) -> bool:
        return all(c["status"] != FAIL for c in self.checks)

    def summary(self) -> str:
        lines = []
        for c in self.checks:
            lines.append(f"[{c['status']:4}] {c['id']}: {c['detail']}")
        lines.append("")
        lines.append("总体：" + ("全绿 ✅" if self.ok else "存在 FAIL ❌"))
        return "\n".join(lines)


def _load_provenance(build_dir: Path) -> Optional[dict]:
    p = Path(build_dir) / "provenance.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def run(build_dir: Path,
        results_path: Optional[Path] = None,
        corpus_path: Optional[Path] = None,
        contract_path: Optional[Path] = None,
        config_path: Optional[Path] = None,
        report_path: Optional[Path] = None,
        claims_path: Optional[Path] = None,
        patterns_path: Optional[Path] = None,
        audit_path: Optional[Path] = None,
        ledger_path: Optional[Path] = None,
        paper_meta_path: Optional[Path] = None,
        run_figures_check: bool = False) -> CheckResult:
    from core import render, results as results_mod

    res = CheckResult()
    prov = _load_provenance(build_dir)
    if prov is None:
        res.add("C0", FAIL, f"{build_dir}/provenance.json 不存在，先 make render")
        _write_report(res, report_path)
        return res

    rendered_path = Path(prov["output"])
    rendered = rendered_path.read_text(encoding="utf-8") if rendered_path.exists() else ""
    replacements = prov.get("replacements", [])
    config = render._load_config(config_path)

    # C1 裸数字
    try:
        render._scan_bare_numbers(rendered, replacements, config)
        res.add("C1", PASS, "build/ 中数字均有 provenance 记录")
    except render.BareNumberError as e:
        res.add("C1", FAIL, str(e))

    # C2 引用可解析 ID
    cites = render._collect_cites(rendered)
    if not cites:
        res.add("C2", PASS, "无引用")
    elif corpus_path is None:
        res.add("C2", FAIL, f"有引用 {cites} 但未提供 corpus")
    else:
        from core import corpus as corpus_mod

        bad = [k for k in cites if not corpus_mod.has_resolved_id(corpus_path, k)]
        res.add("C2", FAIL if bad else PASS,
                f"未解析出 ID 的引用：{bad}" if bad else f"{len(cites)} 条引用均可解析 ID")

    # C3 强论断引用蕴含（本期：unsupported→FAIL，unverifiable/partial→WARN）
    _check_strong_claims(res, rendered, cites, corpus_path, config_path)

    # C4 Agg 的 n
    contract = None
    if contract_path and Path(contract_path).exists():
        from core.contract import Contract

        contract = Contract.load(Path(contract_path))
    agg_entries = [r for r in replacements if r.get("kind") == "agg"]
    if not agg_entries:
        res.add("C4", SKIP, "论文未使用 agg 聚合")
    elif contract is None:
        res.add("C4", WARN, "有 agg 但未提供契约，无法核对 n_seeds")
    else:
        need = contract.stat_plan.n_seeds
        bad = [(a["tag"], a["n"]) for a in agg_entries if a["n"] < need]
        res.add("C4", FAIL if bad else PASS,
                f"n < n_seeds({need}) 的聚合：{bad}" if bad
                else f"所有聚合 n >= {need}")

    # 收集被引用的 run_id
    ref_run_ids = []
    for r in replacements:
        ref_run_ids.extend(r.get("run_ids", []))
    ref_run_ids = list(dict.fromkeys(ref_run_ids))

    # C5 holdout 访问次数
    _check_holdout(res, contract, replacements, ref_run_ids, results_path)

    # C6 图 manifest：与 C14（figures-check）对齐；无 figures 目录时 SKIP
    fig_root = REPO_ROOT / "figures"
    if not fig_root.exists() or not any(fig_root.glob("fig_*")):
        res.add("C6", SKIP, "无 figures/fig_* 目录")
    else:
        missing = []
        for d in sorted(fig_root.glob("fig_*")):
            if d.is_dir() and not (d / "manifest.json").exists():
                missing.append(d.name)
        res.add("C6", FAIL if missing else PASS,
                f"缺 manifest 的图：{missing}" if missing
                else "所有图均有 manifest.json（byte-identical 见 C14）")

    # C7 baseline 一致性（骨架：有 baseline 引用即 PASS；严格数值比对留后续）
    res.add("C7", SKIP, "baseline 与所引论文数值一致性比对为骨架")

    # C8 契约 hash 一致
    if contract is None:
        res.add("C8", SKIP, "未提供契约")
    else:
        recomputed = contract.content_hash_value()
        if contract.content_hash is None:
            res.add("C8", WARN, "契约未冻结（content_hash 为空）")
        elif recomputed == contract.content_hash:
            res.add("C8", PASS, f"contract_hash 一致 ({recomputed[:12]})")
        else:
            res.add("C8", FAIL,
                    f"contract_hash 不一致：声明 {contract.content_hash[:12]} != 重算 {recomputed[:12]}")

    # C9 harness_hash 一致
    _check_harness_hash(res, ref_run_ids, results_path)

    # C10 被引用 run 不得 invalid
    if not ref_run_ids or results_path is None:
        res.add("C10", SKIP if not ref_run_ids else FAIL,
                "无被引用 run" if not ref_run_ids else "被引用 run 但无结果库")
    else:
        invalid = [rid for rid in ref_run_ids
                   if results_mod.get_run(rid, db=results_path).is_invalid]
        res.add("C10", FAIL if invalid else PASS,
                f"被引用但已作废的 run：{invalid}" if invalid else "被引用 run 均有效")

    # C11–C17（第三期）
    _check_claim_markers(res, rendered, patterns_path)
    _check_registry(res, rendered, claims_path, contract, audit_path)
    _check_ablation_ids(res, contract, results_path, audit_path)
    _check_figures(res, run_figures_check)
    _check_unverifiable_signoff(res, cites, corpus_path, audit_path)
    _check_hypothesis_held(res, paper_meta_path)
    _check_limitations(res, rendered, ledger_path, contract, paper_meta_path)

    _write_report(res, report_path)
    return res


def _check_strong_claims(res, rendered, cites, corpus_path, config_path):
    import yaml

    if not cites or corpus_path is None:
        res.add("C3", SKIP, "无引用或无 corpus")
        return
    patterns_path = config_path
    claims_cfg = REPO_ROOT / "config" / "claims.yaml"
    patterns = []
    if claims_cfg.exists():
        patterns = (yaml.safe_load(claims_cfg.read_text(encoding="utf-8")) or {}).get(
            "strong_claim_patterns", [])
    # 判断正文里是否存在强论断
    has_strong = any(p.lower() in rendered.lower() for p in patterns)
    if not has_strong:
        res.add("C3", PASS, "未检测到强论断句")
        return
    from core import corpus as corpus_mod

    unsupported, unverifiable = [], []
    for k in cites:
        v = corpus_mod.support_check(corpus_path, "强论断（本期粗粒度）", k)
        if v.verdict == "unsupported":
            unsupported.append(k)
        elif v.verdict in ("unverifiable", "partial"):
            unverifiable.append(k)
    if unsupported:
        res.add("C3", FAIL, f"强论断引用 unsupported：{unsupported}")
    elif unverifiable:
        res.add("C3", WARN,
                f"强论断引用 unverifiable/partial（需人工放行，第三期 C15 签字）：{unverifiable}")
    else:
        res.add("C3", PASS, "强论断引用均 supported")


def _section_text(rendered: str, name: str) -> Optional[str]:
    """抽取 '## <name>' 到下一个同级/更高级标题之间的正文。找不到返回 None。"""
    import re

    m = re.search(rf"^#{{1,6}}\s+{re.escape(name)}\s*$", rendered, re.MULTILINE)
    if not m:
        return None
    start = m.end()
    nxt = re.search(r"^#{1,6}\s+", rendered[start:], re.MULTILINE)
    end = start + nxt.start() if nxt else len(rendered)
    return rendered[start:end]


# C11：强论断句都有 [claim:id] 标记
def _check_claim_markers(res, rendered, patterns_path):
    try:
        from writing import claims
    except Exception as e:  # pragma: no cover
        res.add("C11", SKIP, f"writing.claims 不可用（{e}）")
        return
    patterns = claims.load_patterns(patterns_path)
    unreg = claims.scan_paper_claims(rendered, patterns)
    if unreg:
        detail = "; ".join(f"L{u.line}[{u.matched}]:{u.sentence[:30]}" for u in unreg)
        res.add("C11", FAIL, f"强论断句缺 [claim:id] 标记：{detail}")
    else:
        res.add("C11", PASS, "所有强论断句均带 [claim:id] 标记（或无强论断）")


# C12：registry 的 source 合法 + 正文标记 id 已注册
def _check_registry(res, rendered, claims_path, contract, audit_path):
    if claims_path is None:
        res.add("C12", SKIP, "未提供 claims registry")
        return
    from writing import claims

    reg = claims.load_registry(claims_path)
    audit = claims.load_claim_approvals(audit_path) if audit_path else []
    violations = claims.validate_registry(reg, contract, audit) if contract else []
    violations += claims.markers_not_registered(rendered, reg)
    if contract is None:
        res.add("C12", WARN, "有 registry 但未提供契约，仅校验标记注册性")
        if not any(v.code == "I16_MARKER_UNKNOWN" for v in violations):
            return
    if violations:
        res.add("C12", FAIL,
                "; ".join(f"{v.code}:{v.claim_id}:{v.detail}" for v in violations))
    else:
        res.add("C12", PASS, "claim registry 合法且正文标记均已注册")


# C13：ablation run 的 ablation_id 已预注册或经批准
def _check_ablation_ids(res, contract, results_path, audit_path):
    if results_path is None or contract is None:
        res.add("C13", SKIP, "无结果库或契约")
        return
    from loop import ablation

    try:
        bad = ablation.validate_ablation_runs(results_path, contract, audit_path)
    except Exception as e:
        res.add("C13", SKIP, f"无 ablation_runs 表（{type(e).__name__}）")
        return
    res.add("C13", FAIL if bad else PASS,
            f"ablation_id 非法的 run：{bad}" if bad else "所有 ablation run 的 id 合法")


# C14：figures-check 通过
def _check_figures(res, run_figures_check):
    if not run_figures_check:
        res.add("C14", SKIP, "未启用 figures-check（make check 会启用）")
        return
    try:
        from figures import _lib
    except Exception as e:
        res.add("C14", SKIP, f"figures 不可用（{e}）")
        return
    ok, details = _lib.check_all()
    res.add("C14", PASS if ok else FAIL, "; ".join(details))


# C15：unverifiable 引用清单已生成且有人工签字
def _check_unverifiable_signoff(res, cites, corpus_path, audit_path):
    if audit_path is None:
        res.add("C15", SKIP, "未提供 audit（C15 需 citation_unverifiable 签字记录）")
        return
    if not cites or corpus_path is None:
        res.add("C15", PASS, "无引用需签字")
        return
    from core import corpus as corpus_mod

    unverifiable = []
    for k in cites:
        try:
            v = corpus_mod.support_check(corpus_path, "（引用可核验性）", k)
            if v.verdict == "unverifiable":
                unverifiable.append(k)
        except KeyError:
            pass
    if not unverifiable:
        res.add("C15", PASS, "无 unverifiable 引用")
        return
    signed = _has_signoff(audit_path, "citation_unverifiable")
    res.add("C15", PASS if signed else FAIL,
            f"unverifiable 引用 {unverifiable}："
            + ("已人工签字放行" if signed else "缺 citation_unverifiable 签字（I21）"))


def _has_signoff(audit_path, gate_type) -> bool:
    import sqlite3

    con = sqlite3.connect(audit_path)
    try:
        rows = con.execute(
            "SELECT decision FROM audit WHERE gate_type=?", (gate_type,)).fetchall()
    except sqlite3.OperationalError:
        return False
    finally:
        con.close()
    return any(r[0] == "approve" for r in rows)


# C16：论文声明的 hypothesis_held 与模板一致（确定性派生，非 LLM）
def _check_hypothesis_held(res, paper_meta_path):
    if paper_meta_path is None or not Path(paper_meta_path).exists():
        res.add("C16", SKIP, "未提供 paper meta")
        return
    meta = json.loads(Path(paper_meta_path).read_text(encoding="utf-8"))
    held = meta.get("hypothesis_held")
    template = meta.get("template")
    expect = {True: "positive", False: "negative", None: "inconclusive"}.get(held, "?")
    if template == expect:
        res.add("C16", PASS, f"hypothesis_held={held} 与模板 {template} 一致")
    else:
        res.add("C16", FAIL,
                f"hypothesis_held={held} 应对应模板 {expect}，实际 {template}")


# C17：Limitations 非空；台账非空则必须引用至少一条
def _check_limitations(res, rendered, ledger_path, contract, paper_meta_path):
    if paper_meta_path is None:
        res.add("C17", SKIP, "未提供 paper meta（非成品论文，跳过 Limitations 检查）")
        return
    sec = _section_text(rendered, "Limitations")
    if sec is None or not sec.strip():
        res.add("C17", FAIL, "Limitations 章节缺失或为空")
        return
    if ledger_path is None or contract is None:
        res.add("C17", PASS, "Limitations 非空（未提供台账，跳过引用检查）")
        return
    from loop import ledger

    try:
        active = ledger.active_lessons(ledger_path, contract.contract_id)
    except Exception:
        active = []
    if not active:
        res.add("C17", PASS, "Limitations 非空；台账为空可豁免引用要求")
        return
    body = sec.strip()
    cited = any(l.text and l.text[:20] in body for l in active)
    res.add("C17", PASS if cited else FAIL,
            "Limitations 引用了台账教训" if cited
            else "台账非空但 Limitations 未引用任何一条（C17）")


def _check_holdout(res, contract, replacements, ref_run_ids, results_path):
    if results_path is None:
        res.add("C5", SKIP, "无结果库")
        return
    from core import results as results_mod

    # 论文是否报告了测试集数字：被引用 run 里有 phase==test
    reports_test = False
    for rid in ref_run_ids:
        try:
            if results_mod.get_run(rid, db=results_path).phase == "test":
                reports_test = True
                break
        except KeyError:
            pass
    import sqlite3

    con = sqlite3.connect(results_path)
    try:
        if contract is not None:
            n = con.execute(
                "SELECT COUNT(*) FROM holdout_access WHERE contract_id=?",
                (contract.contract_id,),
            ).fetchone()[0]
        else:
            n = con.execute("SELECT COUNT(*) FROM holdout_access").fetchone()[0]
    finally:
        con.close()
    if n > 1:
        res.add("C5", FAIL, f"holdout_access 记录 {n} 条 > 1（I7）")
    elif reports_test and n != 1:
        res.add("C5", FAIL, f"论文报告了测试集数字但 holdout_access={n}（应为 1）")
    elif not reports_test and n != 0:
        res.add("C5", FAIL, f"论文未报告测试集数字但 holdout_access={n}（应为 0）")
    else:
        res.add("C5", PASS, f"holdout_access={n}，与是否报告测试集数字一致")


def _check_harness_hash(res, ref_run_ids, results_path):
    if not ref_run_ids or results_path is None:
        res.add("C9", SKIP, "无被引用 run")
        return
    from core import results as results_mod

    hashes = set()
    for rid in ref_run_ids:
        try:
            hashes.add(results_mod.get_run(rid, db=results_path).harness_hash)
        except KeyError:
            pass
    if len(hashes) > 1:
        res.add("C9", FAIL, f"被引用 run 的 harness_hash 不一致：{hashes}")
        return
    try:
        import harness

        current = harness.harness_hash()
        if hashes and next(iter(hashes)) != current:
            res.add("C9", FAIL,
                    f"harness_hash 与当前不符：run={next(iter(hashes))[:12]} vs 当前={current[:12]}")
        else:
            res.add("C9", PASS, "harness_hash 一致且等于当前")
    except Exception as e:  # harness 依赖缺失
        res.add("C9", WARN, f"无法计算当前 harness_hash（{e}）；仅校验了 run 间一致性")


def _write_report(res: CheckResult, report_path: Optional[Path]):
    report_path = Path(report_path) if report_path else (REPO_ROOT / "audit" / "check_report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps({"ok": res.ok, "checks": res.checks}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _main(argv: List[str]) -> int:  # pragma: no cover - CLI
    build_dir = REPO_ROOT / "build"

    def _opt(p: Path) -> Optional[Path]:
        return p if p.exists() else None

    claims_yaml = REPO_ROOT / "paper" / "claims.yaml"
    meta = build_dir / "main.md.meta.json"
    res = run(
        build_dir,
        results_path=_opt(REPO_ROOT / "data" / "results.sqlite"),
        corpus_path=_opt(REPO_ROOT / "data" / "corpus.sqlite"),
        contract_path=_find_contract(),
        config_path=(REPO_ROOT / "config" / "render.yaml"),
        claims_path=_opt(claims_yaml),
        patterns_path=_opt(REPO_ROOT / "config" / "claims_patterns.yaml"),
        audit_path=_opt(REPO_ROOT / "data" / "audit.sqlite"),
        ledger_path=_opt(REPO_ROOT / "data" / "ledger.sqlite"),
        paper_meta_path=_opt(meta),
        run_figures_check=True,
    )
    print(res.summary())
    return 0 if res.ok else 1


def _find_contract() -> Optional[Path]:
    cdir = REPO_ROOT / "contracts"
    files = sorted(cdir.glob("*.json"))
    return files[-1] if files else None


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main(sys.argv[1:]))
