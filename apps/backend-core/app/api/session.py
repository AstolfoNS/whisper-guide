"""One server-owned state machine per session; microphone data stays in memory."""

import asyncio
import base64
import copy
import inspect
import json
import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import Any

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import clone_model_settings
from app.domain.session import SessionError, SessionState, SpeechSession
from app.domain.tracking import content_units
from app.infrastructure.audio_evidence import AudioEvidence
from app.infrastructure.llm import LLMSemanticRefiner
from app.infrastructure.speech import ProviderSpeechServices, SpeechUnavailableError
from app.repositories.scripts import get_script as find_script

router = APIRouter(tags=["sessions"])


def now_ms() -> int:
    return time.monotonic_ns() // 1_000_000


@dataclass(frozen=True, slots=True)
class SettledTranscript:
    version: int
    evidence_id: tuple[int, int]
    text: str
    timestamp: int
    accepted_position: int | None
    acoustic_support: bool


class AudioFrameQueue(asyncio.Queue[tuple[int, bytes]]):
    """Bound both packet count and PCM duration; never discard accepted audio."""

    def __init__(self) -> None:
        super().__init__(maxsize=50)
        self.queued_bytes = 0

    def put_nowait(self, item: tuple[int, bytes]) -> None:
        if self.queued_bytes + len(item[1]) > 32000:
            raise asyncio.QueueFull
        super().put_nowait(item)
        self.queued_bytes += len(item[1])

    def get_nowait(self) -> tuple[int, bytes]:
        item = super().get_nowait()
        self.queued_bytes -= len(item[1])
        return item

    def take_batch(self, first: tuple[int, bytes], *, enabled: bool) -> list[bytes]:
        frames = [first[1]]
        size = len(first[1])
        # Peeking is encapsulated here so a generation boundary is never dequeued
        # into a preceding batch. No await or input mutation occurs in this loop.
        while enabled and self._queue and len(frames) < 5:
            following = self._queue[0]
            if following[0] != first[0] or size + len(following[1]) > 3200:
                break
            frames.append(self.get_nowait()[1])
            size += len(frames[-1])
        return frames


