"""Run one isolated Groq Vision smoke test against a local rental image."""

from __future__ import annotations

import argparse
import sys

from harness import print_outcome, run_test
from prompt import SINGLE_IMAGE_PROMPT


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", help="Path to a .jpg, .jpeg, .png, or .webp image")
    arguments = parser.parse_args()
    result, saved_path = run_test([arguments.image], SINGLE_IMAGE_PROMPT)
    return print_outcome(result, saved_path)


if __name__ == "__main__":
    sys.exit(main())
