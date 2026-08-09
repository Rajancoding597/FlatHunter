"""Stage 2: test server-side Groq JSON mode after plaintext vision succeeds."""

import argparse
import sys

from staged_harness import print_outcome, run_vision_test
from stage_prompts import SIMPLE_JSON_PROMPT


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+", help="One to five images for the same property")
    arguments = parser.parse_args()
    result, path = run_vision_test(arguments.images, prompt=SIMPLE_JSON_PROMPT, test_type="vision_simple_json", use_response_format=True, parse_json=True)
    return print_outcome(result, path)


if __name__ == "__main__":
    sys.exit(main())
