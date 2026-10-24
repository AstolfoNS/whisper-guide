"""One server-owned state machine per session; microphone data stays in memory."""

import asyncio
import base64
import json
import time
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import clone_model_settings
from app.domain.session import SessionError, SessionState, SpeechSession
from app.infrastructure.llm import LLMSemanticRefiner
from app.infrastructure.speech import ProviderSpeechServices, SpeechUnavailableError
from app.repositories.scripts import get_script as find_script

router = APIRouter(tags=["sessions"])


def now_ms() -> int:
    return time.monotonic_ns() // 1_000_000


class SessionRuntime:
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
        self.transcript_revision = 0
        self.last_filler_only = False
        self.settled_hypothesis: tuple[int, str] | None = None
        self.finalized_segment_id = -1
        self.stream: Any = None
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
        self.audio_queue: asyncio.Queue[tuple[int, bytes]] = asyncio.Queue(maxsize=50)
        self.audio_inflight = False
        self.audio_pending = False

    def snapshot(self) -> dict[str, Any]:
        snapshot = self.session.snapshot()
        snapshot.update(
            session_id=self.session.id,
            script_name=self.script_name,
            script_text=self.session.tracker.text,
            units=[asdict(unit) for unit in self.session.tracker.units],
            position_reliable=self.session.reliable,
            recognized_text=self.last_text or self.session.last_transcript,
            raw_transcript=self.last_text or self.session.raw_transcript,
            aligned_transcript=self.session.aligned_transcript,
            notice=self.session.message,
            stt_provider=self.provider_name,
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
            raw_transcript_is_final=(
                self.last_update_final or (not self.last_text and bool(self.session.raw_transcript))
            ),
            recognition_segment_id=self.last_segment_id,
        )
        return snapshot

    async def send(self, message: dict[str, Any]) -> None:
        if self.socket:
            await self.socket.send_json(message)

    async def publish(self) -> None:
        await self.send({"type": "snapshot", "session": self.snapshot()})

    def release_stream(self) -> None:
        self.cancel_refinement()
        self.transcript_revision += 1
        self.settled_hypothesis = None
        self.finalized_segment_id = -1
        self.last_filler_only = False
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
        self.audio_inflight = False
        self.audio_pending = False

    def cancel_prompt(self) -> None:
        if self.synthesis and self.synthesis is not asyncio.current_task():
            self.synthesis.cancel()
        self.synthesis = None

    def cancel_refinement(self) -> None:
        if self.refinement and self.refinement is not asyncio.current_task():
            self.refinement.cancel()
        self.refinement = None

    def commit_transcript(self, text: str, timestamp: int) -> None:
        """Commit locally first; optional semantic work never holds the audio lock."""
        anchor = self.session.reading_position
        window = self.session.aligner.script_window(anchor)
        self.session.handle("transcript", timestamp, text=text)
        self.settled_hypothesis = (self.last_segment_id, text)
        settings = self.model_settings
        refiner = LLMSemanticRefiner(settings)
        if not refiner.enabled:
            return
        if refiner.align_mode == "fallback" and self.session.reliable:
            return
        self.cancel_refinement()
        self.refinement = asyncio.create_task(
            self.refine_transcript(
                refiner, window, text, self.session.generation, self.transcript_revision
            )
        )

    async def refine_transcript(
        self,
        refiner: LLMSemanticRefiner,
        window: str,
        rough: str,
        generation: int,
        revision: int,
    ) -> None:
        try:
            result = await refiner.refine(window, rough, turn_token=generation)
            async with self.lock:
                if (
                    result is None
                    or result.confidence < 0.68
                    or result.matched_quote not in window
                    or self.session.state != SessionState.LISTENING
                    or self.session.generation != generation
                    or self.transcript_revision != revision
                ):
                    return
                # Domain uses ScriptAligner once more; the model never supplies offsets.
                self.session.handle(
                    "transcript",
                    now_ms(),
                    text=result.matched_quote,
                    raw_text=rough,
                    generation=generation,
                )
                await self.publish()
        except asyncio.CancelledError:
            pass
        finally:
            if self.refinement is asyncio.current_task():
                self.refinement = None

    async def tick(self, timestamp: int) -> None:
        """Settle a pending hypothesis only at the actual silence deadline."""
        due = (
            not self.session.speaking
            and self.session.silence_since_ms is not None
            and timestamp - self.session.silence_since_ms >= self.session.options.silence_timeout_ms
        )
        if (
            due
            and self.last_text
            and not self.last_update_final
            and not self.last_filler_only
            # A late cloud revision can correct a previously unmatched partial.
            # Identical hypotheses stay settled even after a new VAD onset.
            and self.settled_hypothesis != (self.last_segment_id, self.last_text)
        ):
            self.commit_transcript(self.last_text, timestamp)
        before = self.session.state
        self.session.handle("tick", timestamp)
        if self.session.state != before:
            self.release_stream()
            await self.publish()
            if self.session.prompt:
                self.synthesis = asyncio.create_task(self.make_prompt(self.session.prompt.id))

    async def change(self, event: str, **payload: Any) -> None:
        previous_generation = self.session.generation
        self.session.handle(event, now_ms(), **payload)
        if previous_generation != self.session.generation:
            self.release_stream()
            self.cancel_prompt()
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
                await self.change(event, service_available=True)
            except Exception:
                stream.close()
                raise
            self.stream = stream
        elif event == "audio_ready":
            if self.session.state == SessionState.LISTENING:
                if self.ready_generation == self.session.generation:
                    return
                if self.stream is None:
                    self.stream = await self._create_stream()
                if not self.stream.available:
                    await self.change("service_fault", reason="识别服务不可用，已暂停。")
                    return
                self.audio_ready = True
                self.ready_generation = self.session.generation
                self.session.silence_since_ms = now_ms()
                self.last_frame_ms = now_ms()
        elif event in {"configure", "update_options"}:
            await self.change(
                "update_options", silence_timeout_ms=command.get("silence_timeout_ms")
            )
        elif event in {"terminate", "end"}:
            await self.change("end")
        elif event in {"pause", "interrupt", "manual_prompt"}:
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

    async def audio(self, pcm: bytes, generation: int | None = None) -> None:
        async with self.lock:
            if (
                self.session.state != SessionState.LISTENING
                or not self.audio_ready
                or not self.stream
                or (generation is not None and generation != self.session.generation)
            ):
                return
            if not pcm or len(pcm) > 32000 or len(pcm) % 2:
                raise SessionError("音频必须是有效的 16 kHz 单声道 PCM16 数据。")
            received = now_ms()
            self.last_frame_ms = received
            generation = self.session.generation
            stream = self.stream
            voiced = stream.is_speech(pcm)
            changed = False
            if voiced:
                self.last_voice_ms = received
                if not self.session.speaking:
                    self.transcript_revision += 1
                    self.cancel_refinement()
                    self.last_text = ""
                    self.last_segment_id = -1
                    self.last_update_final = False
                    self.session.handle("speech_start", received)
                    changed = True
            elif self.session.speaking and self.last_voice_ms is not None:
                if received - self.last_voice_ms >= 200:
                    self.session.handle("speech_end", self.last_voice_ms)
                    changed = True
            if changed:
                await self.publish()
        # Native inference must not hold the control lock: pause and terminate stay
        # responsive. Results are accepted only for the generation that requested them.
        provider_before = getattr(stream, "effective_provider", self.provider_name)
        update = await asyncio.to_thread(stream.accept_pcm, pcm)
        async with self.lock:
            if (
                generation != self.session.generation
                or self.session.state != SessionState.LISTENING
            ):
                return
            if provider_before != getattr(stream, "effective_provider", self.provider_name):
                await self.publish()
            if not update:
                return
            # VAD clears transient display state, but must never make an old
            # finalized segment eligible for alignment as a fresh readback.
            if (
                update.segment_id < self.last_segment_id
                or update.segment_id <= self.finalized_segment_id
            ):
                return
            if update.segment_id > self.last_segment_id:
                self.last_text = ""
            if update.text != self.last_text or update.segment_id != self.last_segment_id:
                self.transcript_revision += 1
                self.cancel_refinement()
            self.last_segment_id = update.segment_id
            self.last_update_final = update.is_final
            if update.is_final:
                self.finalized_segment_id = update.segment_id
            self.last_filler_only = update.is_filler_only
            previous_text = self.last_text
            self.last_text = update.text
            if update.text.strip() and not update.is_filler_only and update.text != previous_text:
                # Text can confirm quiet voice missed by VAD. Identical cumulative
                # hypotheses during silence must not continually restart the clock.
                self.last_voice_ms = received
                if not self.session.speaking:
                    self.session.handle("speech_start", received)
            if update.text != previous_text:
                await self.publish()
            if update.is_filler_only:
                self.session.handle("filler", received)
                await self.publish()
                return
            if not update.is_final or not update.text.strip():
                return
            self.commit_transcript(update.text, received)
            await self.publish()


