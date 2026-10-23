import gzip
import json
import struct
from uuid import UUID

import pytest

from app.infrastructure.speech_protocols import (
    ProtocolTranscript,
    SpeechProtocolError,
    VolcengineSpeechProtocol,
)


def response(payload, *, flags=1, sequence=1, compressed=True, extensions=b""):
    data = json.dumps(payload, ensure_ascii=False).encode()
    if compressed:
        data = gzip.compress(data)
    frame = bytes((0x11 + len(extensions) // 4, 0x90 | flags, 0x10 | int(compressed), 0))
    frame += extensions
    if flags & 1:
        frame += struct.pack(">i", sequence)
    return frame + struct.pack(">I", len(data)) + data


def utterance(text, *, start=0, end=800, definite=False):
    return {"text": text, "start_time": start, "end_time": end, "definite": definite}


def test_initial_request_matches_pcm_and_preserves_readback():
    protocol = VolcengineSpeechProtocol(request_id="test-request", end_window_ms=600)
    frame = protocol.initial_frame()
    assert frame[:4] == bytes((0x11, 0x10, 0x11, 0))
    assert struct.unpack_from(">I", frame, 4)[0] == len(frame) - 8
    body = json.loads(gzip.decompress(frame[8:]))
    assert body["audio"] == {
        "format": "pcm",
        "codec": "raw",
        "rate": 16000,
        "bits": 16,
        "channel": 1,
    }
    assert body["request"]["show_utterances"] is True
    assert body["request"]["result_type"] == "single"
    assert body["request"]["enable_ddc"] is False
    assert body["request"]["enable_nonstream"] is True
    assert body["request"]["end_window_size"] == 600
    disabled = VolcengineSpeechProtocol(enable_nonstream=False).initial_frame()
    assert json.loads(gzip.decompress(disabled[8:]))["request"]["enable_nonstream"] is False


def test_audio_requests_have_raw_pcm_gzip_and_distinct_final_flag():
    protocol = VolcengineSpeechProtocol()
    pcm = b"\x01\x00" * 3200
    for final, flag in [(False, 0x20), (True, 0x22)]:
        frame = protocol.audio_frame(pcm, final=final)
        assert frame[:4] == bytes((0x11, flag, 0x01, 0))
        assert gzip.decompress(frame[8:]) == pcm
    assert gzip.decompress(protocol.audio_frame(b"", final=True)[8:]) == b""


@pytest.mark.parametrize("pcm", [b"x", b"\x00" * 262146, "unsafe"])
def test_invalid_audio_frames_are_rejected(pcm):
    with pytest.raises(SpeechProtocolError, match="PCM16"):
        VolcengineSpeechProtocol().audio_frame(pcm)


def test_api_key_and_legacy_headers_are_separate_and_have_trace_ids():
    protocol = VolcengineSpeechProtocol()
    headers = protocol.headers(credential="api-secret", resource_id="volc.seedasr.sauc.duration")
    assert headers["X-Api-Key"] == "api-secret"
    assert "X-Api-App-Key" not in headers
    assert "X-Api-Access-Key" not in headers
    UUID(headers["X-Api-Request-Id"])
    UUID(headers["X-Api-Connect-Id"])
    assert headers["X-Api-Sequence"] == "-1"
    headers = protocol.headers(
        credential="access-secret", auth_mode="app-token", app_id="1234", resource_id="asr"
    )
    assert headers["X-Api-App-Key"] == "1234"
    assert headers["X-Api-Access-Key"] == "access-secret"
    assert "X-Api-Key" not in headers


@pytest.mark.parametrize("field", ["credential", "app_id", "resource_id"])
def test_headers_reject_injection_without_echoing_credentials(field):
    args = dict(credential="secret", auth_mode="app-token", app_id="app", resource_id="asr")
    args[field] = "secret\r\nInjected: true"
    with pytest.raises(SpeechProtocolError) as error:
        VolcengineSpeechProtocol().headers(**args)
    assert "secret" not in str(error.value)
    assert "Injected" not in str(error.value)


def test_invalid_auth_mode_and_missing_legacy_app_id_fail_closed():
    protocol = VolcengineSpeechProtocol()
    with pytest.raises(SpeechProtocolError):
        protocol.headers(credential="key", resource_id="asr", auth_mode="unknown")
    with pytest.raises(SpeechProtocolError):
        protocol.headers(credential="key", resource_id="asr", auth_mode="app-token")


def test_partial_revisions_are_replaced_and_definite_does_not_close_connection():
    protocol = VolcengineSpeechProtocol()
    assert protocol.decode(response({"result": {"utterances": [utterance("大")]}})) == [
        ProtocolTranscript("大", False, 0)
    ]
    assert protocol.decode(response({"result": {"utterances": [utterance("大家好")]}})) == [
        ProtocolTranscript("大家好", False, 0)
    ]
    assert protocol.decode(response({"result": {"utterances": [utterance("大家好")]}})) == []
    final = utterance("大家好。", definite=True)
    assert protocol.decode(response({"result": {"utterances": [final]}})) == [
        ProtocolTranscript("大家好。", True, 0)
    ]
    assert not protocol.last_package
    assert protocol.decode(response({"result": {"utterances": [final]}})) == []


def test_full_snapshots_deduplicate_history_but_preserve_real_readback():
    protocol = VolcengineSpeechProtocol()
    first = utterance("大家好。", definite=True)
    second = utterance("今天", start=900, end=1400)
    assert protocol.decode(response({"result": {"utterances": [first, second]}})) == [
        ProtocolTranscript("大家好。", True, 0),
        ProtocolTranscript("今天", False, 1),
    ]
    second = utterance("今天开始。", start=900, end=2000, definite=True)
    repeated = utterance("大家好。", start=2200, end=3000, definite=True)
    assert protocol.decode(response({"result": {"utterances": [first, second, repeated]}})) == [
        ProtocolTranscript("今天开始。", True, 1),
        ProtocolTranscript("大家好。", True, 2),
    ]
    assert protocol.decode(response({"result": {"utterances": [first, second, repeated]}})) == []
    # History corrections after a definite result cannot submit it again.
    revised = utterance("大家好！", end=850, definite=True)
    assert protocol.decode(response({"result": {"utterances": [revised, second, repeated]}})) == []
    assert not any(isinstance(value, (list, set, dict)) for value in vars(protocol).values())


def test_timestamp_free_full_snapshots_use_bounded_ordinal_watermark():
    protocol = VolcengineSpeechProtocol()
    a = {"text": "你好", "definite": True}
    b = {"text": "你好", "definite": False}
    assert protocol.decode(response({"result": {"utterances": [a, b]}})) == [
        ProtocolTranscript("你好", True, 0),
        ProtocolTranscript("你好", False, 1),
    ]
    b["definite"] = True
    assert protocol.decode(response({"result": {"utterances": [a, b]}})) == [
        ProtocolTranscript("你好", True, 1)
    ]
    assert protocol.decode(response({"result": {"utterances": [a, b]}})) == []


@pytest.mark.parametrize("flags,sequence", [(2, 1), (3, -4), (1, -2)])
def test_last_packet_finalizes_active_segment_for_all_wire_conventions(flags, sequence):
    protocol = VolcengineSpeechProtocol()
    protocol.decode(response({"result": {"utterances": [utterance("今天开始")]}}))
    assert protocol.decode(response({}, flags=flags, sequence=sequence)) == [
        ProtocolTranscript("今天开始", True, 0)
    ]
    assert protocol.last_package
    assert protocol.decode(response({}, flags=flags, sequence=sequence)) == []


def test_legacy_text_only_results_are_same_segment_until_last_packet():
    protocol = VolcengineSpeechProtocol()
    assert protocol.decode(response({"result": [{"text": "大家"}]})) == [
        ProtocolTranscript("大家", False, 0)
    ]
    assert protocol.decode(response({"result": {"text": "大家好"}}, compressed=False)) == [
        ProtocolTranscript("大家好", False, 0)
    ]
    assert protocol.decode(response({"result": {"text": "大家好。"}}, flags=2)) == [
        ProtocolTranscript("大家好。", True, 0)
    ]


def test_full_text_is_never_resubmitted_after_utterance_commit():
    protocol = VolcengineSpeechProtocol()
    protocol.decode(response({"result": {"utterances": [utterance("大家好", definite=True)]}}))
    assert protocol.decode(response({"result": {"text": "大家好"}}, flags=2)) == []


def test_acknowledgements_and_extended_headers_are_valid():
    protocol = VolcengineSpeechProtocol()
    assert protocol.decode(response({"code": 20000000}, flags=0, extensions=b"1234")) == []
    assert protocol.decode(response({"code": 0, "result": {}})) == []
    assert not protocol.last_package


@pytest.mark.parametrize("code", [40000001, "bad", True, 20000000.0])
def test_payload_errors_do_not_echo_service_messages(code):
    body = {"code": code, "message": "secret token is xyz", "result": {"text": "injected"}}
    with pytest.raises(SpeechProtocolError) as error:
        VolcengineSpeechProtocol().decode(response(body))
    assert "secret" not in str(error.value)
    assert "injected" not in str(error.value)
    assert error.value.code == (code if type(code) is int else None)


def test_server_error_uses_only_numeric_code():
    body = b"secret-token and upstream debug info"
    frame = bytes((0x11, 0xF0, 0, 0)) + struct.pack(">II", 45000000, len(body)) + body
    with pytest.raises(SpeechProtocolError) as error:
        VolcengineSpeechProtocol().decode(frame)
    assert error.value.code == 45000000
    assert "secret" not in str(error.value)


@pytest.mark.parametrize(
    "frame",
    [
        b"",
        b"123",
        b"x" * (1024 * 1024 + 1),
        bytes((0x21, 0x90, 0x10, 0)) + b"\0" * 4,
        bytes((0x10, 0x90, 0x10, 0)) + b"\0" * 4,
        bytes((0x1F, 0x90, 0x10, 0)) + b"\0" * 4,
        bytes((0x11, 0x80, 0x10, 0)) + b"\0" * 4,
        bytes((0x11, 0x94, 0x10, 0)) + b"\0" * 4,
        bytes((0x11, 0x90, 0x20, 0)) + b"\0" * 4,
        bytes((0x11, 0x90, 0x12, 0)) + b"\0" * 4,
        bytes((0x11, 0x91, 0x10, 0)) + b"\0" * 4,
        bytes((0x11, 0x90, 0x10, 0)) + struct.pack(">I", 100) + b"{}",
        bytes((0x11, 0x90, 0x11, 0)) + struct.pack(">I", 2) + b"{}",
        bytes((0x11, 0xF0, 0, 0)) + b"\0" * 4,
    ],
)
def test_malformed_frames_fail_with_controlled_errors(frame):
    with pytest.raises(SpeechProtocolError):
        VolcengineSpeechProtocol().decode(frame)


def test_gzip_bombs_trailing_members_and_truncation_are_rejected():
    payload = gzip.compress(b"x" * 262145)
    valid = gzip.compress(b"{}")
    for data in (payload, valid + valid, valid[:-1]):
        frame = bytes((0x11, 0x90, 0x11, 0)) + struct.pack(">I", len(data)) + data
        with pytest.raises(SpeechProtocolError):
            VolcengineSpeechProtocol().decode(frame)


@pytest.mark.parametrize("body", [b"invalid secret", b"[]", b"\xff", b"[" * 1500])
def test_invalid_json_is_rejected_without_body_echo(body):
    frame = bytes((0x11, 0x90, 0x10, 0)) + struct.pack(">I", len(body)) + body
    with pytest.raises(SpeechProtocolError) as error:
        VolcengineSpeechProtocol().decode(frame)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize(
    "item",
    [
        "invalid",
        {"text": 123},
        {"text": "x", "start_time": -1},
        {"text": "x", "start_time": True},
        {"text": "x", "start_time": 50, "end_time": 10},
    ],
)
def test_invalid_utterance_shapes_are_rejected(item):
    with pytest.raises(SpeechProtocolError):
        VolcengineSpeechProtocol().decode(response({"result": {"utterances": [item]}}))


@pytest.mark.parametrize("result", ["invalid", 1, {"utterances": "invalid"}])
def test_invalid_result_structures_fail_closed(result):
    with pytest.raises(SpeechProtocolError):
        VolcengineSpeechProtocol().decode(response({"result": result}))


def test_single_snapshots_commit_sequential_sentences_at_same_array_index():
    protocol = VolcengineSpeechProtocol()
    for index, text in enumerate(("大家好。", "今天开始。", "大家好。")):
        start = index * 1000
        partial = utterance(text[:-1], start=start, end=start + 600)
        final = utterance(text, start=start, end=start + 800, definite=True)
        assert protocol.decode(response({"result": {"utterances": [partial]}})) == [
            ProtocolTranscript(text[:-1], False, index)
        ]
        assert protocol.decode(response({"result": {"utterances": [final]}})) == [
            ProtocolTranscript(text, True, index)
        ]
        assert protocol.decode(response({"result": {"utterances": [final]}})) == []
    assert not protocol.last_package