class SessionRuntime:
    MAX_REFINEMENT_CATCHUP = 4
    PREVIEW_STABLE_MS = 300

    def __init__(
        self,
        session: SpeechSession,
        script_name: str,
        speech: Any,
        provider_name: str = "local",
        *,
        model_settings: Any | None = None,
        model_config: dict[str, Any] | None = None,
    ) -> None:
        self.session = session
        self.script_name = script_name
        self.speech = speech
        self.provider_name = provider_name
        self.model_settings = model_settings or getattr(speech, "settings", None)
        self.model_config_id = (model_config or {}).get("id", "")
        self.model_config_name = (model_config or {}).get("name", "")
        self.model_config_revision = (model_config or {}).get("revision", 0)
        self.refinement: asyncio.Task[None] | None = None
        self.committed_revision = 0
        self.stream_epoch = 0
        self.voice_activity_revision = 0
        self.settled_activity_revision = 0
        self.committed_history: deque[SettledTranscript] = deque(
            maxlen=self.MAX_REFINEMENT_CATCHUP + 1
        )
        self.refinement_version: int | None = None
        self.refinement_evidence_id: tuple[int, int] | None = None
        self.preview_hypothesis: tuple[int, str] | None = None
        self.preview_since_ms = 0
        self.preview_observations = 0
        self.preview_applied: tuple[int, str] | None = None
        self.acoustic_segments: deque[int] = deque(maxlen=32)
        self.settled_hypothesis: tuple[int, str] | None = None
        # A single accepted hypothesis survives VAD display resets until settled.
        self.pending_hypothesis: tuple[int, str] | None = None
        self.raw_transcript_is_current = False
        self.finalized_segment_id = -1
        self.stream: Any = None
        self.stt_ready = False
        self.readiness: asyncio.Task[None] | None = None
        self.audio_ready_requested_generation = -1
        self.socket: WebSocket | None = None
        self.lock = asyncio.Lock()
        self.audio_ready = False
        self.synthesis: asyncio.Task[None] | None = None
        self.last_voice_ms: int | None = None
        self.last_frame_ms: int | None = None
        self.last_text = ""
        self.last_segment_id = -1
        self.last_update_final = False
        self.ready_generation = -1
        self.audio_queue = AudioFrameQueue()
        self.audio_received_ms: int | None = None
        self.audio_processed_ms: int | None = None
        self.audio_inference_ms = 0.0
        self.audio_batch_frames = 0
        self.audio_inflight = False
        self.audio_pending = False
        self.audio_evidence = AudioEvidence()
        self.filtered_text = ""
        self.filtered_final = False
        self.filtered_count = 0
        self.last_prompt_diagnostics_ms = now_ms()

    @property
    def audio_queued_ms(self) -> int:
        return self.audio_queue.queued_bytes // 32

    def prompt_diagnostics(self, timestamp: int | None = None) -> dict[str, Any]:
        """Explain the real prompt gates using scalars, without audio or script data."""
        timestamp = now_ms() if timestamp is None else timestamp
        received_age = (
            None if self.audio_received_ms is None else max(0, timestamp - self.audio_received_ms)
        )
        pending_voice = bool(self.audio_evidence.pending_voice)
        queued_ms = self.audio_queued_ms
        elapsed = remaining = None
        if (
            self.session.state == SessionState.LISTENING
            and self.stt_ready
            and self.audio_ready
            and received_age is not None
            and received_age <= 5000
            and not self.session.speaking
            and self.session.silence_since_ms is not None
        ):
            elapsed = max(0, timestamp - self.session.silence_since_ms)
            remaining = max(0, self.session.options.silence_timeout_ms - elapsed)
        progress_elapsed = progress_remaining = None
        if (
            self.session.options.prompt_mode == "progress"
            and self.session.state == SessionState.LISTENING
            and self.stt_ready
            and self.audio_ready
            and received_age is not None
            and received_age <= 5000
            and self.session.progress_prompt_armed
            and self.session.progress_since_ms is not None
        ):
            progress_elapsed = max(0, timestamp - self.session.progress_since_ms)
            progress_remaining = max(0, self.session.options.progress_timeout_ms - progress_elapsed)
        if self.session.state != SessionState.LISTENING:
            reason = "inactive"
        elif not self.stt_ready:
            reason = "stt_connecting"
        elif not self.audio_ready:
            reason = "audio_not_ready"
        elif received_age is None or received_age > 5000:
            # audio_ready seeds last_frame_ms but does not prove any PCM arrived.
            reason = "audio_missing"
        elif not self.audio_queue.empty():
            reason = "audio_backlog"
        elif self.audio_inflight or self.audio_pending:
            reason = "audio_processing"
        elif self.session.options.prompt_mode == "progress" and self.session.prompt_position >= len(
            self.session.tracker.text
        ):
            reason = "at_end"
        elif (
            self.session.options.prompt_mode == "progress"
            and not self.session.progress_prompt_armed
        ):
            reason = "awaiting_progress"
        elif self.session.options.prompt_mode == "progress":
            reason = (
                "progress_wait" if progress_remaining is None or progress_remaining else "ready"
            )
        elif self.session.speaking:
            reason = "voice_active"
        elif pending_voice:
            reason = "voice_pending"
        elif self.session.prompt_position >= len(self.session.tracker.text):
            reason = "at_end"
        elif elapsed is None or remaining:
            reason = "silence_wait"
        elif self.pending_hypothesis and self.pending_hypothesis != self.settled_hypothesis:
            reason = "recognition_settling"
        else:
            # Alignment uncertainty is not a permanent gate under saved-position
            # policy. The next eligible tick uses the saved prompt anchor.
            reason = "ready"
        return {
            "prompt_wait_reason": reason,
            "silence_elapsed_ms": elapsed,
            "silence_remaining_ms": remaining,
            "progress_elapsed_ms": progress_elapsed,
            "progress_remaining_ms": progress_remaining,
            "progress_prompt_armed": self.session.progress_prompt_armed,
            "last_progress_source": self.session.last_progress_source,
            "last_audio_received_age_ms": received_age,
            "audio_queued_ms": queued_ms,
            "audio_inference_ms": max(0, int(self.audio_inference_ms)),
            "audio_batch_frames": self.audio_batch_frames,
            "audio_ready": self.audio_ready,
            "pending_voice": pending_voice,
            "audio_inflight": self.audio_inflight,
        }

    async def publish_prompt_diagnostics(self, timestamp: int | None = None) -> None:
        """At most one small diagnostic event per second while listening."""
        timestamp = now_ms() if timestamp is None else timestamp
        if (
            self.socket is None
            or self.session.state != SessionState.LISTENING
            or timestamp - self.last_prompt_diagnostics_ms < 1000
        ):
            return
        self.last_prompt_diagnostics_ms = timestamp
        await self.send(
            {
                "type": "audio_diagnostics",
                "session_id": self.session.id,
                "generation": self.session.generation,
                "diagnostics": self.prompt_diagnostics(timestamp),
            }
        )

    def snapshot(self) -> dict[str, Any]:
        snapshot = self.session.snapshot()
        snapshot.update(
            session_id=self.session.id,
            script_name=self.script_name,
            script_text=self.session.tracker.text,
            units=[asdict(unit) for unit in self.session.tracker.units],
            position_reliable=self.session.reliable,
            recognized_text=self.last_text or self.session.last_transcript,
            raw_transcript=self.filtered_text or self.last_text or self.session.raw_transcript,
            aligned_transcript=self.session.aligned_transcript,
            notice=self.session.message,
            stt_provider=self.provider_name,
            stt_ready=self.stt_ready,
            stt_fallback_reason=getattr(self.stream, "fallback_reason", None),
            stt_provider_active=getattr(self.stream, "effective_provider", self.provider_name),
            stt_fallback=bool(
                getattr(self.stream, "fallback_active", False)
                or (
                    self.stream is not None
                    and self.provider_name != "local"
                    and getattr(self.stream, "effective_provider", None) == "local"
                )
            ),
            model_config_id=self.model_config_id,
            model_config_name=self.model_config_name,
            model_config_revision=self.model_config_revision,
            recognition_is_final=self.last_update_final,
            raw_transcript_is_current=self.raw_transcript_is_current,
            raw_transcript_is_final=(
                self.filtered_final
                if self.filtered_text
                else self.last_update_final
                or (not self.last_text and bool(self.session.raw_transcript))
            ),
            recognition_filtered=bool(self.filtered_text),
            recognition_filtered_count=self.filtered_count,
            recognition_filter_reason="缺少本段有效人声证据" if self.filtered_text else "",
            recognition_segment_id=self.last_segment_id,
        )
        snapshot.update(self.prompt_diagnostics())
        return snapshot

    async def send(self, message: dict[str, Any]) -> None:
        if self.socket:
            await self.socket.send_json(message)

    async def publish(self) -> None:
        await self.send({"type": "snapshot", "session": self.snapshot()})

    def release_stream(self) -> None:
        if self.readiness and self.readiness is not asyncio.current_task():
            self.readiness.cancel()
        self.readiness = None
        self.stt_ready = False
        self.audio_ready_requested_generation = -1
        self.cancel_refinement()
        self.stream_epoch += 1
        self.voice_activity_revision = 0
        self.settled_activity_revision = 0
        self.committed_history.clear()
        self.session.reset_recovery_evidence()
        self.acoustic_segments.clear()
        self.clear_preview()
        self.settled_hypothesis = None
        self.pending_hypothesis = None
        self.raw_transcript_is_current = False
        self.finalized_segment_id = -1
        self.ready_generation = -1
        while not self.audio_queue.empty():
            self.audio_queue.get_nowait()
        if self.stream:
            self.stream.close()
        self.stream = None
        self.audio_ready = False
        self.last_voice_ms = None
        self.last_frame_ms = None
        self.last_text = ""
        self.last_segment_id = -1
        self.last_update_final = False
        self.audio_evidence = AudioEvidence()
        self.filtered_text = ""
        self.filtered_final = False
        self.filtered_count = 0
        self.audio_inflight = False
        self.audio_pending = False
        self.audio_received_ms = None
        self.audio_processed_ms = None
        self.audio_inference_ms = 0.0
        self.audio_batch_frames = 0

    @staticmethod
    def _stream_ready(stream: Any) -> bool:
        return bool(stream is not None and getattr(stream, "ready", stream.available))

    def _mark_audio_ready(self) -> None:
        if self.ready_generation == self.session.generation:
            return
        self.audio_ready = True
        self.ready_generation = self.session.generation
        self.session.silence_since_ms = now_ms()
        self.session.progress_since_ms = None
        self.last_frame_ms = now_ms()

    def _prepare_readiness(self) -> None:
        self.stt_ready = self._stream_ready(self.stream)
        if self.stt_ready:
            return
        self.readiness = asyncio.create_task(
            self._wait_for_stream(self.stream, self.session.generation, self.stream_epoch)
        )

    async def _wait_for_stream(self, stream: Any, generation: int, epoch: int) -> None:
        """A handshake waits outside the control lock and before microphone capture."""
        try:
            budget = min(
                30.0, max(0.2, getattr(self.model_settings, "cloud_stt_timeout_ms", 5000) / 1000)
            )
            waiter = getattr(stream, "wait_ready", None)
            if not callable(waiter):
                raise SpeechUnavailableError("语音连接尚未就绪。")
            async with asyncio.timeout(budget + 0.5):
                pending = waiter()
                prepared = await pending if inspect.isawaitable(pending) else pending
            if prepared is False or not self._stream_ready(stream):
                raise SpeechUnavailableError("语音连接未能就绪。")
            async with self.lock:
                if (
                    self.stream is not stream
                    or self.session.generation != generation
                    or self.stream_epoch != epoch
                    or self.session.state != SessionState.LISTENING
                ):
                    return
                self.stt_ready = True
                if self.audio_ready_requested_generation == generation:
                    # Older clients may already have requested capture. New clients
                    # wait for stt_ready before enabling PCM, so no first words vanish.
                    self._mark_audio_ready()
                await self.publish()
        except asyncio.CancelledError:
            pass
        except (SpeechUnavailableError, TimeoutError):
            async with self.lock:
                if (
                    self.stream is stream
                    and self.session.generation == generation
                    and self.stream_epoch == epoch
                    and self.session.state == SessionState.LISTENING
                ):
                    await self.change(
                        "service_fault", reason="语音识别连接未就绪，请检查服务后继续。"
                    )
        finally:
            if self.readiness is asyncio.current_task():
                self.readiness = None

    def cancel_prompt(self) -> None:
        if self.synthesis and self.synthesis is not asyncio.current_task():
            self.synthesis.cancel()
        self.synthesis = None

    def cancel_refinement(self) -> None:
        if self.refinement and self.refinement is not asyncio.current_task():
            self.refinement.cancel()
        self.refinement = None
        self.refinement_version = None
        self.refinement_evidence_id = None

    def clear_preview(self) -> None:
        self.preview_applied = None
        self.preview_hypothesis = None
        self.preview_observations = 0
        self.session.clear_provisional()

    def retract_transcript_evidence(self, evidence_id: tuple[int, int]) -> bool:
        self.session.retract_evidence(evidence_id)
        revoked = [item for item in self.committed_history if item.evidence_id == evidence_id]
        if revoked:
            versions = {item.version for item in revoked}
            self.committed_history = deque(
                (item for item in self.committed_history if item.version not in versions),
                maxlen=self.MAX_REFINEMENT_CATCHUP + 1,
            )
        if self.refinement_evidence_id == evidence_id or (
            self.refinement_version is not None
            and any(item.version >= self.refinement_version for item in revoked)
        ):
            # A retracted later segment also invalidates an older request whose
            # catch-up would otherwise replay those withdrawn words.
            self.cancel_refinement()
        return bool(revoked)

    def retract_unsupported_evidence(self) -> bool:
        changed = False
        pending = self.pending_hypothesis
        if pending and not self.audio_evidence.is_supported(pending[0]):
            self.pending_hypothesis = None
            self.filtered_text = pending[1]
            self.filtered_final = False
            self.filtered_count += 1
            self.clear_preview()
            self.retract_transcript_evidence((self.session.generation, pending[0]))
            changed = True
        revoked_ids = {
            item.evidence_id
            for item in self.committed_history
            if item.acoustic_support and not self.audio_evidence.is_supported(item.evidence_id[1])
        }
        for evidence_id in revoked_ids:
            changed = self.retract_transcript_evidence(evidence_id) or changed
        return changed

    def preview_partial(self, timestamp: int) -> bool:
        candidate = self.preview_hypothesis
        if (
            not candidate
            or candidate != self.pending_hypothesis
            or candidate == self.preview_applied
            or not self.audio_evidence.is_supported(candidate[0])
            or candidate[0] <= self.finalized_segment_id
            or (
                self.preview_observations < 2
                and timestamp - self.preview_since_ms < self.PREVIEW_STABLE_MS
            )
        ):
            return False
        before = self.session.snapshot()
        self.session.preview_transcript(candidate[1])
        self.session.note_preview_progress(timestamp)
        self.preview_applied = candidate
        after = self.session.snapshot()
        return any(
            before.get(key) != after.get(key)
            for key in ("provisional_position", "provisional_transcript", "provisional_confidence")
        )

    def commit_transcript(self, text: str, timestamp: int) -> None:
        """Commit local evidence first; partial display revisions cannot starve recovery."""
        anchor = self.session.reading_position
        pending = self.pending_hypothesis
        # Actual final/silence settlement resolves the current listening round,
        # including accepted partials preserved across harmless VAD display resets.
        self.settled_activity_revision = self.voice_activity_revision
        self.settled_hypothesis = (
            pending if pending and pending[1] == text else (self.last_segment_id, text)
        )
        self.pending_hypothesis = None
        self.clear_preview()
        evidence_id = (self.session.generation, self.settled_hypothesis[0])
        self.session.handle("transcript", timestamp, text=text, evidence_id=evidence_id)
        self.committed_revision += 1
        evidence = SettledTranscript(
            self.committed_revision,
            evidence_id,
            text,
            timestamp,
            self.session.reading_position if self.session.reliable else None,
            self.settled_hypothesis[0] in self.acoustic_segments,
        )
        self.committed_history.append(evidence)
        if self.refinement and not self.refinement.done():
            if (
                self.refinement_evidence_id != evidence_id
                and self.refinement_version is not None
                and evidence.version - self.refinement_version <= self.MAX_REFINEMENT_CATCHUP
            ):
                # New segments are checked after the old result arrives. A revised
                # interpretation of the same segment must invalidate its old request.
                return
            self.cancel_refinement()
        self.start_refinement(evidence, anchor)

    def start_refinement(self, evidence: SettledTranscript, anchor: int | None) -> None:
        refiner = LLMSemanticRefiner(self.model_settings)
        if (
            not refiner.enabled
            or len(content_units(evidence.text)) < 6
            or (refiner.align_mode == "fallback" and self.session.reliable)
        ):
            return
        recovering = self.session.snapshot()["alignment_status"] != "reliable"
        window = (
            self.session.aligner.recovery_window(anchor, transcript=evidence.text)
            if recovering
            else self.session.aligner.script_window(anchor)
        )
        if not window:
            return
        self.refinement_version = evidence.version
        self.refinement_evidence_id = evidence.evidence_id
        self.refinement = asyncio.create_task(
            self.refine_transcript(
                refiner,
                window,
                evidence.text,
                self.session.generation,
                evidence.version,
                self.stream_epoch,
            )
        )

    async def refine_transcript(
        self,
        refiner: LLMSemanticRefiner,
        window: str,
        rough: str,
        generation: int,
        revision: int,
        stream_epoch: int,
    ) -> None:
        try:
            result = await refiner.refine(window, rough, turn_token=generation)
            async with self.lock:
                history = list(self.committed_history)
                original = next((item for item in history if item.version == revision), None)
                catchup = [item for item in history if item.version > revision]
                if (
                    result is None
                    or result.confidence < 0.68
                    or result.matched_quote not in window
                    or len(content_units(result.matched_quote)) < 6
                    or not self.session.aligner.supports_refinement(rough, result.matched_quote)
                    or self.session.state != SessionState.LISTENING
                    or self.session.generation != generation
                    or self.stream_epoch != stream_epoch
                    or self.refinement is not asyncio.current_task()
                    or original is None
                    or original.text != rough
                    or len(catchup) > self.MAX_REFINEMENT_CATCHUP
                    or self.committed_revision != revision + len(catchup)
                    or any(item.evidence_id == original.evidence_id for item in catchup)
                ):
                    return
                # Validate every intervening settled segment on a separate domain
                # state before updating the live cursor. The aligner/index is read-only.
                projection = copy.copy(self.session)
                projection._progress_confirmed_ids = self.session._progress_confirmed_ids.copy()
                events = [
                    (
                        result.matched_quote,
                        rough,
                        original.evidence_id,
                        True,
                        original.timestamp,
                    ),
                    *[
                        (item.text, item.text, item.evidence_id, False, item.timestamp)
                        for item in catchup
                    ],
                ]
                for text, raw, evidence_id, recovery, timestamp in events:
                    projection.handle(
                        "transcript",
                        timestamp,
                        text=text,
                        raw_text=raw,
                        evidence_id=evidence_id,
                        generation=generation,
                        recovery=recovery,
                    )
                if catchup and (
                    not projection.reliable
                    or (
                        catchup[-1].accepted_position is not None
                        and projection.reading_position != catchup[-1].accepted_position
                    )
                ):
                    return
                # No await separates validation and replay, and a model never
                # supplies character offsets or independent second-segment evidence.
                for text, raw, evidence_id, recovery, timestamp in events:
                    self.session.handle(
                        "transcript",
                        timestamp,
                        text=text,
                        raw_text=raw,
                        evidence_id=evidence_id,
                        generation=generation,
                        recovery=recovery,
                    )
                if (
                    self.pending_hypothesis is not None
                    or self.voice_activity_revision > self.settled_activity_revision
                ):
                    # A corrected historical anchor cannot settle newer sounds.
                    # Keep its true verdict for later filler handling, while the
                    # current activity still waits for its own settled evidence.
                    self.session.mark_alignment_pending()
                # Model replay can move the committed anchor and clear the
                # provisional display even when the new partial is unchanged.
                self.preview_applied = None
                self.preview_partial(now_ms())
                await self.publish()
        except asyncio.CancelledError:
            pass
        finally:
            if self.refinement is asyncio.current_task():
                self.refinement = None
                self.refinement_version = None
                self.refinement_evidence_id = None
                if (
                    self.session.state == SessionState.LISTENING
                    and self.session.generation == generation
                    and self.stream_epoch == stream_epoch
                    and self.committed_history
                    and self.committed_revision > revision
                ):
                    # A failed old request may retry the newest settled evidence
                    # once. Never loop on an unchanged evidence version.
                    self.start_refinement(self.committed_history[-1], self.session.reading_position)

    async def tick(self, timestamp: int) -> None:
        """Settle supported drafts at bounded silence or progress deadlines."""
        retracted = self.retract_unsupported_evidence()
        if self.preview_partial(timestamp) or retracted:
            await self.publish()
        progress_mode = self.session.options.prompt_mode == "progress"
        if progress_mode and (
            not self.stt_ready
            or not self.audio_ready
            or self.audio_received_ms is None
            or timestamp - self.audio_received_ms > 5000
            or not self.audio_queue.empty()
            or self.audio_inflight
            or self.audio_pending
        ):
            return
        progress_due = self.session.progress_due(timestamp)
        if self.audio_evidence.pending_voice and not progress_due:
            return
        due = (
            not self.session.speaking
            and self.session.silence_since_ms is not None
            and timestamp - self.session.silence_since_ms >= self.session.options.silence_timeout_ms
        )
        pending = self.pending_hypothesis
        if (due or progress_due) and pending and pending != self.settled_hypothesis:
            # Settle accepted text at either deadline. If it supplies genuinely
            # new progress, give it its full new budget before taking over.
            previous_progress = self.session.progress_since_ms
            self.commit_transcript(pending[1], timestamp)
            if progress_due and self.session.progress_since_ms != previous_progress:
                return
        before = self.session.state
        self.session.handle("tick", timestamp)
        if self.session.state != before:
            self.release_stream()
            await self.publish()
            if self.session.prompt:
                self.synthesis = asyncio.create_task(self.make_prompt(self.session.prompt.id))

    async def change(self, event: str, *, prepared_stream: Any = None, **payload: Any) -> None:
        previous_generation = self.session.generation
        self.session.handle(event, now_ms(), **payload)
        if previous_generation != self.session.generation:
            self.release_stream()
            self.cancel_prompt()
        if prepared_stream is not None:
            self.stream = prepared_stream
        if self.session.state == SessionState.LISTENING:
            if self.stream is None:
                try:
                    self.stream = await self._create_stream()
                except SpeechUnavailableError:
                    await self.change("service_fault", reason="识别服务不可用，已暂停。")
                    return
            if not self.stream.available:
                await self.change("service_fault", reason="识别服务不可用，已暂停。")
                return
            if self.readiness is None:
                self._prepare_readiness()
        await self.publish()
        if self.session.state == SessionState.PROMPTING and self.session.prompt:
            if self.synthesis is None:
                self.synthesis = asyncio.create_task(self.make_prompt(self.session.prompt.id))

    async def make_prompt(self, prompt_id: str) -> None:
        try:
            prompt = self.session.prompt
            if not prompt or prompt.id != prompt_id:
                return
            audio = await self.speech.synthesize(prompt.text)
            async with self.lock:
                if (
                    self.session.state == SessionState.PROMPTING
                    and self.session.prompt
                    and self.session.prompt.id == prompt_id
                ):
                    await self.send(
                        {
                            "type": "audio",
                            "prompt_id": prompt_id,
                            "mime_type": "audio/mpeg",
                            "data": base64.b64encode(audio).decode("ascii"),
                        }
                    )
        except asyncio.CancelledError:
            return
        except Exception:
            async with self.lock:
                if self.session.prompt and self.session.prompt.id == prompt_id:
                    await self.change(
                        "service_fault", reason="提示语音生成失败，请检查服务后继续。"
                    )
        finally:
            if self.synthesis is asyncio.current_task():
                self.synthesis = None

    async def _create_stream(self, turn_token: int | None = None) -> Any:
        try:
            return await asyncio.to_thread(
                self.speech.create_stream,
                provider_name=self.provider_name,
                turn_token=self.session.generation if turn_token is None else turn_token,
            )
        except TypeError:
            return await asyncio.to_thread(self.speech.create_stream)

    async def control(self, command: dict[str, Any]) -> None:
        event = command.get("type")
        if event in {"start", "resume"}:
            try:
                status = self.speech.status(provider_name=self.provider_name)
            except TypeError:
                status = self.speech.status()
            available = status.get("stt") in {True, "available", "ready"} and status.get("tts") in {
                True,
                "available",
                "ready",
            }
            if not available:
                raise SessionError("识别模型尚未就绪，请先安装服务器语音资源。")
            stream = await self._create_stream(self.session.generation + 1)
            if not stream.available:
                stream.close()
                raise SessionError("识别模型加载失败，请检查服务器语音资源。")
            try:
                await self.change(event, prepared_stream=stream, service_available=True)
            except Exception:
                stream.close()
                raise
        elif event == "audio_ready":
            if self.session.state == SessionState.LISTENING:
                if self.ready_generation == self.session.generation:
                    return
                if self.stream is None:
                    self.stream = await self._create_stream()
                if not self.stream.available:
                    await self.change("service_fault", reason="识别服务不可用，已暂停。")
                    return
                self.audio_ready_requested_generation = self.session.generation
                if self._stream_ready(self.stream):
                    self.stt_ready = True
                    self._mark_audio_ready()
                elif self.readiness is None:
                    self._prepare_readiness()
        elif event in {"configure", "update_options"}:
            await self.change(
                "update_options",
                **{
                    key: command[key]
                    for key in (
                        "silence_timeout_ms",
                        "prompt_mode",
                        "progress_timeout_ms",
                        "continuous_prompt",
                    )
                    if key in command
                },
            )
        elif event in {"terminate", "end"}:
            await self.change("end")
        elif event in {"pause", "interrupt", "manual_prompt", "postpone_prompt"}:
            await self.change(event)
        elif event == "reposition":
            position = command.get("position")
            if "unit_index" in command:
                index = command["unit_index"]
                if isinstance(index, bool) or not isinstance(index, int):
                    raise SessionError("讲稿单位位置必须是整数。")
                units = self.session.tracker.units
                if index < 0 or index >= len(units):
                    raise SessionError("讲稿单位位置超出范围。")
                position = units[index].start
            await self.change("reposition", position=position)
        elif event == "playback_finished":
            await self.change("playback_finished", prompt_id=command.get("prompt_id"))
        elif event == "playback_failed":
            if self.session.prompt and command.get("prompt_id") == self.session.prompt.id:
                await self.change("service_fault", reason="提示音频无法播放，请检查耳机后继续。")
        else:
            raise SessionError("不支持的会话命令。")

    @staticmethod
    def _can_batch_audio(stream: Any) -> bool:
        # Cloud result deadlines must observe voice before PCM is enqueued.
        # Local inference has no network watchdog and can safely amortize dispatch.
        return callable(getattr(stream, "accept_pcm_batch", None)) and (
            getattr(stream, "effective_provider", None) == "local"
            or bool(getattr(stream, "fallback_active", False))
        )

    async def audio(self, pcm: bytes, generation: int | None = None) -> None:
        await self.audio_batch([pcm], generation)

    async def audio_batch(self, frames: list[bytes], generation: int | None = None) -> None:
        if len(frames) > 1 and not self._can_batch_audio(self.stream):
            # Keep the same ordering for explicit callers as for the socket worker.
            # Even if the first packet falls back, every remaining packet is owned
            # by this generation and no already-sent cloud audio is replayed.
            generation = self.session.generation if generation is None else generation
            for pcm in frames:
                await self.audio(pcm, generation)
            return
        async with self.lock:
            if (
                self.session.state != SessionState.LISTENING
                or not self.audio_ready
                or not self.stream
                or (generation is not None and generation != self.session.generation)
            ):
                return
            if any(not pcm or len(pcm) > 32000 or len(pcm) % 2 for pcm in frames):
                raise SessionError("音频必须是有效的 16 kHz 单声道 PCM16 数据。")
            received = now_ms()
            self.last_frame_ms = received
            generation = self.session.generation
            stream = self.stream
            if self.audio_received_ms is None:
                self.audio_received_ms = received
            if self.audio_processed_ms is None:
                self.session.progress_since_ms = None
            self.session.begin_audio_clock(received)
        if len(frames) == 1 and not await self._observe_packet(
            frames[0], received, generation, stream
        ):
            return
        # One worker call per bounded batch; individual packet hypotheses and
        # endpoint/fallback ordering are retained by the infrastructure adapter.
        provider_before = getattr(stream, "effective_provider", self.provider_name)
        started = time.perf_counter()
        batch = getattr(stream, "accept_pcm_batch", None)
        if len(frames) > 1 and callable(batch):
            updates = await asyncio.to_thread(batch, frames)
        else:

            def infer_packets():
                updates = []
                for pcm in frames:
                    update = stream.accept_pcm(pcm)
                    updates.append(update)
                return updates

            updates = await asyncio.to_thread(infer_packets)
        elapsed = (time.perf_counter() - started) * 1000
        async with self.lock:
            if (
                generation != self.session.generation
                or self.session.state != SessionState.LISTENING
                or self.stream is not stream
            ):
                return
            self.audio_inference_ms = elapsed
            self.audio_batch_frames = sum((len(pcm) + 639) // 640 for pcm in frames)
            self.audio_processed_ms = now_ms()
            if provider_before != getattr(stream, "effective_provider", self.provider_name):
                await self.publish()
        for index, update in enumerate(updates):
            if len(frames) > 1 and not await self._observe_packet(
                frames[index], received, generation, stream
            ):
                return
            await self._accept_update(update, received, generation, stream)

    async def _observe_packet(
        self, packet: bytes, received: int, generation: int, stream: Any
    ) -> bool:
        async with self.lock:
            if (
                generation != self.session.generation
                or self.session.state != SessionState.LISTENING
                or self.stream is not stream
            ):
                return False
            for offset in range(0, len(packet), 640):
                pcm = packet[offset : offset + 640]
                voiced = self.audio_evidence.observe(
                    pcm,
                    stream.is_speech(pcm),
                    vad_is_fallback=getattr(stream, "vad_is_fallback", False),
                )
                changed = self.retract_unsupported_evidence()
                if voiced:
                    notify = getattr(stream, "notify_voice", None)
                    if callable(notify):
                        notify()
                    self.last_voice_ms = received
                    if not self.session.speaking:
                        # Voice activity changes display state, not the immutable
                        # settled evidence owned by an in-flight recovery request.
                        self.last_text = ""
                        self.last_update_final = False
                        self.raw_transcript_is_current = False
                        self.voice_activity_revision += 1
                        self.session.handle("speech_start", received)
                        changed = True
                elif self.session.speaking and self.last_voice_ms is not None:
                    if received - self.last_voice_ms >= 200:
                        self.session.handle("speech_end", self.last_voice_ms)
                        changed = True
                if changed:
                    await self.publish()
            return True

    async def _accept_update(
        self, update: Any, received: int, generation: int, stream: Any
    ) -> None:
        async with self.lock:
            if (
                generation != self.session.generation
                or self.session.state != SessionState.LISTENING
                or self.stream is not stream
            ):
                return
            if not update:
                return
            # VAD clears transient display state, but must never make an old
            # finalized segment eligible for alignment as a fresh readback.
            if (
                update.segment_id < self.last_segment_id
                or update.segment_id <= self.finalized_segment_id
            ):
                return
            if (
                update.text.strip()
                and not update.is_filler_only
                and not self.audio_evidence.allows(
                    update.segment_id,
                    start_ms=getattr(update, "start_ms", None),
                    end_ms=getattr(update, "end_ms", None),
                    is_final=update.is_final,
                )
            ):
                # A cloud hypothesis alone cannot turn live silence into speech
                # or disable prompting at the last reliable script position.
                self.raw_transcript_is_current = True
                self.filtered_text = update.text
                self.filtered_final = update.is_final
                self.filtered_count += 1
                await self.publish()
                return
            if update.text.strip() and not update.is_filler_only:
                if update.segment_id not in self.acoustic_segments:
                    self.acoustic_segments.append(update.segment_id)
            if update.text.strip():
                self.raw_transcript_is_current = True
                self.filtered_text = ""
                self.filtered_final = False
            else:
                self.raw_transcript_is_current = False
            if update.segment_id > self.last_segment_id:
                self.last_text = ""
            if update.text != self.last_text or update.segment_id != self.last_segment_id:
                self.clear_preview()
            self.last_segment_id = update.segment_id
            self.last_update_final = update.is_final
            if update.is_final:
                self.finalized_segment_id = update.segment_id
            if update.text.strip() and not update.is_filler_only:
                hypothesis = (update.segment_id, update.text)
                original = next(
                    (
                        item
                        for item in self.committed_history
                        if item.version == self.refinement_version
                    ),
                    None,
                )
                if (
                    original is not None
                    and original.evidence_id == (generation, update.segment_id)
                    and original.text != update.text
                ):
                    self.cancel_refinement()
                self.pending_hypothesis = (
                    hypothesis if hypothesis != self.settled_hypothesis else None
                )
                if not update.is_final:
                    if self.preview_hypothesis == hypothesis:
                        self.preview_observations += 1
                    else:
                        self.preview_hypothesis = hypothesis
                        self.preview_since_ms = received
                        self.preview_observations = 1
                    if self.preview_partial(received):
                        await self.publish()
            else:
                if self.pending_hypothesis and self.pending_hypothesis[0] == update.segment_id:
                    # Filler/empty updates retract this segment's unsettled words.
                    self.pending_hypothesis = None
                self.clear_preview()
                # Empty/filler final retracts an earlier silence-settled partial
                # from this segment, so its model response is no longer evidence.
                evidence_id = (generation, update.segment_id)
                self.retract_transcript_evidence(evidence_id)
            previous_text = self.last_text
            self.last_text = update.text
            if update.text.strip() and not update.is_filler_only and update.text != previous_text:
                # Text can confirm quiet voice missed by VAD. Identical cumulative
                # hypotheses during silence must not continually restart the clock.
                self.last_voice_ms = received
                if not self.session.speaking:
                    self.voice_activity_revision += 1
                    self.session.handle("speech_start", received)
            if update.text != previous_text:
                await self.publish()
            if update.is_filler_only:
                self.session.handle("filler", received)
                await self.publish()
                return
            if not update.is_final or not update.text.strip():
                return
            if self.settled_hypothesis != (update.segment_id, update.text):
                self.commit_transcript(update.text, received)
            await self.publish()


def runtimes(application: Any) -> dict[str, SessionRuntime]:
    if not hasattr(application.state, "sessions"):
        with application.state.model_config_lock:
            if not hasattr(application.state, "sessions"):
                application.state.sessions = {}
    return application.state.sessions


def get_speech(application: Any) -> Any:
    if not hasattr(application.state, "speech_services"):
        application.state.speech_services = ProviderSpeechServices(application.state.settings)
    return application.state.speech_services


@router.get("/speech/providers")
def speech_providers(request: Request):
    services = get_speech(request.app)
    return {
        "default_provider": getattr(services, "provider_name", "local"),
        "configuration_source": "server-environment",
        "providers": services.provider_options(),
    }


@router.get("/speech/status")
def speech_status(request: Request):
    return get_speech(request.app).status()


@router.websocket("/session")
async def session_socket(socket: WebSocket) -> None:
    await socket.accept()
    runtime: SessionRuntime | None = None
    services = get_speech(socket.app)
    registry = runtimes(socket.app)

    async def check_idle(current: SessionRuntime, expected_generation: int | None = None) -> None:
        # Frame completion and the periodic timer share the same ownership guard.
        # Queued/inflight voice remains ahead of timeout prompting, but must not
        # hide the small, throttled explanation of why the timeout is waiting.
        async with current.lock:
            if (
                runtime is not current
                or current.socket is not socket
                or (
                    expected_generation is not None
                    and expected_generation != current.session.generation
                )
                or current.session.state != SessionState.LISTENING
            ):
                return
            timestamp = now_ms()
            if (
                current.audio_ready
                and not current.audio_inflight
                and not current.audio_pending
                and current.audio_queue.empty()
            ):
                received = current.audio_received_ms
                if received is None:
                    # The browser acknowledgement is not a real PCM frame. Give
                    # startup the existing bounded grace, without prompting early.
                    if (
                        current.last_frame_ms is not None
                        and timestamp - current.last_frame_ms > 5000
                    ):
                        await current.change("service_fault", reason="麦克风数据中断，已暂停。")
                elif timestamp - received > 5000:
                    await current.change("service_fault", reason="麦克风数据中断，已暂停。")
                elif (
                    current.session.options.prompt_mode == "progress"
                    or not current.audio_evidence.pending_voice
                ):
                    await current.tick(timestamp)
            # Never serialize a long script/units list for this heartbeat.
            await current.publish_prompt_diagnostics(timestamp)

    async def audio_worker() -> None:
        while True:
            if runtime is None:
                await asyncio.sleep(0.01)
                continue
            current = runtime
            generation, pcm = await current.audio_queue.get()
            frames = current.audio_queue.take_batch(
                (generation, pcm), enabled=current._can_batch_audio(current.stream)
            )
            current.audio_inflight = True
            try:
                if len(frames) == 1:
                    await current.audio(pcm, generation)
                else:
                    await current.audio_batch(frames, generation)
            except (SpeechUnavailableError, RuntimeError):
                async with current.lock:
                    if (
                        generation == current.session.generation
                        and current.session.state == SessionState.LISTENING
                    ):
                        await current.change("service_fault", reason="识别服务失败，已暂停。")
            except SessionError as error:
                await socket.send_json({"type": "error", "message": str(error)})
            finally:
                current.audio_inflight = False
                current.audio_pending = not current.audio_queue.empty()
            await check_idle(current, generation)

    async def ticker() -> None:
        while True:
            await asyncio.sleep(0.05)
            current = runtime
            if current:
                await check_idle(current)

    timer = asyncio.create_task(ticker())
    audio_task = asyncio.create_task(audio_worker())
    try:
        while True:
            message = await socket.receive()
            if message["type"] == "websocket.disconnect":
                break
            try:
                if "bytes" in message and message["bytes"] is not None:
                    if runtime and runtime.session.state == SessionState.LISTENING:
                        pcm = message["bytes"]
                        if not pcm or len(pcm) > 32000 or len(pcm) % 2:
                            raise SessionError("音频必须是有效的 16 kHz 单声道 PCM16 数据。")
                        try:
                            runtime.audio_received_ms = now_ms()
                            runtime.audio_pending = True
                            runtime.audio_queue.put_nowait(
                                (runtime.session.generation, message["bytes"])
                            )
                        except asyncio.QueueFull:
                            runtime.audio_pending = False
                            async with runtime.lock:
                                await runtime.change(
                                    "service_fault",
                                    reason="音频处理积压，已暂停，请检查服务器性能。",
                                )
                    continue
                command = json.loads(message.get("text") or "{}")
                if not isinstance(command, dict):
                    raise SessionError("会话命令必须为 JSON 对象。")
                if command.get("type") == "prepare":
                    # Serialize script lookup and registration with deletion and
                    # profile activation. There is no await while this lock is held.
                    with socket.app.state.model_config_lock:
                        if runtime and runtime.session.state != SessionState.TERMINATED:
                            raise SessionError("请先结束当前会话再准备新的讲稿。")
                        if runtime:
                            registry.pop(runtime.session.id, None)
                        with Session(socket.app.state.database_engine) as database:
                            script = find_script(database, command.get("script_id"))
                            if script is None:
                                raise SessionError("请先选择一份有效讲稿。")
                            engine = SpeechSession(
                                script.id,
                                script.content,
                                command.get(
                                    "silence_timeout_ms",
                                    socket.app.state.settings.default_silence_timeout_ms,
                                ),
                                prompt_mode=command.get(
                                    "prompt_mode", socket.app.state.settings.default_prompt_mode
                                ),
                                progress_timeout_ms=command.get(
                                    "progress_timeout_ms",
                                    socket.app.state.settings.default_progress_timeout_ms,
                                ),
                                continuous_prompt=command.get(
                                    "continuous_prompt",
                                    socket.app.state.settings.default_continuous_prompt,
                                ),
                            )
                            profile = socket.app.state.model_configs.active_snapshot()
                            settings = clone_model_settings(
                                socket.app.state.settings, profile["settings"]
                            )
                            provider_name = command.get("stt_provider", settings.stt_provider)
                            options = (
                                services.provider_options()
                                if hasattr(services, "provider_options")
                                else [{"id": "local", "available": True}]
                            )
                            if provider_name not in {item["id"] for item in options}:
                                raise SessionError("未配置或未授权该语音 Provider。")
                            runtime = SessionRuntime(
                                engine,
                                script.name,
                                services,
                                provider_name,
                                model_settings=settings,
                                model_config=profile,
                            )
                            runtime.socket = socket
                            registry[engine.id] = runtime
                    audio_task.cancel()
                    audio_task = asyncio.create_task(audio_worker())
                    await runtime.publish()
                elif command.get("type") == "attach":
                    attached = registry.get(command.get("session_id"))
                    if attached is None:
                        raise SessionError("原会话已不可用，请重新准备演讲。")
                    if attached.socket and attached.socket is not socket:
                        raise SessionError("此会话已有连接。")
                    runtime = attached
                    runtime.socket = socket
                    audio_task.cancel()
                    audio_task = asyncio.create_task(audio_worker())
                    await runtime.publish()
                elif runtime:
                    async with runtime.lock:
                        await runtime.control(command)
                else:
                    raise SessionError("请先准备演讲会话。")
            except SpeechUnavailableError:
                if runtime:
                    async with runtime.lock:
                        await runtime.change(
                            "service_fault", reason="语音服务不可用，请检查后继续。"
                        )
                await socket.send_json({"type": "error", "message": "语音服务不可用。"})
            except (SessionError, ValidationError, ValueError, TypeError) as error:
                await socket.send_json({"type": "error", "message": str(error)})
    except WebSocketDisconnect:
        pass
    finally:
        # Detach before awaiting cancelled workers, so a reconnect never sees an
        # already closed socket as the active owner.
        if runtime and runtime.socket is socket:
            runtime.socket = None
            runtime.cancel_prompt()
            runtime.release_stream()
            runtime.session.handle(
                "service_fault",
                now_ms(),
                reason="连接中断，等待用户继续。",
                transition_trigger="ws_disconnected",
            )
            if runtime.session.state == SessionState.TERMINATED:
                with socket.app.state.model_config_lock:
                    registry.pop(runtime.session.id, None)
        timer.cancel()
        audio_task.cancel()
        await asyncio.gather(timer, audio_task, return_exceptions=True)
