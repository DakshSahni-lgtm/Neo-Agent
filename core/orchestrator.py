"""
Agent orchestration loop (ReAct pattern).

Each turn:
  1. Build system prompt from agent.md + skills.md + memory.md
  2. Send to LLM, expect a JSON response with thought/action/final_answer
  3. If action → run tool, feed Observation back, loop
  4. If final_answer → return it to the user

Two types of memory:
  - memory.md     : long-term, persists across sessions (written by append_memory)
  - session_context: short-term, lives for the current CLI session only.
                     Automatically updated after gmail_read/gmail_list so the
                     agent knows who it was just talking about without being asked.
"""
import json
import re
from pathlib import Path

from .llm_client import LLMClient
from .tools import run_tool, init_tools

BASE_DIR = Path(__file__).parent.parent

SYSTEM_PROMPT_TEMPLATE = """\
You are a local AI agent assistant. Follow these rules exactly.

# Identity and operating rules
{agent_md}

# Tool registry (already loaded — never call read_memory just to see this list)
You already have ALL tools and memory below. Use them directly.
Only call read_memory if you need to verify memory AFTER an append.
{skills_md}

# Long-term memory (loaded fresh every turn — this IS the current state)
{memory_md}

# Session context (short-term — current conversation only)
{session_context}

# CRITICAL: Response format
Respond with ONLY a single raw JSON object — no markdown fences, no extra text.

{{
  "thought": "step-by-step reasoning about what to do",
  "action": "tool_name or null",
  "action_input": {{}},
  "final_answer": "your reply to the user, or null"
}}

Rules:
- Set EITHER action OR final_answer — never both, never neither.
- If calling a tool: set action to its name, fill action_input, set final_answer to null.
- If replying: set action to null, action_input to {{}}, set final_answer to your reply.
- One action per response only.
- NEVER call a tool just to read info you already have in this prompt.
- When drafting a reply to an email just read, use the sender's address from
  session context as the 'to' field — do not ask Daksh for the recipient.
- The user NEVER sees raw tool Observations — only your final_answer text.
  If a tool returns content the user needs to see (an email draft, diagram
  details, search results, etc.), you MUST copy that content into your
  final_answer directly. Never say "shown above," "displayed," or "as you
  can see" — there is nothing above; final_answer is the ONLY thing the
  user receives.
"""

MAX_STEPS = 32  # raised from 8 — compound tasks (read email + speak) need more steps


