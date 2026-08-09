"""Run one isolated multi-image Groq Vision test for a single property."""

from __future__ import annotations

import argparse
import sys

from harness import MAX_IMAGES, print_outcome, run_test
from prompt import MULTIPLE_IMAGE_PROMPT


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "images",
        nargs="+",
        help=f"One to {MAX_IMAGES} .jpg, .jpeg, .png, or .webp images for the same property",
    )
    arguments = parser.parse_args()
    result, saved_path = run_test(arguments.images, MULTIPLE_IMAGE_PROMPT)
    return print_outcome(result, saved_path)


if __name__ == "__main__":
    sys.exit(main())
