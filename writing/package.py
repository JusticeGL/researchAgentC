"""可复现交付包。见 IMPLEMENTATION-P3.md §6 Phase 16。

`make package` 产出一个目录，内含：
  - 冻结契约
  - 渲染后的论文 + provenance
  - AI_CONTRIBUTION.md
  - figures/ 的 manifest + out.pdf
  - 复现说明（README）

干净机器上应能 `make reproduce`（至少能跑 smoke / figures-check）。
本模块只做打包，不重跑实验。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent


def package(out_dir: Path, *,
            contract_path: Path,
            paper_build_dir: Path,
            audit_db: Optional[Path] = None,
            results_path: Optional[Path] = None,
            agent_model: str = "（未指定）",
            include_figures: bool = True) -> Path:
    """组装交付包，返回包目录路径。"""
    from core.contract import Contract
    from writing import disclosure

    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    contract = Contract.load(Path(contract_path))
    # 契约
    contracts = out_dir / "contracts"
    contracts.mkdir()
    shutil.copy2(contract_path, contracts / Path(contract_path).name)

    # 论文 build
    paper_dst = out_dir / "build"
    if Path(paper_build_dir).exists():
        shutil.copytree(paper_build_dir, paper_dst)
    else:
        paper_dst.mkdir()

    # 披露
    disclosure.generate(
        contract,
        audit_db=Path(audit_db) if audit_db else (REPO_ROOT / "data" / "audit.sqlite"),
        results_path=results_path,
        agent_model=agent_model,
        out_path=out_dir / "AI_CONTRIBUTION.md")

    # 图
    if include_figures:
        fig_src = REPO_ROOT / "figures"
        fig_dst = out_dir / "figures"
        fig_dst.mkdir()
        for d in sorted(fig_src.glob("fig_*")):
            if d.is_dir():
                shutil.copytree(d, fig_dst / d.name,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        # 复制 _lib.py 以便包内可重跑
        for name in ("_lib.py", "__init__.py", "__main__.py"):
            src = fig_src / name
            if src.exists():
                shutil.copy2(src, fig_dst / name)

    # 复现说明
    readme = (
        "# 复现包\n\n"
        f"- contract: `{contract.contract_id}` v{contract.version}\n"
        f"- content_hash: `{contract.content_hash or contract.content_hash_value()}`\n\n"
        "## 复现步骤\n\n"
        "```bash\n"
        "make figures-check   # I19：图 byte-identical\n"
        "make check           # 确定性检查器\n"
        "make reproduce       # smoke e2e（需数据时按 CLAUDE.md）\n"
        "```\n\n"
        "详见 `AI_CONTRIBUTION.md`。\n"
    )
    (out_dir / "README.md").write_text(readme, encoding="utf-8")

    # 清单
    manifest = {
        "contract_id": contract.contract_id,
        "contract_hash": contract.content_hash or contract.content_hash_value(),
        "has_paper_build": (paper_dst / "provenance.json").exists(),
        "has_disclosure": (out_dir / "AI_CONTRIBUTION.md").exists(),
        "has_figures": include_figures,
    }
    (out_dir / "package_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_dir


def _main(argv=None) -> int:  # pragma: no cover - CLI
    import argparse

    p = argparse.ArgumentParser(description="生成可复现交付包")
    p.add_argument("--out", default="dist/package", help="输出目录")
    p.add_argument("--contract", required=False,
                   help="冻结契约 JSON；缺省取 contracts/ 下最新")
    p.add_argument("--build", default="build", help="论文渲染输出目录")
    p.add_argument("--audit", default="data/audit.sqlite")
    p.add_argument("--results", default="data/results.sqlite")
    p.add_argument("--model", default="（未指定）")
    args = p.parse_args(argv)

    contract = Path(args.contract) if args.contract else None
    if contract is None:
        cdir = REPO_ROOT / "contracts"
        cands = sorted(cdir.glob("*.json")) if cdir.exists() else []
        if not cands:
            print("未找到契约；请 --contract 指定")
            return 2
        contract = cands[-1]

    out = package(Path(args.out), contract_path=contract,
                  paper_build_dir=Path(args.build),
                  audit_db=Path(args.audit) if Path(args.audit).exists() else None,
                  results_path=Path(args.results) if Path(args.results).exists() else None,
                  agent_model=args.model)
    print(f"交付包已生成：{out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys
    raise SystemExit(_main(sys.argv[1:]))
