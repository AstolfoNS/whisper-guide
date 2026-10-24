"""Bounded acoustic support rejects silent ASR hallucinations."""

from array import array

import pytest

from app.infrastructure.audio_evidence import AudioEvidence


def pcm(amplitude=100, milliseconds=20, offset=0):
    return array("h", [offset - amplitude, offset + amplitude] * (8 * milliseconds)).tobytes()


def feed(evidence, milliseconds, *, amplitude=100, vad=True, offset=0):
    return [evidence.observe(pcm(amplitude, offset=offset), vad) for _ in range(milliseconds // 20)]


def test_vad_requires_sixty_ms_and_pending_onset_is_bounded():
    evidence = AudioEvidence()
    assert not evidence.observe(pcm(), True)
    assert evidence.pending_voice
    assert not evidence.observe(pcm(), True)
    assert evidence.pending_voice
    assert evidence.observe(pcm(), True)
    assert not evidence.pending_voice
    assert evidence.audio_ms == 60
    assert evidence.allows(0)
    assert not evidence.observe(bytes(640), False)
    assert not evidence.pending_voice
    assert all(
        not isinstance(value, (bytes, bytearray, memoryview)) for value in vars(evidence).values()
    )


@pytest.mark.parametrize("amplitude,offset", [(0, 0), (0, 16000), (1, 0), (1, 16000), (7, 0)])
def test_silence_dc_and_tiny_background_do_not_authorize_text(amplitude, offset):
    evidence = AudioEvidence()
    assert not any(feed(evidence, 1000, amplitude=amplitude, offset=offset))
    assert not evidence.pending_voice
    assert not evidence.allows(0, 0, 1000, is_final=True)


def test_very_quiet_vad_speech_survives():
    evidence = AudioEvidence()
    assert feed(evidence, 60, amplitude=16) == [False, False, True]
    assert evidence.allows(0, 0, 60)


def test_vad_missed_weak_voice_only_authorizes_transcripts():
    evidence = AudioEvidence()
    assert not any(feed(evidence, 80, amplitude=64, vad=False))
    assert not evidence.pending_voice
    assert evidence.allows(0, 0, 80)


def test_short_clicks_do_not_authorize_text():
    evidence = AudioEvidence()
    feed(evidence, 40)
    feed(evidence, 20, amplitude=0, vad=False)
    feed(evidence, 60, vad=False)
    assert not evidence.allows(0, 0, 120)


def test_old_voice_cannot_authorize_later_timestamped_noise():
    evidence = AudioEvidence()
    feed(evidence, 120)
    feed(evidence, 700, amplitude=0, vad=False)
    assert evidence.allows(0, 0, 120, is_final=True)
    assert not evidence.allows(1, 300, 800, is_final=True)
    assert evidence.allows(0, 0, 800, is_final=True)


def test_latched_final_survives_expired_history():
    evidence = AudioEvidence()
    feed(evidence, 80)
    assert evidence.allows(0, 0, 80)
    feed(evidence, 31000, amplitude=0, vad=False)
    assert evidence.allows(0, 0, 80, is_final=True)
    assert not evidence.allows(1, 0, 80, is_final=True)


@pytest.mark.parametrize("timestamped", [True, False])
def test_late_previous_final_does_not_consume_following_sentence(timestamped):
    evidence = AudioEvidence()
    feed(evidence, 120)
    first = (0, 120) if timestamped else (None, None)
    second = (820, 940) if timestamped else (None, None)
    assert evidence.allows(0, *first)
    feed(evidence, 700, amplitude=0, vad=False)
    feed(evidence, 120)
    assert evidence.allows(0, *first, is_final=True)
    assert evidence.allows(1, *second, is_final=True)


def test_untimed_segments_cannot_reuse_previous_voice_burst():
    evidence = AudioEvidence()
    feed(evidence, 100)
    assert evidence.allows(0)
    feed(evidence, 700, amplitude=0, vad=False)
    assert evidence.allows(0, is_final=True)
    assert not evidence.allows(1, is_final=True)
    feed(evidence, 100)
    assert evidence.allows(1, is_final=True)


def test_delayed_first_untimed_result_claims_earliest_recent_burst():
    evidence = AudioEvidence()
    feed(evidence, 100)
    feed(evidence, 700, amplitude=0, vad=False)
    feed(evidence, 100)
    assert evidence.allows(0, is_final=True)
    assert evidence.allows(1, is_final=True)
    assert not evidence.allows(2, is_final=True)


def test_untimed_support_must_be_recent():
    evidence = AudioEvidence()
    feed(evidence, 100)
    feed(evidence, 3000, amplitude=0, vad=False)
    assert not evidence.allows(0)


def test_durations_are_measured_from_pcm_not_frame_count():
    evidence = AudioEvidence()
    for _ in range(3):
        assert not evidence.observe(pcm(milliseconds=10), True)
    assert not evidence.allows(0, 0, 30)
    for _ in range(3):
        evidence.observe(pcm(milliseconds=10), True)
    assert evidence.allows(0, 0, 60)


def test_large_packets_are_split_into_twenty_ms_features():
    evidence = AudioEvidence()
    assert evidence.observe(pcm(milliseconds=1000), True)
    assert evidence.audio_ms == 1000
    assert evidence.allows(0, 900, 1000)
    assert not evidence.allows(1, 990, 1000)


def test_metadata_and_segment_latches_remain_bounded():
    evidence = AudioEvidence()
    for segment in range(40):
        start = evidence.audio_ms
        feed(evidence, 100)
        assert evidence.allows(segment, int(start), int(evidence.audio_ms), is_final=True)
        feed(evidence, 700, amplitude=0, vad=False)
    assert len(evidence._features) <= 1500
    assert len(evidence._accepted) <= 32
    assert evidence._features[0].start_ms >= evidence.audio_ms - 30000


def test_invalid_frames_and_intervals_do_not_create_support():
    evidence = AudioEvidence()
    assert not evidence.observe(b"", True)
    assert not evidence.observe(b"\x01", True)
    assert evidence.audio_ms == 0
    feed(evidence, 100)
    assert not evidence.allows(0, 90, 10)
    assert not evidence.allows(0, -1, 100)
    assert not evidence.allows(0, 200, 300)


def test_later_untimed_noise_cannot_reuse_a_timed_segments_voice_burst():
    evidence = AudioEvidence()
    feed(evidence, 100)
    assert evidence.allows(0, 0, 100, is_final=True)
    feed(evidence, 700, amplitude=0, vad=False)
    assert not evidence.allows(1, is_final=True)


def test_timed_previous_final_does_not_claim_a_later_untimed_voice_burst():
    evidence = AudioEvidence()
    feed(evidence, 100)
    feed(evidence, 700, amplitude=0, vad=False)
    feed(evidence, 100)
    assert evidence.allows(0, 0, 100, is_final=True)
    assert evidence.allows(1, is_final=True)


@pytest.mark.parametrize("amplitude,vad,duration", [(16, True, 60), (64, False, 80)])
def test_untimed_continuous_voice_can_authorize_new_segments_with_fresh_support(
    amplitude, vad, duration
):
    evidence = AudioEvidence()
    feed(evidence, 120, amplitude=amplitude, vad=vad)
    assert evidence.allows(0)
    assert not evidence.allows(1)
    feed(evidence, duration - 20, amplitude=amplitude, vad=vad)
    assert not evidence.allows(1)
    feed(evidence, 20, amplitude=amplitude, vad=vad)
    assert evidence.allows(1)
    feed(evidence, 700, amplitude=0, vad=False)
    assert not evidence.allows(2)


def test_untimed_delayed_final_does_not_consume_fresh_continuous_voice():
    evidence = AudioEvidence()
    feed(evidence, 120)
    assert evidence.allows(0)
    feed(evidence, 120)
    assert evidence.allows(0, is_final=True)
    assert evidence.allows(1)
    assert not evidence.allows(2)


def test_timed_voice_cannot_immediately_authorize_an_untimed_duplicate():
    evidence = AudioEvidence()
    feed(evidence, 120)
    assert evidence.allows(0, 0, 120)
    assert not evidence.allows(1)
    feed(evidence, 120)
    assert evidence.allows(1)


@pytest.mark.parametrize("partial_times", [(None, 1000), (300, None)])
def test_incomplete_timestamp_cannot_extend_a_new_segment_over_old_voice(partial_times):
    evidence = AudioEvidence()
    feed(evidence, 100)
    assert evidence.allows(0, 0, 100, is_final=True)
    feed(evidence, 700, amplitude=0, vad=False)
    assert not evidence.allows(1, *partial_times, is_final=True)
    feed(evidence, 100)
    assert evidence.allows(1, *partial_times, is_final=True)
