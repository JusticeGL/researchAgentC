"""统一样式 + 确定性设置 + 图目录构建/校验。见 IMPLEMENTATION-P3.md §4.4。

byte-identical 的关键在 deterministic_setup()：不做这几条重跑就不会一致，I19 无法满足。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
FIG_ROOT = Path(__file__).resolve().parent

_SETUP_DONE = False


def deterministic_setup() -> None:
    """matplotlib 的 byte-identical 输出需要显式处理这几条。"""
    global _SETUP_DONE
    # 去掉 PDF 时间戳：matplotlib 会读取 SOURCE_DATE_EPOCH 作为固定创建时间
    os.environ["SOURCE_DATE_EPOCH"] = "0"
    # 缓存目录固定到仓库内的可写位置（避免 ~/.matplotlib 不可写 + 字体查找顺序不定）
    mplconfig = REPO_ROOT / "build" / ".mplconfig"
    mplconfig.mkdir(parents=True, exist_ok=True)
    os.environ["MPLCONFIGDIR"] = str(mplconfig)

    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import rcParams

    rcParams["svg.hashsalt"] = "fixed"
    rcParams["pdf.compression"] = 0          # 便于 diff
    rcParams["pdf.fonttype"] = 42            # 嵌入 TrueType，跨机稳定
    rcParams["ps.fonttype"] = 42
    # 固定字体：只用 matplotlib 自带的 DejaVu Sans，不依赖系统字体查找顺序
    rcParams["font.family"] = "sans-serif"
    rcParams["font.sans-serif"] = ["DejaVu Sans"]
    rcParams["axes.unicode_minus"] = False
    rcParams["figure.dpi"] = 100
    rcParams["savefig.dpi"] = 100
    rcParams["figure.figsize"] = (6.0, 4.0)
    _SETUP_DONE = True


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def figure_dirs() -> List[Path]:
    return sorted(p for p in FIG_ROOT.glob("fig_*") if p.is_dir())


def _load_plot_module(fig_dir: Path):
    plot_py = fig_dir / "plot.py"
    spec = importlib.util.spec_from_file_location(f"figplot_{fig_dir.name}", plot_py)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 构建 / 校验
# ---------------------------------------------------------------------------
def render_to(fig_dir: Path, out_path: Path) -> None:
    """调用 fig_dir/plot.py 的 render(out_path) 生成 PDF。"""
    deterministic_setup()
    mod = _load_plot_module(fig_dir)
    mod.render(Path(out_path))


def _read_manifest(fig_dir: Path) -> dict:
    p = fig_dir / "manifest.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def build_figure(fig_dir: Path) -> dict:
    """重跑图，更新 out.pdf 与 manifest.json（script_sha / output_sha256 / generated_at）。"""
    fig_dir = Path(fig_dir)
    out_pdf = fig_dir / "out.pdf"
    render_to(fig_dir, out_pdf)
    manifest = _read_manifest(fig_dir)
    manifest["figure_id"] = fig_dir.name
    manifest["script_sha"] = sha256_file(fig_dir / "plot.py")
    manifest.setdefault("run_ids", manifest.get("run_ids", []))
    manifest.setdefault("contract_hash", manifest.get("contract_hash"))
    manifest["output_sha256"] = sha256_file(out_pdf)
    manifest["generated_at"] = _now()
    (fig_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def check_figure(fig_dir: Path) -> Tuple[bool, str]:
    """重跑到临时目录，比对 sha256。不一致 → (False, detail)。"""
    fig_dir = Path(fig_dir)
    manifest = _read_manifest(fig_dir)
    recorded = manifest.get("output_sha256")
    if not recorded:
        return False, f"{fig_dir.name}: manifest 缺 output_sha256（先 make figures）"
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "out.pdf"
        render_to(fig_dir, tmp)
        actual = sha256_file(tmp)
    if actual != recorded:
        return False, (f"{fig_dir.name}: 重跑非 byte-identical（I19）"
                       f" 记录={recorded[:12]} 重跑={actual[:12]}")
    # out.pdf 若存在，也应与记录一致
    out_pdf = fig_dir / "out.pdf"
    if out_pdf.exists() and sha256_file(out_pdf) != recorded:
        return False, f"{fig_dir.name}: out.pdf 与 manifest 不符"
    return True, f"{fig_dir.name}: byte-identical ✅"


def manifest_run_ids(fig_dir: Path) -> List[str]:
    return list(_read_manifest(fig_dir).get("run_ids", []))


def missing_run_ids(fig_dir: Path, results_path: Path) -> List[str]:
    """manifest.run_ids 中不存在于结果库的（应为空）。见 test_figure_manifest_run_ids_exist。"""
    from core import results

    bad = []
    for rid in manifest_run_ids(fig_dir):
        try:
            results.get_run(rid, db=results_path)
        except KeyError:
            bad.append(rid)
    return bad


def build_all() -> List[dict]:
    return [build_figure(d) for d in figure_dirs()]


def check_all() -> Tuple[bool, List[str]]:
    ok_all = True
    details = []
    dirs = figure_dirs()
    if not dirs:
        return True, ["无图表目录"]
    for d in dirs:
        ok, detail = check_figure(d)
        ok_all = ok_all and ok
        details.append(detail)
    return ok_all, details


def _main(argv: List[str]) -> int:  # pragma: no cover - CLI
    cmd = argv[0] if argv else "build"
    if cmd == "build":
        for m in build_all():
            print(f"构建 {m['figure_id']} → out.pdf ({m['output_sha256'][:12]})")
        return 0
    if cmd == "check":
        ok, details = check_all()
        for d in details:
            print(d)
        print("figures-check：" + ("全绿 ✅" if ok else "存在不一致 ❌"))
        return 0 if ok else 1
    print(f"未知命令：{cmd}")
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main(sys.argv[1:]))
