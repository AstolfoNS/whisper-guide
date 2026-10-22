"""Deterministic session state machine; speech engines and wall clocks stay outside."""

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.domain.tracking import ScriptTracker


class SessionState(StrEnum):
    READY = "READY"
    LISTENING = "LISTENING"
    PROMPTING = "PROMPTING"
    PAUSED = "PAUSED"
    TERMINATED = "TERMINATED"


class SessionOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    silence_timeout_ms: StrictInt = Field(default=3000, gt=0)


class SessionError(ValueError):
    """An explicit user command is not valid in the current state."""


@dataclass(frozen=True, slots=True)
class ActivePrompt:
    id: str
    text: str
    start: int
    end: int
    next_position: int


EVENT_PRIORITY = {
    "end": 0,
    "pause": 1,
    "service_fault": 1,
    "interrupt": 2,
    "speech_start": 3,
    "transcript": 3,
    "speech_end": 3,
    "playback_finished": 4,
    "tick": 5,
}


class SpeechSession:
    """One in-memory session with original-script offsets and round identifiers.

    The adapter serializes incoming events and supplies a monotonic millisecond clock.
    Audio buffers are not part of this domain object and are never persisted here.
    """

    def __init__(
        self,
        script_id: str,
        script_text: str,
        silence_timeout_ms: int = 3000,
        session_id: str | None = None,
    ) -> None:
        self.id = session_id or str(uuid4())
        self.script_id = script_id
        self.tracker = ScriptTracker(script_text)
        self.options = SessionOptions(silence_timeout_ms=silence_timeout_ms)
        self.state = SessionState.READY
        self.reading_position: int | None = None
        self.prompt_position = self.tracker.units[0].start
        self.reliable = True
        self.generation = 0
        self.prompt: ActivePrompt | None = None
        self.silence_since_ms: int | None = None
        self.speaking = False
        self.pause_reason: str | None = None
        self.message: str | None = None
        self.feedback_id = 0
        self.last_transcript = ""
        self.match_confidence: float | None = None
        self._location_warning_reported = False

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "script_id": self.script_id,
            "state": self.state.value,
            "silence_timeout_ms": self.options.silence_timeout_ms,
            "reading_position": self.reading_position,
            "prompt_position": self.prompt_position,
            "reliable": self.reliable,
            "generation": self.generation,
            "prompt": asdict(self.prompt) if self.prompt else None,
            "speaking": self.speaking,
            "pause_reason": self.pause_reason,
            "message": self.message,
            "feedback_id": self.feedback_id,
            "last_transcript": self.last_transcript,
            "match_confidence": self.match_confidence,
            "at_end": self.prompt_position >= len(self.tracker.text),
        }

    def _feedback(self, message: str) -> None:
        self.message = message
        self.feedback_id += 1

    def _listen(self, now_ms: int) -> None:
        self.state = SessionState.LISTENING
        self.generation += 1
        self.prompt = None
        self.speaking = False
        self.silence_since_ms = now_ms
        self.pause_reason = None

    def _prompt(self) -> None:
        segment = self.tracker.segment(self.prompt_position)
        if segment is None:
            return
        self.state = SessionState.PROMPTING
        self.generation += 1
        self.silence_since_ms = None
        self.speaking = False
        self.prompt = ActivePrompt(id=str(uuid4()), **asdict(segment))

    def _require(self, *states: SessionState) -> None:
        if self.state not in states:
            raise SessionError(f"当前状态 {self.state.value} 不允许此操作。")

    def handle_batch(self, events: list[tuple[str, dict[str, Any]]], now_ms: int) -> dict[str, Any]:
        ordered = sorted(events, key=lambda item: EVENT_PRIORITY.get(item[0], 3))
        for event, payload in ordered:
            self.handle(event, now_ms, **payload)
        return self.snapshot()

    def handle(self, event: str, now_ms: int, **payload: Any) -> dict[str, Any]:
        # A terminated session is immutable, including late controls and callbacks.
        if self.state == SessionState.TERMINATED:
            return self.snapshot()
        # Only asynchronous engine callbacks carry a round identifier. User controls
        # always apply to the current session and must never be dropped as stale.
        if event in {"speech_start", "speech_end", "transcript", "tick"}:
            generation = payload.get("generation")
            if generation is not None and generation != self.generation:
                return self.snapshot()

        if event == "end":
            self.state = SessionState.TERMINATED
            self.generation += 1
            self.prompt = None
            self.silence_since_ms = None
            self.speaking = False
            self.pause_reason = None
        elif event in {"start", "resume"}:
            self._require(SessionState.READY if event == "start" else SessionState.PAUSED)
            if not payload.get("service_available", True):
                raise SessionError("语音服务尚不可用，请检查后重试。")
            self._listen(now_ms)
        elif event == "update_options":
            self._require(SessionState.READY, SessionState.PAUSED)
            self.options = SessionOptions(silence_timeout_ms=payload.get("silence_timeout_ms"))
        elif event in {"pause", "service_fault"}:
            if event == "pause":
                self._require(SessionState.LISTENING, SessionState.PROMPTING)
            elif self.state not in {SessionState.LISTENING, SessionState.PROMPTING}:
                return self.snapshot()
            self.state = SessionState.PAUSED
            self.generation += 1
            self.prompt = None
            self.silence_since_ms = None
            self.speaking = False
            self.pause_reason = payload.get("reason") or (
                "用户暂停" if event == "pause" else "语音服务连接中断，请检查后继续。"
            )
            self._feedback(self.pause_reason)
        elif event == "interrupt":
            if self.state == SessionState.PROMPTING:
                self._listen(now_ms)
        elif event == "playback_finished":
            if (
                self.state == SessionState.PROMPTING
                and self.prompt
                and payload.get("prompt_id") == self.prompt.id
            ):
                self.prompt_position = self.prompt.next_position
                self._listen(now_ms)
        elif event == "speech_start":
            if self.state == SessionState.LISTENING:
                self.speaking = True
                self.silence_since_ms = None
                # A new utterance invalidates the previous automatic-prompt anchor
                # until it is matched. Never guess from an old position if STT lags.
                self.reliable = False
        elif event == "speech_end":
            if self.state == SessionState.LISTENING:
                self.speaking = False
                self.silence_since_ms = now_ms
        elif event == "transcript":
            if self.state == SessionState.LISTENING:
                text = payload.get("text", "")
                if not isinstance(text, str):
                    raise SessionError("识别文本必须是字符串。")
                self.last_transcript = text
                result = self.tracker.match(text, self.reading_position)
                if result is None and self.prompt_position != self.reading_position:
                    # After several automatically read segments the speaker may
                    # resume near the system's current prompt, outside the last
                    # confirmed user's local window. This is only a second anchor;
                    # it never changes reading_position without actual speech.
                    result = self.tracker.match(text, self.prompt_position)
                if result:
                    self.reading_position = result.position
                    self.prompt_position = result.position
                    self.reliable = True
                    self.match_confidence = result.confidence
                    self._location_warning_reported = False
                elif text.strip():
                    if not self._location_warning_reported:
                        self._feedback(
                            "暂时无法可靠定位讲稿，自动提示已暂停；可手动提示或重新定位。"
                        )
                        self._location_warning_reported = True
                    self.reliable = False
                    self.match_confidence = None
        elif event == "tick":
            if (
                self.state == SessionState.LISTENING
                and not self.speaking
                and self.reliable
                and self.silence_since_ms is not None
                and now_ms - self.silence_since_ms >= self.options.silence_timeout_ms
            ):
                self._prompt()
        elif event == "manual_prompt":
            self._require(SessionState.LISTENING)
            self._prompt()
        elif event == "reposition":
            self._require(SessionState.READY, SessionState.LISTENING, SessionState.PAUSED)
            position = payload.get("position")
            if isinstance(position, bool) or not isinstance(position, int):
                raise SessionError("重新定位的位置必须是整数。")
            try:
                self.prompt_position = self.tracker.snap_position(position)
            except ValueError as error:
                raise SessionError(str(error)) from error
            self.reading_position = self.prompt_position
            self.reliable = True
            self._location_warning_reported = False
            self.match_confidence = None
            self.generation += 1
            if self.state == SessionState.LISTENING:
                self.speaking = False
                self.silence_since_ms = now_ms
        else:
            raise SessionError(f"未知事件：{event}")
        return self.snapshot()
