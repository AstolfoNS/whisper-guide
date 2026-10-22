# Speech provider integration notes

This temporary implementation contract documents the server speech adapter in
`backend-core/app/speech.py` for the parallel session API implementation.

```python
services = SpeechServices(settings)
services.status()  # {"stt": "ready"|"unavailable", "vad": "ready"|"fallback", "tts": ...}
stream = services.create_stream()
stream.available  # STT readiness
stream.is_speech(pcm_frame)  # 16 kHz mono PCM16, 10/20/30 ms
update = stream.accept_pcm(pcm_bytes)  # RecognitionUpdate(text, is_final) or None
stream.reset()  # reset recognizer utterance after pause/prompt
stream.close()  # discard stream context
audio = await services.synthesize(text)  # MP3 bytes, in memory only
```

`SpeechUnavailableError` denotes a runtime service failure and should pause an
active session. `create_stream`, `accept_pcm`, and model loading are synchronous
and should execute away from the event loop (`asyncio.to_thread`). A shared
`SpeechServices` instance caches the acoustic model; streams remain per-session.

Provider dependencies and configuration are being added by `speech_backend`:
sherpa-onnx + numpy, webrtcvad-wheels, edge-tts. No microphone audio is written to
disk. Model files are downloaded separately into `backend-core/models/`.
