"""Deterministic session state machine with bounded transition telemetry."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.domain.tracking import MatchResult, ScriptAligner, ScriptTracker, content_units


class SessionState(StrEnum):
    READY = "READY"
    LISTENING = "LISTENING"
    PROMPTING = "PROMPTING"
    PAUSED = "PAUSED"
    TERMINATED = "TERMINATED"


TRANSITION_TARGETS: dict[SessionState, tuple[SessionState, ...]] = {
    SessionState.READY: (SessionState.LISTENING, SessionState.TERMINATED),
    SessionState.LISTENING: (
        SessionState.PROMPTING,
        SessionState.PAUSED,
        SessionState.TERMINATED,
    ),
    SessionState.PROMPTING: (
        SessionState.LISTENING,
        SessionState.PAUSED,
        SessionState.TERMINATED,
    ),
    SessionState.PAUSED: (SessionState.LISTENING, SessionState.TERMINATED),
    SessionState.TERMINATED: (),
}


class SessionOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    silence_timeout_ms: StrictInt = Field(default=3000, ge=1000)


class SessionError(ValueError):
    """An explicit user command is not valid in the current state."""


FILLER_UTTERANCES = frozenset(
    {"嗯", "嗯嗯", "呃", "呃呃", "啊", "额", "唔", "诶", "欸", "那个", "这个"}
)


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
    """One in-memory session with bounded state transition telemetry."""

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
        self.aligner = ScriptAligner(self.tracker)
        self.options = SessionOptions(silence_timeout_ms=silence_timeout_ms)
        self.state = SessionState.READY
        self.previous_state: SessionState | None = None
        self.transition_trigger = "session_initialized"
        self.transition_at = datetime.now(UTC).isoformat()
        self.transition_history: deque[dict[str, Any]] = deque(maxlen=10)
        self.transition_history.append(
            {
                "from_state": None,
                "to_state": SessionState.READY.value,
                "trigger": "session_initialized",
                "timestamp": self.transition_at,
            }
        )
        self.reading_position: int | None = None
        self.prompt_position = self.tracker.units[0].start
        self.reliable = True
        self._last_reliable = True
        self.generation = 0
        self.prompt: ActivePrompt | None = None
        self.silence_since_ms: int | None = None
        self.speaking = False
        self.pause_reason: str | None = None
        self.message: str | None = None
        self.feedback_id = 0
        self.last_transcript = ""
        self.raw_transcript = ""
        self.aligned_transcript = ""
        self.match_confidence: float | None = None
        self._location_warning_reported = False
        self.provisional_position: int | None = None
        self.provisional_transcript = ""
        self.provisional_confidence: float | None = None
        self.recovery_status = "idle"
        self.recovery_attempts = 0
        self._recovery_candidates: tuple[MatchResult, ...] = ()
        self._recovery_evidence_id: Any = None
        self._alignment_evidence_ids: tuple[Any, ...] = ()

    def _transition(self, state: SessionState, trigger: str, timestamp: int | None) -> None:
        if state == self.state:
            return
        self.clear_provisional()
        if state != SessionState.LISTENING:
            self._recovery_candidates = ()
            self._recovery_evidence_id = None
        previous = self.state
        self.previous_state = previous
        self.state = state
        self.transition_trigger = trigger
        self.transition_at = datetime.now(UTC).isoformat()
        self.transition_history.append(
            {
                "from_state": previous.value,
                "to_state": state.value,
                "trigger": trigger,
                "timestamp": self.transition_at,
            }
        )

    def allowed_transitions(self) -> list[str]:
        return [state.value for state in TRANSITION_TARGETS[self.state]]

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "script_id": self.script_id,
            "state": self.state.value,
            "current_state": self.state.value,
            "previous_state": self.previous_state.value if self.previous_state else None,
            "transition_trigger": self.transition_trigger,
            "transition_at": self.transition_at,
            "transition_history": list(self.transition_history),
            "allowed_transitions": self.allowed_transitions(),
            "silence_timeout_ms": self.options.silence_timeout_ms,
            "silence_prompt_policy": "saved_position",
            "reading_position": self.reading_position,
            "provisional_position": self.provisional_position,
            "provisional_transcript": self.provisional_transcript,
            "provisional_confidence": self.provisional_confidence,
            "recovery_status": self.recovery_status,
            "recovery_attempts": self.recovery_attempts,
            "prompt_position": self.prompt_position,
            "reliable": self.reliable,
            "alignment_status": (
                "mismatch"
                if self._location_warning_reported
                else "reliable"
                if self.reliable
                else "pending"
            ),
            "generation": self.generation,
            "prompt": asdict(self.prompt) if self.prompt else None,
            "speaking": self.speaking,
            "pause_reason": self.pause_reason,
            "message": self.message,
            "feedback_id": self.feedback_id,
            "last_transcript": self.last_transcript,
            "raw_transcript": self.raw_transcript,
            "aligned_transcript": self.aligned_transcript,
            "match_confidence": self.match_confidence,
            "at_end": self.prompt_position >= len(self.tracker.text),
        }

    def clear_provisional(self) -> None:
        self.provisional_position = None
        self.provisional_transcript = ""
        self.provisional_confidence = None

    def retract_evidence(self, evidence_id: Any) -> None:
        """Withdraw tentative anchors when their segment is revised or filtered."""
        self.clear_provisional()
        if evidence_id in self._alignment_evidence_ids:
            # Keep the old offset for reference, but neither fillers nor timers
            # may authorize a prompt from a withdrawn acoustic/transcript proof.
            self.reliable = self._last_reliable = False
            self.match_confidence = None
            self._alignment_evidence_ids = ()
            if not self._location_warning_reported:
                self._feedback("本段声音证据已失效，正在尝试自动重新定位；请继续朗读正文。")
                self._location_warning_reported = True
            self.reset_recovery_evidence()
        elif evidence_id == self._recovery_evidence_id:
            self.reset_recovery_evidence()

    def mark_alignment_pending(self) -> None:
        """An older alignment verdict does not settle a newer voice activity."""
        self.reliable = False

    def reset_recovery_evidence(self) -> None:
        """A rebuilt stream cannot provide the second half of an old recovery chain."""
        self._recovery_candidates = ()
        self._recovery_evidence_id = None
        self.recovery_status = "searching" if self._location_warning_reported else "idle"

    def preview_transcript(self, text: str) -> None:
        """Show stable hypotheses without authorizing a prompt or moving either cursor."""
        self.clear_provisional()
        if self.state != SessionState.LISTENING or len(content_units(text)) < 3:
            return
        if self._location_warning_reported:
            candidates = self.aligner.recovery_candidates(text, min_units=3)
            result = candidates[0] if len(candidates) == 1 else None
        else:
            result = self.aligner.align(text, self.reading_position)
        if result is not None:
            self.provisional_position = result.position
            self.provisional_transcript = result.matched_text
            self.provisional_confidence = result.confidence

    @staticmethod
    def _candidate_start(result: MatchResult) -> int:
        return result.unit_index - len(content_units(result.matched_text)) + 1

    def _nearby(self, result: MatchResult) -> bool:
        anchor = (
            0
            if self.reading_position is None
            else self.tracker.unit_index_at(self.reading_position)
        )
        start = self._candidate_start(result)
        return start - 40 <= anchor <= result.unit_index + 24

    def _recover_position(self, text: str, evidence_id: Any) -> MatchResult | None:
        candidates = self.aligner.recovery_candidates(text, min_units=3)
        # A near fuzzy correction still needs independent original-text support and
        # global uniqueness. It cannot bypass the repeated-phrase recovery guard.
        if not candidates:
            local = self.aligner.align(text, self.reading_position)
            if local and self.aligner.supports_refinement(text, local.matched_text):
                candidates = self.aligner.recovery_candidates(local.matched_text, min_units=3)
        self.recovery_attempts += 1
        if not candidates:
            self.recovery_status = "searching"
            self._recovery_candidates = ()
            self._recovery_evidence_id = evidence_id
            return None
        if len(candidates) == 1 and self._nearby(candidates[0]):
            return candidates[0]
        # Outside the neighborhood, require two independent, advancing pieces of
        # evidence. A partial/final revision or an LLM quote is still the same piece.
        continuous = [
            current
            for current in candidates
            if len(content_units(current.matched_text)) >= 6
            and any(
                len(content_units(previous.matched_text)) >= 6
                and self._candidate_start(current) >= self._candidate_start(previous)
                and current.unit_index > previous.unit_index
                and -8 <= self._candidate_start(current) - previous.unit_index <= 24
                for previous in self._recovery_candidates
            )
        ]
        if (
            evidence_id != self._recovery_evidence_id
            and len(candidates) < 33
            and len(self._recovery_candidates) < 33
            and len(continuous) == 1
        ):
            return continuous[0]
        self.recovery_status = "ambiguous" if len(candidates) > 1 else "confirming"
        self._recovery_candidates = candidates
        self._recovery_evidence_id = evidence_id
        return None

    def _commit_alignment(self, result: MatchResult, evidence_id: Any) -> None:
        dependencies = (evidence_id,)
        if not self._nearby(result) and self._recovery_evidence_id is not None:
            dependencies = (self._recovery_evidence_id, evidence_id)
        self._alignment_evidence_ids = dependencies
        self.reading_position = result.position
        self.prompt_position = self.tracker.prompt_position_after_reading(result.position)
        self.reliable = self._last_reliable = True
        self.match_confidence = result.confidence
        self.aligned_transcript = result.matched_text
        self._location_warning_reported = False
        self.recovery_status = "idle"
        self._recovery_candidates = ()
        self._recovery_evidence_id = None
        self.message = None

    def _feedback(self, message: str) -> None:
        self.message = message
        self.feedback_id += 1

    def _listen(self, now_ms: int, trigger: str) -> None:
        self._transition(SessionState.LISTENING, trigger, now_ms)
        self.generation += 1
        self.prompt = None
        self.speaking = False
        self.silence_since_ms = now_ms
        self.pause_reason = None
        self.clear_provisional()
        self._recovery_candidates = ()
        self._recovery_evidence_id = None
        self.recovery_status = "searching" if self._location_warning_reported else "idle"

    def _prompt(self, now_ms: int, trigger: str = "silence_threshold_reached") -> None:
        segment = self.tracker.segment(self.prompt_position)
        if segment is None:
            return
        self._transition(SessionState.PROMPTING, trigger, now_ms)
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
        if self.state == SessionState.TERMINATED:
            return self.snapshot()
        if event in {"speech_start", "speech_end", "transcript", "tick"}:
            generation = payload.get("generation")
            if generation is not None and generation != self.generation:
                return self.snapshot()

        if event == "end":
            self._transition(
                SessionState.TERMINATED,
                payload.get("transition_trigger", "user_terminated"),
                now_ms,
            )
            self.generation += 1
            self.prompt = None
            self.silence_since_ms = None
            self.speaking = False
            self.pause_reason = None
        elif event in {"start", "resume"}:
            self._require(SessionState.READY if event == "start" else SessionState.PAUSED)
            if not payload.get("service_available", True):
                raise SessionError("语音服务尚不可用，请检查后重试。")
            self._listen(now_ms, "user_resumed" if event == "resume" else "session_started")
        elif event == "update_options":
            self._require(SessionState.READY, SessionState.PAUSED)
            self.options = SessionOptions(silence_timeout_ms=payload.get("silence_timeout_ms"))
        elif event in {"pause", "service_fault"}:
            if event == "pause":
                self._require(SessionState.LISTENING, SessionState.PROMPTING)
            elif self.state not in {SessionState.LISTENING, SessionState.PROMPTING}:
                return self.snapshot()
            trigger = payload.get(
                "transition_trigger",
                "user_paused" if event == "pause" else "service_fault",
            )
            self._transition(SessionState.PAUSED, trigger, now_ms)
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
                self._listen(now_ms, "speech_interrupted_prompt")
        elif event == "playback_finished":
            if (
                self.state == SessionState.PROMPTING
                and self.prompt
                and payload.get("prompt_id") == self.prompt.id
            ):
                self.prompt_position = self.prompt.next_position
                self._listen(now_ms, "prompt_audio_finished")
        elif event == "speech_start":
            if self.state == SessionState.LISTENING:
                # VAD marks this activity pending, not a new alignment verdict.
                # Repeated onsets must not overwrite the last committed verdict.
                self.reliable = False
                self.speaking = True
                self.silence_since_ms = None
        elif event == "speech_end":
            if self.state == SessionState.LISTENING:
                self.speaking = False
                self.silence_since_ms = now_ms
        elif event == "filler":
            if self.state == SessionState.LISTENING:
                self.reliable = self._last_reliable
        elif event == "transcript":
            if self.state == SessionState.LISTENING:
                text = payload.get("text", "")
                if not isinstance(text, str):
                    raise SessionError("识别文本必须是字符串。")
                self.raw_transcript = payload.get("raw_text", text)
                text = self.tracker.normalize_transcript(text, self.reading_position)
                self.last_transcript = text
                compact = "".join(text.split()).strip("，。！？!?；;,.、")
                if compact.casefold() in FILLER_UTTERANCES:
                    self.reliable = self._last_reliable
                    return self.snapshot()
                self.clear_provisional()
                if payload.get("recovery") and not self.aligner.supports_refinement(
                    self.raw_transcript, text
                ):
                    return self.snapshot()
                lost = self._location_warning_reported
                result = None if lost else self.aligner.align(text, self.reading_position)
                if not lost and result is None and self.prompt_position != self.reading_position:
                    result = self.aligner.align(text, self.prompt_position)
                if result is None:
                    result = self._recover_position(text, payload.get("evidence_id", now_ms))
                if result is not None:
                    self._commit_alignment(result, payload.get("evidence_id", now_ms))
                elif text.strip():
                    if not self._location_warning_reported:
                        self._feedback(
                            "暂时无法可靠定位讲稿，正在尝试自动重新定位；"
                            "请继续朗读正文，也可手动提示或重新定位。"
                        )
                        self._location_warning_reported = True
                    self.reliable = self._last_reliable = False
                    self.match_confidence = None
        elif event == "tick":
            if (
                self.state == SessionState.LISTENING
                and not self.speaking
                and self.silence_since_ms is not None
                and now_ms - self.silence_since_ms >= self.options.silence_timeout_ms
            ):
                # Recognition may remain pending or genuinely mismatched after
                # silence. The user's saved prompt anchor is still an explicit
                # place to help from; playing it is not evidence of a new read.
                unconfirmed = not self.reliable
                self._prompt(
                    now_ms,
                    "recognition_wait_timeout" if unconfirmed else "silence_threshold_reached",
                )
                if unconfirmed and self.state == SessionState.PROMPTING:
                    self._feedback(
                        "静音等待已到，当前朗读位置尚未确认，先从已保存的提示起点播报；"
                        "继续朗读可自动重新定位，也可手动定位。"
                    )
        elif event == "manual_prompt":
            self._require(SessionState.LISTENING)
            self._prompt(now_ms, "manual_prompt")
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
            self._alignment_evidence_ids = ()
            self.clear_provisional()
            self.recovery_status = "idle"
            self.recovery_attempts = 0
            self._recovery_candidates = ()
            self._recovery_evidence_id = None
            self.reliable = True
            self._last_reliable = True
            self._location_warning_reported = False
            self.match_confidence = None
            self.generation += 1
            if self.state == SessionState.LISTENING:
                self.speaking = False
                self.silence_since_ms = now_ms
        else:
            raise SessionError(f"未知事件：{event}")
        return self.snapshot()
