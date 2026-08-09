"""Run every FlatHunter vision golden case using one explicit provider."""

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["groq", "gemini"], required=True)
    arguments = parser.parse_args()
    failures = 0
    for case in sorted(path.name for path in (HERE / "cases").iterdir() if path.is_dir()):
        completed = subprocess.run([sys.executable, str(HERE / "run_case.py"), case, "--provider", arguments.provider], check=False)
        failures += completed.returncode != 0
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
