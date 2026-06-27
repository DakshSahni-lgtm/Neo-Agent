"""
Regenerates skills.md from core/tools.py TOOLS registry.
Run after adding or changing any tool:
    python sync_skills.py
"""
from pathlib import Path
from core.tools import TOOLS

OUTPUT = Path(__file__).parent / "skills.md"

HEADER = """\
# Tool registry (already in your context — no tool call needed to read this)

Call tools by exact name in your JSON response. Do NOT invent tool names.
Auto-generated from core/tools.py — edit tools there, then run sync_skills.py.

"""


def generate() -> str:
    lines = [HEADER]
    for name, spec in TOOLS.items():
        args = ", ".join(
            f"{k} ({v})" for k, v in spec["args_schema"].items()
        ) or "none"
        lines.append(f"## {name}\n{spec['description']}\nArgs: {args}\n")
    return "\n".join(lines)


if __name__ == "__main__":
    OUTPUT.write_text(generate())
    print(f"skills.md updated ({len(TOOLS)} tools)")
