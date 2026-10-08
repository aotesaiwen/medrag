"""An interactive conversation kept only in this process's memory."""

import sys

from api.client import ask, display
from rag.answer import HistoryTurn


def main() -> None:
    history: list[HistoryTurn] = []
    print("请输入问题；/exit 退出，/clear 开始新对话。历史仅保留在内存中。")
    while True:
        try:
            question = input("\n你：").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not question:
            continue
        if question.lower() in {"/exit", "/quit"}:
            break
        if question.lower() == "/clear":
            history.clear()
            print("已清空对话。")
            continue
        try:
            result = ask(question, history)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            continue
        print(f"\n助手：{display(result)}")
        history = [*history, HistoryTurn(question, result["answer"])][-5:]


if __name__ == "__main__":
    main()
