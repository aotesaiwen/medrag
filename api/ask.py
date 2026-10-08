"""Ask one question: python -m api.ask 'GDPR 的删除权是什么？'."""

import argparse
import json
import sys

from api.client import ask, display


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask the local course RAG service.")
    parser.add_argument("question")
    parser.add_argument("--json", action="store_true", help="Print the full JSON response.")
    args = parser.parse_args()
    try:
        result = ask(args.question)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else display(result))


if __name__ == "__main__":
    main()
