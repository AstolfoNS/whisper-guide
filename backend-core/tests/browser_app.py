"""Isolated browser-test server: deterministic speech doubles, no external requests."""

import asyncio
import gzip
import io
import json
import os
import struct
import tempfile
import wave
from array import array
from pathlib import Path

from fastapi import Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from app.config import Settings
from app.infrastructure.speech import (
    FallbackSpeechStream,
    ProviderSpeechServices,
    RecognitionUpdate,
)
from app.main import create_app


class BrowserStream:
    available = True
    effective_provider = "local"

    def __init__(self, *, silent=False):
        self.frames = 0
        self.silent = silent
        self.acoustic_vad = False

    def is_speech(self, pcm):
        if self.acoustic_vad:
            # Selected speech scenarios explicitly label their finite synthetic
            # tones as voice. Zero PCM still remains unvoiced after each burst.
            samples = array("h", pcm)
            return (
                bool(samples) and sum(value * value for value in samples) >= len(samples) * 128**2
            )
        return not self.silent and self.frames < 8

    def accept_pcm(self, pcm):
        self.frames += 1
        if not self.silent and self.frames == 3:
            return RecognitionUpdate("大家好", is_final=True, segment_id=0)
        return None

    def close(self):
        pass


class BrowserSpeech(ProviderSpeechServices):
    silent = False

    def _local_stt_ready(self):
        return True

    def status(self, provider_name=None):
        result = super().status(provider_name)
        result.update(stt="ready", vad="ready", tts="ready", detail="")
        return result

    def create_stream(self, provider_name=None, turn_token=None):
        local = BrowserStream(silent=self.silent)
        if (provider_name or self.provider_name) == "local":
            return local
        remote = self.registry.provider(provider_name or self.provider_name)
        if remote is None or not remote.configured:
            return local
        local.acoustic_vad = any(
            marker in remote.config.endpoint
            for marker in (
                "/mock/asr/pending/",
                "/mock/asr/recovery/",
                "/mock/asr/noise/",
                "/mock/asr/revising/",
                "/mock/asr/ready-delay/",
            )
        )
        return FallbackSpeechStream(remote.create_stream(vad=None, turn_token=turn_token), local)

    async def synthesize(self, text):
        output = io.BytesIO()
        with wave.open(output, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(bytes(16000 * 2 * 6))
        return output.getvalue()


temporary_data = (
    tempfile.TemporaryDirectory(prefix="whisper-guide-browser-")
    if not os.environ.get("WHISPER_GUIDE_BROWSER_DATA")
    else None
)
data_dir = Path(temporary_data.name if temporary_data else os.environ["WHISPER_GUIDE_BROWSER_DATA"])
app = create_app(Settings(environment="test", data_dir=data_dir, _env_file=None))
app.state.speech_services = BrowserSpeech(app.state.settings)


@app.post("/mock/chat/completions")
async def mock_llm(request: Request):
    payload = await request.json()
    text = "OK"
    if payload.get("messages", [{}])[0].get("role") == "system":
        text = json.dumps({"matched_quote": "大家好", "confidence": 0.99})
    return {"choices": [{"message": {"content": text}}]}


@app.get("/mock/models")
async def mock_models(request: Request):
    if request.headers.get("authorization") != "Bearer synthetic-browser-discovery-key":
        return JSONResponse(
            status_code=401, content={"error": {"message": "mock credentials rejected"}}
        )
    return {
        "data": [
            {"id": "demo-accurate-model", "display_name": "精确文本模型"},
            {"id": "demo-fast-model", "display_name": "快速文本模型"},
            {"id": "demo-fast-model", "private": "synthetic-browser-discovery-key"},
            {"id": "synthetic-browser-discovery-key"},
        ]
    }


def mock_asr_frame(payload, final=False):
    body = gzip.compress(json.dumps(payload).encode(), mtime=0)
    return (
        bytes((0x11, 0x93 if final else 0x91, 0x11, 0))
        + struct.pack(">iI", -1 if final else 1, len(body))
        + body
    )


@app.websocket("/mock/asr/bigmodel_async")
@app.websocket("/mock/asr/revising/bigmodel_async")
@app.websocket("/mock/asr/noise/bigmodel_async")
@app.websocket("/mock/asr/pending/bigmodel_async")
@app.websocket("/mock/asr/recovery/bigmodel_async")
@app.websocket("/mock/asr/ready-delay/bigmodel_async")
@app.websocket("/mock/asr/boundary/bigmodel_async")
@app.websocket("/mock/asr/complete/bigmodel_async")
async def mock_asr(socket: WebSocket):
    if socket.headers.get("x-api-key") != "synthetic-browser-asr-key":
        await socket.close(code=1008)
        return
    if "/ready-delay/" in socket.url.path:
        await asyncio.sleep(1.25)
    await socket.accept()
    packets = 0
    try:
        await socket.receive_bytes()
        await socket.send_bytes(mock_asr_frame({}))
        while True:
            frame = await socket.receive_bytes()
            if frame[1] & 2:
                await socket.send_bytes(mock_asr_frame({}, final=True))
                return
            packets += 1
            if "/ready-delay/" in socket.url.path:
                hypotheses = ["我将", "我将继续", "我将继续立足", "我将继续立足岗位"]
                if packets <= 5:
                    await socket.send_bytes(
                        mock_asr_frame(
                            {
                                "result": {
                                    "utterances": [
                                        {
                                            "text": hypotheses[min(packets - 1, 3)],
                                            "start_time": 0,
                                            "end_time": 1000,
                                            "definite": packets == 5,
                                        }
                                    ]
                                }
                            }
                        )
                    )
                continue
            if "/recovery/" in socket.url.path:
                utterances = {
                    2: ("这段背景声音无法匹配讲稿", 0, 400),
                    5: ("我们继续介绍实时讲稿定位的工作流程", 600, 1000),
                    8: ("系统需要根据后续正文恢复可靠位置", 1200, 1600),
                }
                if packets in utterances:
                    text, start_ms, end_ms = utterances[packets]
                    await socket.send_bytes(
                        mock_asr_frame(
                            {
                                "result": {
                                    "utterances": [
                                        {
                                            "text": text,
                                            "start_time": start_ms,
                                            "end_time": end_ms,
                                            "definite": True,
                                        }
                                    ]
                                }
                            }
                        )
                    )
                continue
            if "/pending/" in socket.url.path:
                if packets <= 2:
                    await socket.send_bytes(
                        mock_asr_frame(
                            {
                                "result": {
                                    "utterances": [
                                        {
                                            "text": "大家好",
                                            "start_time": 0,
                                            "end_time": 400,
                                            "definite": False,
                                        }
                                    ]
                                }
                            }
                        )
                    )
                continue
            if "/boundary/" in socket.url.path or "/complete/" in socket.url.path:
                if packets <= 2:
                    text = (
                        "大家好。谢谢大家。"
                        if "/complete/" in socket.url.path
                        else "大家好，我宣布，从今天开始，我要好好调整自己，认真奔赴目标"
                    )
                    await socket.send_bytes(
                        mock_asr_frame(
                            {
                                "result": {
                                    "utterances": [
                                        {
                                            "text": text,
                                            "start_time": 0,
                                            "end_time": 600,
                                            "definite": packets == 2,
                                        }
                                    ]
                                }
                            }
                        )
                    )
                continue
            if "/noise/" in socket.url.path:
                if packets in {1, 3, 6}:
                    noise = packets == 6
                    await socket.send_bytes(
                        mock_asr_frame(
                            {
                                "result": {
                                    "utterances": [
                                        {
                                            "text": "点"
                                            if noise
                                            else "我会坚持自律"
                                            if packets == 1
                                            else "我会坚持自律，好好",
                                            "start_time": 800 if noise else 0,
                                            "end_time": 1000 if noise else 600,
                                            "definite": packets != 1,
                                        }
                                    ]
                                }
                            }
                        )
                    )
                continue
            if "/revising/" in socket.url.path:
                if packets in {1, 15}:
                    await socket.send_bytes(
                        mock_asr_frame(
                            {
                                "result": {
                                    "utterances": [
                                        {
                                            "text": "完全不相干的文字"
                                            if packets == 1
                                            else "大甲好",
                                            "start_time": 0,
                                            "end_time": 600,
                                            "definite": False,
                                        }
                                    ]
                                }
                            }
                        )
                    )
                continue
            if packets <= 2:
                await socket.send_bytes(
                    mock_asr_frame(
                        {
                            "result": {
                                "utterances": [
                                    {
                                        "text": "大甲" if packets == 1 else "大甲好",
                                        "start_time": 0,
                                        "end_time": 600,
                                        "definite": packets == 2,
                                    }
                                ]
                            }
                        }
                    )
                )
    except WebSocketDisconnect:
        return


@app.post("/mock/silence")
async def silence_mode(payload: dict[str, bool]):
    app.state.speech_services.silent = payload.get("enabled") is True
    return {"enabled": app.state.speech_services.silent}
