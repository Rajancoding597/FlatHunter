"""Stage 1: determine whether Groq can inspect one-property rental screenshots."""

import argparse
import sys

from harness import print_outcome, run_vision_test
from prompt import PLAINTEXT_VISION_PROMPT


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+", help="One to five images for the same property")
    arguments = parser.parse_args()
    result, path = run_vision_test(
        arguments.images,
        prompt=PLAINTEXT_VISION_PROMPT,
        test_type="vision_plaintext",
        use_response_format=False,
        parse_json=False,
    )
    return print_outcome(result, path)


if __name__ == "__main__":
    sys.exit(main())
