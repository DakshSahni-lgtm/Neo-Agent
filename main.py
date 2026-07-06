"""
Local AI Agent — CLI

Usage:
  python main.py            # Stepfun primary, verbose thinking shown
  python main.py --qwen     # Force Qwen3.5-122B
  python main.py --quiet    # Hide step-by-step thinking (clean output only)
"""
import sys
import os
from pathlib import Path

# Load .env (no external dependency needed)
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

from core.llm_client import LLMClient, STEPFUN_MODEL, QWEN_MODEL
from core.orchestrator import Orchestrator


def main():
    force_qwen = "--qwen"  in sys.argv
    verbose    = "--quiet" not in sys.argv   # verbose by default in CLI

    model_name = QWEN_MODEL if force_qwen else STEPFUN_MODEL
    llm        = LLMClient(force_qwen=force_qwen)
    agent      = Orchestrator(llm=llm)

    print(f"[model] {model_name}")
    if not verbose:
        print("[mode] quiet — thinking hidden")
    print("Local agent ready. Type 'exit' to quit.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break
        if user_input.lower() in {"exit", "quit"}:
            break
        if not user_input:
            continue

        answer = agent.run(user_input, verbose=verbose)
        print(f"\nAgent: {answer}\n")


if __name__ == "__main__":
    main()
