"""The booking agent: runs the tool loop on top of any LLMProvider.

Hardening for small local models:
- tool arguments are validated (in tools.py); invalid calls get an error back so the model
  can retry, up to MAX_INVALID_TOOL_CALLS per message,
- tool calls written as plain text are recovered and run,
- the loop is capped at MAX_TOOL_ROUNDS per message,
- bookings and cancellations need the patient's confirmation (enforced in tools.py),
- provider failures become a friendly message, with an optional fallback provider.

Progress can be streamed to the UI through an `on_event(kind, **data)` callback:
"delta" (a piece of reply text), "tool" (a tool is being run) and "reset" (discard
streamed text: the model is starting a new round).
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app import services
from app.assistant import texts
from app.assistant.parsing import extract_tool_calls
from app.assistant.prompts import build_system_prompt
from app.assistant.providers.base import ChatMessage, LLMProvider, ProviderError
from app.assistant.sessions import ChatSession
from app.assistant.tools import TOOL_NAMES, TOOL_SPECS, ToolExecutor, identity_not_provided
from app.clock import today

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 6
MAX_INVALID_TOOL_CALLS = 3
MAX_MESSAGE_CHARS = 1000
MAX_TURNS_PER_SESSION = 40
_INVALID_CODES = {"invalid_arguments", "unknown_tool", "slot_not_offered", "patient_details_not_provided"}


@dataclass
class AgentReply:
    text: str
    pending: dict | None = None
    error: bool = False
    provider: str | None = None


EventCallback = Callable[..., None]

# Text that looks like the start of a tool call written as plain text (see parsing.py).
_TOOL_CALL_MARKERS = ("{", "<tool_call", "```")


class _TextStreamer:
    """Forwards reply text to the UI, but holds it back once it looks like a tool call in text form."""

    def __init__(self, emit: EventCallback):
        self.emit = emit
        self.text = ""
        self.muted = False

    def new_round(self) -> None:
        if self.text:
            self.emit("reset")
        self.text = ""
        self.muted = False

    def __call__(self, piece: str) -> None:
        if self.muted:
            return
        self.text += piece
        if any(marker in self.text for marker in _TOOL_CALL_MARKERS):
            self.muted = True
            self.emit("reset")
            return
        self.emit("delta", text=piece)


class _GiveUp(Exception):
    def __init__(self, text_key: str):
        self.text_key = text_key


class BookingAssistant:
    def __init__(
        self,
        primary: LLMProvider | None,
        fallback: LLMProvider | None = None,
        setup_error: str | None = None,
    ):
        self.primary = primary
        self.fallback = fallback if fallback is not primary else None
        self.setup_error = setup_error

    @property
    def providers(self) -> list[LLMProvider]:
        return [p for p in (self.primary, self.fallback) if p is not None]

    def status(self) -> dict:
        """Whether the assistant can answer right now, with a message for the page if not."""
        if not self.providers:
            return {"ready": False, "provider": None, "message": self.setup_error}
        errors = []
        for provider in self.providers:
            try:
                provider.check_ready()
                return {"ready": True, "provider": provider.label, "message": None}
            except ProviderError as exc:
                errors.append(exc.user_message)
        return {"ready": False, "provider": self.providers[0].label, "message": errors[0]}

    def warm_up(self, db: Session) -> None:
        """Prepare the primary model in advance. Failures are logged, never raised."""
        if self.primary is None:
            return
        try:
            prompt = build_system_prompt(services.list_doctors(db), today())
            self.primary.warm_up(prompt, TOOL_SPECS)
            logger.info("Assistant model %s is warmed up", self.primary.label)
        except Exception as exc:  # e.g. Ollama not running yet; the chat reports it properly later
            logger.info("Assistant warm-up skipped: %s", exc)

    # -- chat ---------------------------------------------------------------

    def reply(
        self, session: ChatSession, db: Session, user_text: str, on_event: EventCallback | None = None
    ) -> AgentReply:
        user_text = user_text.strip()
        if len(session.transcript) // 2 >= MAX_TURNS_PER_SESSION:
            return self._reply(session, texts.text("conversation_too_long"), error=True)
        if not self.providers:
            return self._reply(session, self.setup_error or "The assistant isn't configured.", error=True)

        session.state.user_turn += 1
        last_error: ProviderError | None = None
        for provider in self._providers_for(session):
            try:
                answer = self._run_turn(session, provider, db, user_text, on_event)
            except ProviderError as exc:
                logger.warning("Provider %s failed: %s", provider.label, exc)
                if on_event:
                    on_event("reset")
                session.conversation = None  # rebuild from the transcript next time
                last_error = exc
                if not exc.allow_fallback:
                    break
                continue
            except _GiveUp as give_up:
                session.conversation = None  # it has unanswered tool calls; start clean next turn
                answer = texts.text(give_up.text_key)
            self._record(session, user_text, answer)
            return self._reply(session, answer, provider=provider.label)

        return self._reply(session, last_error.user_message, error=True)

    def confirm(self, session: ChatSession, db: Session, accept: bool) -> AgentReply:
        """Handle the Confirm / Cancel buttons. Runs the pending action directly, without the model."""
        pending = session.state.pending
        if pending is None:
            return self._reply(session, texts.text("nothing_pending"))

        if not accept:
            session.state.pending = None
            answer = texts.text("declined")
            self._record(session, texts.text("decline_user"), answer, add_to_model=True)
            return self._reply(session, answer)

        user_inputs = [message.content for message in session.transcript if message.role == "user"]
        missing = identity_not_provided(pending.patient_name, pending.phone_number, user_inputs)
        if missing:
            session.state.pending = None
            answer = (
                "I can't confirm this booking because the name or phone number wasn't provided by you. "
                "Please start a new conversation and enter your own details when asked."
            )
            self._record(session, texts.text("confirm_user"), answer, add_to_model=True)
            return self._reply(session, answer)

        executor = ToolExecutor(db, session.state)
        try:
            result = executor.run_pending()
        except services.ServiceError as exc:
            key = exc.code if exc.code in texts.TEXTS else "action_failed"
            answer = texts.text(key)
        else:
            answer = texts.text(result["status"], **result["booking"])
        self._record(session, texts.text("confirm_user"), answer, add_to_model=True)
        return self._reply(session, answer)

    # -- internals ----------------------------------------------------------

    def _providers_for(self, session: ChatSession) -> list[LLMProvider]:
        # Keep using the provider that's already handling this chat, then fall back.
        current = session.provider if session.provider in self.providers else self.primary
        return [current] + [p for p in self.providers if p is not current]

    def _run_turn(
        self,
        session: ChatSession,
        provider: LLMProvider,
        db: Session,
        user_text: str,
        on_event: EventCallback | None = None,
    ) -> str:
        if session.conversation is None or session.provider is not provider:
            prompt = build_system_prompt(services.list_doctors(db), today())
            session.conversation = provider.start_conversation(prompt, TOOL_SPECS, session.transcript)
            session.provider = provider
        conversation = session.conversation
        user_inputs = [message.content for message in session.transcript if message.role == "user"]
        user_inputs.append(user_text)
        executor = ToolExecutor(db, session.state, user_inputs=user_inputs)
        streamer = _TextStreamer(on_event) if on_event else None
        conversation.on_text = streamer
        try:
            return self._tool_loop(conversation, executor, user_text, streamer, on_event)
        finally:
            conversation.on_text = None

    def _tool_loop(self, conversation, executor, user_text, streamer, on_event) -> str:
        turn = conversation.send_user_message(user_text)
        invalid_calls = 0
        for _ in range(MAX_TOOL_ROUNDS):
            calls, visible_text = turn.tool_calls, turn.text
            if not calls:
                calls, visible_text = extract_tool_calls(turn.text, TOOL_NAMES)
            if not calls:
                return visible_text.strip() or texts.text("empty_reply")

            if streamer:
                streamer.new_round()
            for call in calls:
                if on_event:
                    on_event("tool", name=call.name)
            results = [executor.execute(call) for call in calls]
            for call, result in zip(calls, results):
                logger.info("tool %s -> %s", call.name, result.error_code or "ok")
                # Arguments and results can contain patient details, so only at DEBUG level.
                logger.debug("tool %s args=%s result=%s", call.name, call.arguments, result.content)
            invalid_calls += sum(1 for r in results if r.error_code in _INVALID_CODES)
            if invalid_calls >= MAX_INVALID_TOOL_CALLS:
                raise _GiveUp("too_many_errors")
            turn = conversation.send_tool_results(results)
        raise _GiveUp("too_many_steps")

    def _record(self, session: ChatSession, user_text: str, answer: str, add_to_model: bool = False) -> None:
        session.transcript += [ChatMessage("user", user_text), ChatMessage("assistant", answer)]
        if add_to_model and session.conversation is not None:
            session.conversation.add_exchange(user_text, answer)

    @staticmethod
    def _reply(session: ChatSession, text: str, error: bool = False, provider: str | None = None) -> AgentReply:
        pending = session.state.pending.to_public() if session.state.pending else None
        return AgentReply(text=text, pending=pending, error=error, provider=provider)
