"""Stage 3: test JSON generation without server-side JSON validation."""

import argparse
import sys

from staged_harness import print_outcome, run_vision_test
from stage_prompts import SIMPLE_JSON_PROMPT


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+", help="One to five images for the same property")
    arguments = parser.parse_args()
    result, path = run_vision_test(arguments.images, prompt=SIMPLE_JSON_PROMPT, test_type="vision_json_without_response_format", use_response_format=False, parse_json=True)
    return print_outcome(result, path)


if __name__ == "__main__":
    sys.exit(main())