def runtimes(application: Any) -> dict[str, SessionRuntime]:
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
        # The periodic task can repeatedly coincide with native inference even
        # when a healthy 20 ms audio pipeline has brief idle gaps. Also check
        # after a completed frame, without overtaking queued voice or controls.
        async with current.lock:
            if (
                runtime is current
                and current.socket is socket
                and (
                    expected_generation is None or expected_generation == current.session.generation
                )
                and current.audio_ready
                and current.session.state == SessionState.LISTENING
                and not current.audio_inflight
                and not current.audio_pending
                and current.audio_queue.empty()
            ):
                timestamp = now_ms()
                if current.last_frame_ms and timestamp - current.last_frame_ms > 5000:
                    await current.change("service_fault", reason="麦克风数据中断，已暂停。")
                else:
                    await current.tick(timestamp)

    async def audio_worker() -> None:
        while True:
            if runtime is None:
                await asyncio.sleep(0.01)
                continue
            current = runtime
            generation, pcm = await current.audio_queue.get()
            current.audio_inflight = True
            try:
                await current.audio(pcm, generation)
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
                        try:
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
                        )
                        # Capture configuration and register the new session as one
                        # atomic operation with respect to profile activation.
                        with socket.app.state.model_config_lock:
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
                    runtime.socket = socket
                    audio_task.cancel()
                    audio_task = asyncio.create_task(audio_worker())
                    registry[engine.id] = runtime
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
                registry.pop(runtime.session.id, None)
        timer.cancel()
        audio_task.cancel()
        await asyncio.gather(timer, audio_task, return_exceptions=True)