class Orchestrator:
    def __init__(self, llm: "LLMClient | None" = None):
        self.llm = llm or LLMClient()
        # Wire LLM client into tools that need it (e.g. diagram generation)
        init_tools(self.llm)
        # Short-term memory: lasts for the current process session only.
        # Stores the last email read/listed so reply context is always available.
        self._session: dict = {}
        # Tracks the most recent gmail_draft call's body so we can verify
        # the agent actually includes it in final_answer (see _validate_final_answer)
        self._last_draft_body: str | None = None
        # Tracks whether a calendar_delete is pending confirmation
        self._pending_calendar_delete: bool = False
        self._draft_validation_retries: int = 0

    def _load(self, filename: str) -> str:
        path = BASE_DIR / filename
        return path.read_text() if path.exists() else f"({filename} not found)"

    def _session_context_str(self) -> str:
        if not self._session:
            return "(no email context yet this session)"
        lines = ["Last email context (use this for replies):"]
        for k, v in self._session.items():
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    def _update_session_from_tool(self, action: str, observation: str) -> None:
        """Parse tool observations to extract and cache useful session context."""
        if action == "gmail_read":
            # Extract From/Subject/ID from the observation text
            for line in observation.splitlines():
                if line.startswith("From:"):
                    # Extract just the email address from "Name <email>" format
                    raw = line.replace("From:", "").strip()
                    import re as _re
                    match = _re.search(r"<(.+?)>", raw)
                    self._session["last_email_from"] = match.group(1) if match else raw
                    self._session["last_email_from_full"] = raw
                elif line.startswith("Subject:"):
                    subj = line.replace("Subject:", "").strip()
                    self._session["last_email_subject"] = subj
                    self._session["last_email_reply_subject"] = (
                        subj if subj.startswith("Re:") else f"Re: {subj}"
                    )
                elif line.startswith("Message-ID:") or "id" in line.lower():
                    pass  # ID already known from gmail_list

        elif action == "gmail_list":
            # Store the first email's ID from listing for quick follow-up
            import re as _re
            match = _re.search(r"ID:\s*(\S+)", observation)
            if match:
                self._session["last_listed_first_id"] = match.group(1)

        elif action == "gmail_draft":
            # Track the draft body so we can verify final_answer actually
            # includes it (catches the "draft is ready" hallucination pattern)
            if "Error" not in observation.split("\n")[0]:
                self._last_draft_body = observation

        elif action == "gmail_send":
            # Draft has been sent (or attempt was made) — clear tracking
            self._last_draft_body = None

        elif action == "calendar_delete":
            # Track pending delete so we can ensure confirm_delete is actually called
            if "About to delete" in observation:
                self._pending_calendar_delete = True

        elif action == "calendar_confirm_delete":
            self._pending_calendar_delete = False

    def _validate_final_answer(self, final_answer: str) -> str | None:
        """
        Returns a correction instruction if final_answer fails validation,
        or None if it's fine to send as-is.
        """
        # Check 1: Gmail draft must be shown in full before sending
        if self._last_draft_body is not None:
            draft_lines = self._last_draft_body.splitlines()
            body_lines = [l for l in draft_lines if l.strip() and not l.startswith(
                ("Draft ready", "To:", "Subject:")
            )]
            if body_lines:
                sample = " ".join(body_lines)[:60].strip()
                if sample and sample[:30] not in final_answer:
                    return (
                        "Your final_answer did NOT include the actual draft text — "
                        "it only referenced it (e.g. 'draft is ready'). Daksh cannot "
                        "see tool Observations, only final_answer. Rewrite final_answer "
                        "to include the FULL draft content (To, Subject, Body) copied "
                        "directly from the gmail_draft Observation, word for word."
                    )

        # Check 2: If calendar_delete was called but confirm_delete was NOT,
        # the model must NOT claim the event was deleted
        if self._pending_calendar_delete:
            false_delete_phrases = [
                "deleted successfully", "has been deleted", "removed from",
                "successfully deleted", "event deleted", "deletion complete"
            ]
            if any(p in final_answer.lower() for p in false_delete_phrases):
                return (
                    "You said the event was deleted but you only called calendar_delete "
                    "(which shows the event and waits for confirmation) — you did NOT "
                    "call calendar_confirm_delete yet. The event has NOT been deleted. "
                    "Tell Daksh the event details and ask for explicit confirmation "
                    "before proceeding."
                )

        return None

    def _system_prompt(self) -> str:
        return SYSTEM_PROMPT_TEMPLATE.format(
            agent_md=self._load("agent.md"),
            skills_md=self._load("skills.md"),
            memory_md=self._load("memory.md"),
            session_context=self._session_context_str(),
        )

    @staticmethod
    def _parse(raw: str) -> dict:
        if raw is None:
            raise ValueError("LLM returned None — model may not support this parameter combination")
        text = raw.strip()
        text = re.sub(r"^```(json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError(f"No JSON object in response: {text!r}")
        return json.loads(match.group(0))

    def run(
        self,
        user_input: str,
        verbose: bool = True,
        conversation_history: list[dict] | None = None,
    ) -> str:
        """
        Run one turn of the agent loop.

        Args:
          user_input            the user's message for this turn
          verbose               print step-by-step thinking to stdout
          conversation_history  list of prior {role, content} dicts from
                                previous turns in this session — injected
                                between the system prompt and the current
                                message so the model has conversational context
        """
        # Build message list: system → prior history → current user message
        messages = [{"role": "system", "content": self._system_prompt()}]

        if conversation_history:
            # Keep last 20 messages (10 exchanges) to avoid context overflow
            messages.extend(conversation_history[-20:])

        messages.append({"role": "user", "content": user_input})

        for step in range(MAX_STEPS):
            raw = self.llm.chat(messages)

            try:
                parsed = self._parse(raw)
            except (ValueError, json.JSONDecodeError) as e:
                if verbose:
                    print(f"[step {step+1}] parse error: {e}\nRaw: {raw[:300]}")
                messages.append({"role": "assistant", "content": raw})
                messages.append({
                    "role": "user",
                    "content": (
                        "Your response was not valid JSON. "
                        "Reply ONLY with the JSON object as described — "
                        "no markdown, no extra text."
                    ),
                })
                continue

            thought      = parsed.get("thought", "")
            action       = parsed.get("action")
            action_input = parsed.get("action_input") or {}
            final_answer = parsed.get("final_answer")

            if verbose and thought:
                print(f"[step {step+1}] thought: {thought}")

            if action:
                obs = run_tool(action, action_input)
                # Update short-term session context from this observation
                self._update_session_from_tool(action, obs)
                if verbose:
                    print(f"[step {step+1}] action: {action}({action_input})\n           → {obs[:200]}{'...' if len(obs) > 200 else ''}")
                messages.append({"role": "assistant", "content": json.dumps(parsed)})
                messages.append({"role": "user",      "content": f"Observation: {obs}"})
                continue

            if final_answer is not None:
                # Validate: if a draft was just made, did the model actually
                # include its content, or just claim it's "ready"?
                correction = self._validate_final_answer(str(final_answer))
                if correction:
                    self._draft_validation_retries = getattr(self, "_draft_validation_retries", 0) + 1
                    if self._draft_validation_retries <= 1:
                        if verbose:
                            print(f"[step {step+1}] final_answer rejected: missing draft content, forcing retry ({self._draft_validation_retries}/1)")
                        messages.append({"role": "assistant", "content": json.dumps(parsed)})
                        messages.append({"role": "user", "content": correction})
                        continue
                    else:
                        # Model failed to comply after 1 retry — deterministically
                        # build the correct answer ourselves instead of trusting it again
                        if verbose:
                            print(f"[step {step+1}] model failed draft-content validation — forcing answer with raw draft")
                        self._draft_validation_retries = 0
                        forced_answer = (
                            f"{final_answer}\n\n"
                            f"{self._last_draft_body}"
                        )
                        self._auto_memorize(user_input, forced_answer, messages, verbose)
                        return forced_answer

                self._draft_validation_retries = 0
                # Auto-memory: after every answer, check if anything is worth saving
                self._auto_memorize(user_input, str(final_answer), messages, verbose)
                return str(final_answer)

            messages.append({"role": "assistant", "content": json.dumps(parsed)})
            messages.append({
                "role": "user",
                "content": "You must set either 'action' or 'final_answer'. Try again.",
            })

        return (
            "I couldn't complete this within the step limit. "
            "Try rephrasing or breaking it into a smaller request."
        )

    def _auto_memorize(
        self,
        user_input: str,
        final_answer: str,
        messages: list[dict],
        verbose: bool,
    ) -> None:
        """
        After every turn, ask the LLM if anything is worth saving to memory.
        Uses a minimal prompt to keep it cheap and fast.
        Silently skips on any error — memory is best-effort, never blocks the answer.
        """
        existing_memory = self._load("memory.md")

        prompt = f"""You are a memory curator for an AI agent. Given a conversation turn,
decide if there are any NEW facts worth saving to long-term memory.

EXISTING MEMORY (do NOT re-save anything already here):
{existing_memory}

CONVERSATION TURN:
User: {user_input}
Agent: {final_answer}

Rules for what to save:
- Preferences ("prefers X over Y", "likes/dislikes X")
- Names, contacts, relationships ("Vansh is a business partner")
- Ongoing projects or decisions made ("decided to use Next.js for X project")
- Important facts about Daksh's work or life
- Technical context (tools used, stack decisions)
- DO NOT save: greetings, generic questions, things already in memory,
  temporary info (current email, today's tasks), or anything forgettable

Respond with ONLY a JSON object, no markdown:
{{
  "should_save": true or false,
  "facts": ["fact 1", "fact 2"]
}}

If nothing new is worth saving, set should_save to false and facts to [].
"""

        try:
            raw = self.llm.chat([{"role": "user", "content": prompt}])
            raw = re.sub(r"^```(json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE).strip()
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if not match:
                return
            result = json.loads(match.group(0))

            if result.get("should_save") and result.get("facts"):
                for fact in result["facts"]:
                    fact = fact.strip()
                    if fact:
                        run_tool("append_memory", {"note": fact})
                        if verbose:
                            print(f"[memory] auto-saved: {fact}")

        except Exception as e:
            if verbose:
                print(f"[memory] auto-memorize skipped ({type(e).__name__}: {e})")