"""Small, bounded adapters for streaming speech wire protocols.

This module handles bytes and hypotheses only. Session state, privacy consent,
normalization, cursor alignment and network lifecycle belong to its caller.
"""

from __future__ import annotations

import gzip
import json
import struct
import zlib
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

_MAX_FRAME_BYTES = 1024 * 1024
_MAX_PAYLOAD_BYTES = 256 * 1024


@dataclass(frozen=True, slots=True)
class ProtocolTranscript:
    text: str
    is_final: bool
    segment_id: int


class SpeechProtocolError(RuntimeError):
    """A controlled protocol failure, never containing a server response body."""

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class VolcengineSpeechProtocol:
    """Volcengine ASR v3 framing and cumulative-utterance reconciliation.

    ``definite`` closes a speech segment; only a negative package closes the
    connection. Requests omit previously committed sentences using ``single``;
    each current sentence still contains its complete revised hypothesis. Both
    single and full responses are reconciled using timestamps and a monotonic
    fallback index, without retaining historical transcripts.
    """

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        enable_nonstream: bool = True,
        end_window_ms: int = 600,
        request_id: str | None = None,
    ) -> None:
        if sample_rate != 16000:
            raise SpeechProtocolError("云端流式识别仅支持 16000 Hz 音频。")
        if isinstance(end_window_ms, bool) or not isinstance(end_window_ms, int):
            raise SpeechProtocolError("云端分句静音阈值无效。")
        if not 200 <= end_window_ms <= 10000:
            raise SpeechProtocolError("云端分句静音阈值必须在 200 至 10000 ms 之间。")
        self.request_id = _header_value(request_id or str(uuid4()), limit=128)
        self.sample_rate = sample_rate
        self.enable_nonstream = bool(enable_nonstream)
        self.end_window_ms = end_window_ms
        self.last_package = False
        self._segment_id = 0
        self._active_text = ""
        self._seen_utterances = False
        self._committed_start = -1
        self._committed_end = -1
        self._committed_count = 0

    def headers(
        self,
        *,
        credential: str,
        auth_mode: str = "api-key",
        app_id: str = "",
        resource_id: str,
    ) -> dict[str, str]:
        credential = _header_value(credential)
        result = {
            "X-Api-Resource-Id": _header_value(resource_id, limit=512),
            "X-Api-Request-Id": self.request_id,
            "X-Api-Connect-Id": str(uuid4()),
            "X-Api-Sequence": "-1",
        }
        if auth_mode == "api-key":
            result["X-Api-Key"] = credential
        elif auth_mode == "app-token":
            result["X-Api-App-Key"] = _header_value(app_id, limit=512)
            result["X-Api-Access-Key"] = credential
        else:
            raise SpeechProtocolError("不支持的云端语音鉴权方式。")
        return result

    def initial_frame(self) -> bytes:
        request = {
            "user": {"uid": self.request_id},
            "audio": {
                "format": "pcm",
                "codec": "raw",
                "rate": self.sample_rate,
                "bits": 16,
                "channel": 1,
            },
            "request": {
                "model_name": "bigmodel",
                "enable_nonstream": self.enable_nonstream,
                "enable_itn": True,
                "enable_punc": True,
                "enable_ddc": False,
                "show_utterances": True,
                "result_type": "single",
                "end_window_size": self.end_window_ms,
            },
        }
        payload = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode()
        return _frame(1, 0, 1, payload)

    def audio_frame(self, pcm: bytes, *, final: bool = False) -> bytes:
        if not isinstance(pcm, bytes) or len(pcm) % 2 or len(pcm) > _MAX_PAYLOAD_BYTES:
            raise SpeechProtocolError("云端音频帧必须为有界 PCM16 数据。")
        return _frame(2, 2 if final else 0, 0, pcm)

    def decode(self, message: bytes) -> list[ProtocolTranscript]:
        payload, last = _decode_frame(message)
        code = payload.get("code")
        if code is not None and (type(code) is not int or code not in (0, 20000000)):
            safe_code = code if type(code) is int and 0 <= code <= 0xFFFFFFFF else None
            raise SpeechProtocolError("云端语音服务返回失败状态。", code=safe_code)
        if self.last_package:
            return []
        self.last_package = last
        result = payload.get("result")
        if isinstance(result, list):
            # Lists may contain alternative hypotheses; only the first is authoritative.
            result = next((item for item in result if isinstance(item, dict)), {})
        if result is None:
            result = {}
        if not isinstance(result, dict):
            raise SpeechProtocolError("云端语音识别结果格式无效。")
        utterances = result.get("utterances")
        if utterances is not None and not isinstance(utterances, list):
            raise SpeechProtocolError("云端语音分句列表格式无效。")
        updates: list[ProtocolTranscript] = []
        if isinstance(utterances, list) and utterances:
            self._seen_utterances = True
            for index, utterance in enumerate(utterances):
                if not isinstance(utterance, dict):
                    raise SpeechProtocolError("云端语音分句格式无效。")
                text = _text(utterance.get("text"))
                if not text:
                    continue
                start = _time(utterance.get("start_time"))
                end = _time(utterance.get("end_time"))
                if start is not None and end is not None and end < start:
                    raise SpeechProtocolError("云端语音分句时间无效。")
                # Start time remains stable when a partial's end/text are revised.
                # Once committed, even a corrected historical final must not move
                # the local script cursor a second time.
                if start is not None:
                    if start <= self._committed_start:
                        continue
                elif index < self._committed_count:
                    continue
                if end is not None and end <= self._committed_end:
                    continue
                is_final = utterance.get("definite") is True or last
                update = self._update(text, is_final=is_final)
                if update is not None:
                    updates.append(update)
                if is_final:
                    self._committed_count = max(self._committed_count, index + 1)
                    if start is not None:
                        self._committed_start = max(self._committed_start, start)
                    if end is not None:
                        self._committed_end = max(self._committed_end, end)
        elif not self._seen_utterances:
            text = _text(result.get("text"))
            if text:
                update = self._update(text, is_final=last)
                if update is not None:
                    updates.append(update)
        # A final acknowledgement may omit recognition data. Commit the current
        # revised segment once, but never turn cumulative full text into a new one.
        if last and self._active_text:
            update = self._update(self._active_text, is_final=True)
            if update is not None:
                updates.append(update)
        return updates

    def _update(self, text: str, *, is_final: bool) -> ProtocolTranscript | None:
        if not is_final and text == self._active_text:
            return None
        self._active_text = text
        update = ProtocolTranscript(text, is_final, self._segment_id)
        if is_final:
            self._active_text = ""
            self._segment_id += 1
        return update


