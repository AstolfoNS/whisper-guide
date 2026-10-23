"""Isolated browser-test server: deterministic speech doubles, no external requests."""

import io
import os
import tempfile
import wave
from pathlib import Path

from app.config import Settings
from app.infrastructure.speech import RecognitionUpdate
from app.main import create_app


class BrowserStream:
    available = True

    def __init__(self):
        self.frames = 0

    def is_speech(self, pcm):
        return self.frames < 8

    def accept_pcm(self, pcm):
        self.frames += 1
        if self.frames == 3:
            return RecognitionUpdate("大家好", is_final=True, segment_id=0)
        return None

    def close(self):
        pass


class BrowserSpeech:
    def status(self, provider_name=None):
        return {
            "stt": "ready",
            "vad": "ready",
            "tts": "ready",
            "provider": "local",
            "providers": self.provider_options(),
        }

    def provider_options(self):
        return [{"id": "local", "label": "本地", "available": True}]

    def create_stream(self, **kwargs):
        return BrowserStream()

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
app.state.speech_services = BrowserSpeech()


@app.post("/mock/chat/completions")
async def mock_llm():
    return {"choices": [{"message": {"content": "OK"}}]}
