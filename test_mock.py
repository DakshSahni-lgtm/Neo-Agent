"""
Tests the full orchestration loop with a scripted mock LLM.
No API key needed — run this first to verify everything wires correctly.

    python test_mock.py
"""
import json
from core.orchestrator import Orchestrator


class MockLLM:
    def __init__(self, responses):
        self.responses = responses
        self.calls = 0

    def chat(self, messages):
        r = self.responses[self.calls]
        self.calls += 1
        return r


def test_memory_tool():
    print("Running: tool call → observation → final answer loop...")
    scripted = [
        json.dumps({
            "thought": "I should save this preference to memory.",
            "action": "append_memory",
            "action_input": {"note": "Daksh prefers TypeScript over JavaScript."},
            "final_answer": None,
        }),
        json.dumps({
            "thought": "Saved. Now I'll confirm.",
            "action": None,
            "action_input": {},
            "final_answer": "Got it — saved your TypeScript preference to memory.",
        }),
    ]
    agent = Orchestrator(llm=MockLLM(scripted))
    result = agent.run("Remember that I prefer TypeScript over JavaScript.")
    assert "saved" in result.lower(), f"Unexpected result: {result}"

    memory = (agent.llm.__class__.__module__ and True) or True  # just to use agent
    from pathlib import Path
    memory_text = (Path(__file__).parent / "memory.md").read_text()
    assert "TypeScript" in memory_text, "memory.md was not updated"
    print(f"  ✓ Final answer: {result}")
    print(f"  ✓ memory.md updated correctly")


def test_direct_answer():
    print("Running: direct answer (no tool call)...")
    scripted = [
        json.dumps({
            "thought": "The user is greeting me. I can answer directly.",
            "action": None,
            "action_input": {},
            "final_answer": "Hello! I'm your local AI agent. How can I help?",
        }),
    ]
    agent = Orchestrator(llm=MockLLM(scripted))
    result = agent.run("Hey!")
    assert "hello" in result.lower() or "agent" in result.lower()
    print(f"  ✓ Final answer: {result}")


def test_draft_hallucination_forced_correction():
    """
    Reproduces the exact bug: model calls gmail_draft, then tries to say
    'Draft is ready' without the actual content — twice in a row.
    The orchestrator should force the real draft into the final answer
    after 2 failed retries, rather than trusting the model indefinitely.
    """
    print("Running: draft hallucination gets force-corrected...")

    draft_observation = (
        "Draft ready (NOT sent). Review it below, then say 'send it' to send.\n\n"
        "To:      someone@example.com\n"
        "Subject: Re: Application Update\n"
        "\nHi there,\n\nThank you for reaching out. I am completely aware of it.\n\nBest,\nDaksh"
    )

    scripted = [
        json.dumps({
            "thought": "Drafting the reply.",
            "action": "gmail_draft",
            "action_input": {"to": "someone@example.com", "subject": "Re: Application Update", "body": "..."},
            "final_answer": None,
        }),
        json.dumps({
            "thought": "Draft is done.",
            "action": None, "action_input": {},
            "final_answer": "Draft is ready and awaiting your confirmation. Say 'send it' to send.",
        }),
        json.dumps({
            "thought": "Draft is done.",
            "action": None, "action_input": {},
            "final_answer": "The draft has been created. Let me know if you'd like to send it.",
        }),
    ]

    agent = Orchestrator(llm=MockLLM(scripted))

    import core.orchestrator as orch_module
    original_run_tool = orch_module.run_tool

    def fake_run_tool(name, args):
        if name == "gmail_draft":
            return draft_observation
        return original_run_tool(name, args)

    orch_module.run_tool = fake_run_tool
    try:
        result = agent.run("Reply to the latest email saying I'm aware of it.")
    finally:
        orch_module.run_tool = original_run_tool

    assert "someone@example.com" in result, f"Forced answer missing draft content: {result}"
    assert "completely aware of it" in result, f"Forced answer missing draft body: {result}"
    print(f"  ✓ Draft content was force-included after repeated hallucination")


if __name__ == "__main__":
    test_memory_tool()
    test_direct_answer()
    test_draft_hallucination_forced_correction()
    print("\nAll tests passed.")
