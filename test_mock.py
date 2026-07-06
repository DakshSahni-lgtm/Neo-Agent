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


def test_tool_error_hallucination_caught():
    """
    Reproduces the exact bug: schedule_daily_task fails (scheduler not
    initialized), but the model claims success anyway. The orchestrator
    should reject that final_answer and force an honest retry.
    """
    print("Running: tool error hallucination gets caught and corrected...")

    error_observation = "Error: scheduler not initialized"

    scripted = [
        # Step 1: call schedule_daily_task, which fails
        json.dumps({
            "thought": "Scheduling the daily joke task.",
            "action": "schedule_daily_task",
            "action_input": {"name": "Daily joke", "prompt": "Tell a joke", "time": "12:00"},
            "final_answer": None,
        }),
        # Step 2: model hallucinates success despite the error
        json.dumps({
            "thought": "Task is scheduled.",
            "action": None, "action_input": {},
            "final_answer": "Done! I've scheduled a daily joke task for 12:00 PM every day.",
        }),
        # Step 3: after correction, model finally tells the truth
        json.dumps({
            "thought": "I need to be honest about the failure.",
            "action": None, "action_input": {},
            "final_answer": "That actually failed — the scheduler wasn't initialized. Error: scheduler not initialized",
        }),
    ]

    agent = Orchestrator(llm=MockLLM(scripted))

    import core.orchestrator as orch_module
    original_run_tool = orch_module.run_tool

    def fake_run_tool(name, args):
        if name == "schedule_daily_task":
            return error_observation
        return original_run_tool(name, args)

    orch_module.run_tool = fake_run_tool
    try:
        result = agent.run("Tell me a joke every day at 12pm.")
    finally:
        orch_module.run_tool = original_run_tool

    assert "failed" in result.lower() or "error" in result.lower(), (
        f"Expected honest failure message, got: {result}"
    )
    assert "done!" not in result.lower(), f"Hallucinated success leaked through: {result}"
    print(f"  ✓ Tool error was not hidden by a false success claim")


def test_stepfun_xml_tool_call_format_parsed():
    """
    Reproduces the exact bug: Stepfun sometimes emits its native
    <tool_call><function=X> XML format instead of JSON, despite instructions.
    The orchestrator must recognize and parse this instead of looping
    until MAX_STEPS is exhausted.
    """
    print("Running: Stepfun XML tool_call format is parsed correctly...")

    scripted = [
        # Step 1: model emits its native XML format instead of JSON
        "<tool_call>\n<function=list_scheduled_tasks>\n</function>\n</tool_call>",
        # Step 2: after the tool runs, model gives a normal JSON final answer
        json.dumps({
            "thought": "Got the task list.",
            "action": None, "action_input": {},
            "final_answer": "You have no scheduled tasks right now.",
        }),
    ]

    agent = Orchestrator(llm=MockLLM(scripted))

    import core.orchestrator as orch_module
    original_run_tool = orch_module.run_tool

    def fake_run_tool(name, args):
        if name == "list_scheduled_tasks":
            return "No scheduled tasks currently set up."
        return original_run_tool(name, args)

    orch_module.run_tool = fake_run_tool
    try:
        result = agent.run("What scheduled tasks do I have?")
    finally:
        orch_module.run_tool = original_run_tool

    assert "no scheduled tasks" in result.lower(), f"Expected clean answer, got: {result}"
    print(f"  ✓ XML tool_call format was parsed without looping to MAX_STEPS")


def test_stale_draft_does_not_leak_into_unrelated_turns():
    """
    Reproduces the exact bug: draft a scheduled email (via schedule_email_send
    instead of gmail_send), then ask a completely unrelated question in the
    NEXT run() call. The stale draft content must NOT be force-appended to
    that unrelated answer — this was happening because _last_draft_body was
    only ever cleared by gmail_send, not schedule_email_send.
    """
    print("Running: stale draft does not leak into unrelated future turns...")

    draft_observation = (
        "Draft ready (NOT sent). Review it below, then say 'send it' to send.\n\n"
        "To:      vansh@example.com\n"
        "Subject: Shipment Delay Notification\n"
        "\nHi Vansh,\n\nThis is to inform you that the shipment has been delayed.\n"
        "\nBest,\nDaksh"
    )

    import core.orchestrator as orch_module
    original_run_tool = orch_module.run_tool

    def fake_run_tool(name, args):
        if name == "gmail_draft":
            return draft_observation
        if name == "schedule_email_send":
            return "Email scheduled to send at 2026-07-01 11:20!\nTo: vansh@example.com"
        if name == "list_scheduled_tasks":
            return "Scheduled tasks (1):\n\u2022 Send email to vansh (once at 2026-07-01 11:20)"
        return original_run_tool(name, args)

    orch_module.run_tool = fake_run_tool
    try:
        # Turn 1: draft + schedule the email (mirrors the real bug report)
        agent = Orchestrator(llm=MockLLM([
            json.dumps({
                "thought": "Drafting the email.",
                "action": "gmail_draft",
                "action_input": {"to": "vansh@example.com", "subject": "Shipment Delay Notification", "body": "..."},
                "final_answer": None,
            }),
            json.dumps({
                "thought": "Show the draft and ask for confirmation.",
                "action": None, "action_input": {},
                "final_answer": (
                    "Here's your draft to Vansh:\n\nHi Vansh,\n\nThis is to inform you "
                    "that the shipment has been delayed.\n\nBest,\nDaksh\n\n"
                    "Confirm sending at 11:20?"
                ),
            }),
        ]))
        turn1_result = agent.run("send an email to vansh at 11:20 am telling him the shipment is delayed")
        assert turn1_result.count("This is to inform you") == 1, (
            f"Draft content should appear exactly once in turn 1, got: {turn1_result}"
        )

        # Turn 2: user confirms — model calls schedule_email_send (NOT gmail_send)
        agent.llm = MockLLM([
            json.dumps({
                "thought": "Confirmed — scheduling the send.",
                "action": "schedule_email_send",
                "action_input": {"to": "vansh@example.com", "subject": "Shipment Delay Notification",
                                  "body": "...", "time": "11:20"},
                "final_answer": None,
            }),
            json.dumps({
                "thought": "Done.",
                "action": None, "action_input": {},
                "final_answer": "Your email to Vansh is scheduled to send at 11:20 am.",
            }),
        ])
        turn2_result = agent.run("yes")
        assert "This is to inform you" not in turn2_result, (
            f"Stale draft leaked into turn 2 confirmation: {turn2_result}"
        )

        # Turn 3: a COMPLETELY unrelated question — the actual bug scenario
        agent.llm = MockLLM([
            json.dumps({
                "thought": "Checking scheduled tasks.",
                "action": "list_scheduled_tasks",
                "action_input": {},
                "final_answer": None,
            }),
            json.dumps({
                "thought": "Got the list.",
                "action": None, "action_input": {},
                "final_answer": "You have 1 scheduled task: sending an email to Vansh at 11:20am.",
            }),
        ])
        turn3_result = agent.run("what scheduled tasks do you have right now?")
        assert "This is to inform you" not in turn3_result, (
            f"Stale draft leaked into an unrelated turn 3 question: {turn3_result}"
        )

    finally:
        orch_module.run_tool = original_run_tool

    print(f"  ✓ Stale draft did not leak into unrelated future turns")


if __name__ == "__main__":
    test_memory_tool()
    test_direct_answer()
    test_draft_hallucination_forced_correction()
    test_tool_error_hallucination_caught()
    test_stepfun_xml_tool_call_format_parsed()
    test_stale_draft_does_not_leak_into_unrelated_turns()
    print("\nAll tests passed.")
