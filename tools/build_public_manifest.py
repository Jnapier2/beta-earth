"""Build the runtime-integrity manifest for the public evaluation source."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOTS = {
    "runtime": "runtime",
    "config": "runtime/config",
    "state": "runtime",
    "logs": "runtime/computers/<COMPUTER_ID>/logs",
    "temp": "runtime/temp",
    "cache": "runtime/cache",
    "exports": "exports",
    "support_exports": "exports/support/<COMPUTER_ID>",
    "diagnostics": "runtime/computers/<COMPUTER_ID>/diagnostics",
    "reports": "runtime/reports",
    "downloads": "runtime/downloads",
    "backups": "runtime/backups",
    "releases": "releases",
}
ROOT_FILES = (
    "VERSION",
    "VERSION.txt",
    "PACKAGE_METADATA.json",
    "SBOM.json",
    "BetaEarthSovereignty.bat",
    "BetaEarthSovereignty.py",
    "BetaEarthSovereignty_ExportDiagnostics.bat",
    "EXPORT_DIAGNOSTICS.bat",
    "START_BETA_EARTH.bat",
    "run_beta_earth.py",
    "pyproject.toml",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def managed_paths() -> list[Path]:
    paths = [ROOT / name for name in ROOT_FILES]
    for directory in ("content", "hud", "src"):
        paths.extend(
            path
            for path in (ROOT / directory).rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix.casefold() not in {".pyc", ".pyo"}
        )
    paths.append(ROOT / "tools" / "export_diagnostics.py")
    return sorted(paths, key=lambda path: path.relative_to(ROOT).as_posix().casefold())


def main() -> None:
    assets = []
    for path in managed_paths():
        relative = path.relative_to(ROOT).as_posix()
        assets.append(
            {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
                "package_managed": True,
            }
        )
    document = {
        "metadata_schema": "asset-metadata-v1.2-rights-integrity",
        "schema_version": 4,
        "package": "beta-earth-sovereignty-next",
        "project": "Beta Earth: Sovereignty Next",
        "version": "0.51.1",
        "build_id": "BESOV-0.51.1-20260816-HUD-TRUTH-ALIGNMENT",
        "status": "public-evaluation-source",
        "execution_namespace": "BetaEarthSovereignty",
        "canonical_entrypoint": "BetaEarthSovereignty.bat",
        "backend_target": "BetaEarthSovereignty.py",
        "output_roots": OUTPUT_ROOTS,
        "release": "0.51.1 — The Field Tells the Truth",
        "sensitivity": "public",
        "rights_holder": "Gateway Information Group LLC",
        "copyright_notice": "Copyright © 2026 Gateway Information Group LLC. All rights reserved.",
        "source_lineage": [],
        "asset_count": len(assets),
        "assets": assets,
    }
    (ROOT / "MANIFEST.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"MANIFEST.json: {len(assets)} managed files")


if __name__ == "__main__":
    main()