def _header_value(value: str, *, limit: int = 4096) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > limit
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise SpeechProtocolError("云端语音请求头配置无效。")
    return value.strip()


def _text(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise SpeechProtocolError("云端语音文本格式无效。")
    return value.strip()


def _time(value: Any) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= 0x7FFFFFFF:
        raise SpeechProtocolError("云端语音分句时间无效。")
    return value


def _frame(kind: int, flags: int, serialization: int, payload: bytes) -> bytes:
    compressed = gzip.compress(payload, mtime=0)
    return (
        bytes((0x11, (kind << 4) | flags, (serialization << 4) | 1, 0))
        + struct.pack(">I", len(compressed))
        + compressed
    )


def _decode_frame(message: bytes) -> tuple[dict[str, Any], bool]:
    if not isinstance(message, bytes) or not 8 <= len(message) <= _MAX_FRAME_BYTES:
        raise SpeechProtocolError("云端语音响应帧长度无效。")
    version, header_words = message[0] >> 4, message[0] & 15
    kind, flags = message[1] >> 4, message[1] & 15
    serialization, compression = message[2] >> 4, message[2] & 15
    offset = header_words * 4
    if (
        version != 1
        or header_words < 1
        or offset + 4 > len(message)
        or kind not in (9, 15)
        or flags not in (0, 1, 2, 3)
        or compression not in (0, 1)
        or message[3] != 0
    ):
        raise SpeechProtocolError("云端语音响应协议头无效。")
    last = bool(flags & 2)
    if kind == 15:
        if offset + 8 > len(message):
            raise SpeechProtocolError("云端语音错误帧长度无效。")
        code, size = struct.unpack_from(">II", message, offset)
        offset += 8
        if size != len(message) - offset:
            raise SpeechProtocolError("云端语音错误帧长度无效。")
        raise SpeechProtocolError("云端语音服务返回协议错误。", code=code)
    if serialization != 1:
        raise SpeechProtocolError("云端语音响应序列化格式无效。")
    if flags & 1:
        if offset + 8 > len(message):
            raise SpeechProtocolError("云端语音响应序号缺失。")
        sequence = struct.unpack_from(">i", message, offset)[0]
        last = last or sequence < 0
        offset += 4
    size = struct.unpack_from(">I", message, offset)[0]
    offset += 4
    if size != len(message) - offset or size > _MAX_PAYLOAD_BYTES:
        raise SpeechProtocolError("云端语音响应负载长度无效。")
    data = message[offset:]
    if compression == 1:
        try:
            decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
            data = decompressor.decompress(data, _MAX_PAYLOAD_BYTES + 1)
            if (
                len(data) > _MAX_PAYLOAD_BYTES
                or not decompressor.eof
                or decompressor.unused_data
                or decompressor.unconsumed_tail
            ):
                raise SpeechProtocolError("云端语音响应解压长度无效。")
        except zlib.error:
            raise SpeechProtocolError("云端语音响应压缩格式无效。") from None
    try:
        payload = json.loads(data)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise SpeechProtocolError("云端语音响应 JSON 格式无效。") from None
    if not isinstance(payload, dict):
        raise SpeechProtocolError("云端语音响应 JSON 结构无效。")
    return payload, last
