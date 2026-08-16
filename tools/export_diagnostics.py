"""Create or refresh the bounded Beta Earth live support Export20 bundle.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from beta_earth.application.diagnostics import (  # noqa: E402,F401
    MAX_ITEMS,
    MAX_TOTAL_UNCOMPRESSED_BYTES,
    NOTICE,
    _absolute_path_findings,
    _security_and_drive_summary,
    _sha256,
    default_destination,
    export_support_bundle,
)


def export(project_root: Path, destination: Path) -> tuple[int, list[str]]:
    count, errors, _ = export_support_bundle(project_root, destination)
    return count, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="explicit diagnostic ZIP path")
    args = parser.parse_args()
    destination = args.output or default_destination(PROJECT_ROOT)
    try:
        count, errors, output = export_support_bundle(PROJECT_ROOT, destination)
    except Exception as exc:
        print(f"Diagnostic export failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"Diagnostic export: OK ({count}/{MAX_ITEMS} files)")
    print(f"UPLOAD THIS: {output}")
    print(f"SHA256: {_sha256(output)}")
    print(f"Sidecar: {output.with_suffix(output.suffix + '.sha256.txt')}")
    if errors:
        print("Partial collectors: " + ", ".join(errors))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
