from __future__ import annotations

import subprocess
import sys
from pathlib import Path


COMMON_DIR = Path(__file__).resolve().parents[1]
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from runtime import load_runtime_or_exit


def main(argv: list[str] | None = None) -> int:
    runtime = load_runtime_or_exit(__file__)
    script = (
        Path(runtime["agent_toolkit_root"])
        / "skills"
        / "verify-test-db"
        / "scripts"
        / "test_db.py"
    )
    result = subprocess.run([runtime["python_bin"], str(script), *(argv or sys.argv[1:])])
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
