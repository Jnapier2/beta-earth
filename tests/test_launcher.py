from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class LauncherTests(unittest.TestCase):
    def test_launcher_dry_run_validates_startup_without_serving_forever(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-B", "run_beta_earth.py", "--dry-run", "--no-browser"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Beta Earth startup dry-run: OK", completed.stdout)
        self.assertIn("Browser opened: NO", completed.stdout)
        self.assertIn("Server loop started: NO", completed.stdout)


if __name__ == "__main__":
    unittest.main()
