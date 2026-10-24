"""Bounded acoustic evidence for transcripts; PCM always stays in the caller.

This is an evidence check, not a denoiser or a replacement for VAD/ASR. Quiet
VAD-positive voice is retained, and VAD-missed voice may authorize a transcript
through sustained AC energy. Only confirmed VAD starts the speech clock itself.
"""

from __future__ import annotations

import math
import sys
from array import array
from collections import OrderedDict, deque
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import groupby

_SAMPLE_RATE = 16000
_FRAME_SAMPLES = 320
_HISTORY_MS = 30000
_MAX_FEATURES = 1500
_MAX_SEGMENTS = 32
_VAD_MIN_AC_RMS = 16.0
_WEAK_MIN_AC_RMS = 64.0
_VAD_CONFIRM_MS = 60.0
_WEAK_CONFIRM_MS = 80.0
_BURST_GAP_MS = 200.0
_RECENT_MS = 2500.0
_EPSILON = 1e-6


@dataclass(frozen=True, slots=True)
class _Feature:
    start_ms: float
    end_ms: float
    vad_supported: bool
    energy_supported: bool
    activity_id: int


class AudioEvidence:
    """Keep at most 30 seconds of scalars and 32 accepted segment latches.

    Provider timestamps refer to the beginning of this stream. Untimed providers
    use recent, unclaimed activity bursts conservatively: a silence of 200 ms
    separates bursts. Continuous speech may authorize later segments only with
    sufficient new evidence after the previous segment's first approval.
    Timed intervals never consume evidence, so delayed finals cannot eat audio
    belonging to the following utterance. A new instance resets a stream/turn.
    """

    def __init__(self) -> None:
        self._samples = 0
        self._features: deque[_Feature] = deque(maxlen=_MAX_FEATURES)
        self._accepted: OrderedDict[int, None] = OrderedDict()
        self._vad_run_ms = 0.0
        self._activity_id = -1
        self._last_activity_end = -math.inf
        self._untimed_activity_watermark = -1
        self._untimed_claimed_until = 0.0

    @property
    def audio_ms(self) -> float:
        return self._samples * 1000 / _SAMPLE_RATE

    @property
    def pending_voice(self) -> bool:
        """A short AC/VAD onset needs its remaining frames before a timer tick."""
        return 0 < self._vad_run_ms < _VAD_CONFIRM_MS - _EPSILON

    def observe(self, pcm: bytes, vad: bool) -> bool:
        """Advance the PCM clock and report confirmed VAD, without retaining PCM."""
        if not pcm or len(pcm) % 2:
            return False
        confirmed = False
        for offset in range(0, len(pcm), _FRAME_SAMPLES * 2):
            samples = array("h", pcm[offset : offset + _FRAME_SAMPLES * 2])
            if sys.byteorder != "little":  # pragma: no cover - deployment is little endian
                samples.byteswap()
            start = self.audio_ms
            self._samples += len(samples)
            end = self.audio_ms
            average = sum(samples) / len(samples)
            variance = sum(value * value for value in samples) / len(samples) - average * average
            ac_rms = math.sqrt(max(0.0, variance))
            vad_supported = bool(vad and ac_rms >= _VAD_MIN_AC_RMS)
            energy_supported = ac_rms >= _WEAK_MIN_AC_RMS
            if vad_supported:
                self._vad_run_ms += end - start
                confirmed = confirmed or self._vad_run_ms >= _VAD_CONFIRM_MS - _EPSILON
            else:
                self._vad_run_ms = 0.0
            if vad_supported or energy_supported:
                if start - self._last_activity_end >= _BURST_GAP_MS - _EPSILON:
                    self._activity_id += 1
                self._last_activity_end = end
            self._features.append(
                _Feature(start, end, vad_supported, energy_supported, self._activity_id)
            )
        cutoff = self.audio_ms - _HISTORY_MS
        while self._features and self._features[0].start_ms < cutoff - _EPSILON:
            self._features.popleft()
        return confirmed

    def allows(
        self,
        segment_id: int,
        start_ms: int | None = None,
        end_ms: int | None = None,
        is_final: bool = False,
    ) -> bool:
        """Latch a supported segment; partials/finals do not consume newer audio.

        ``is_final`` intentionally does not change evidence ownership: cloud
        finalization can arrive after speech from the next sentence has begun.
        """
        del is_final
        if type(segment_id) is not int or segment_id < 0:
            return False
        if segment_id in self._accepted:
            self._accepted.move_to_end(segment_id)
            return True
        if not self._features:
            return False
        if any(
            value is not None and (type(value) is not int or value < 0)
            for value in (start_ms, end_ms)
        ):
            return False
        if start_ms is not None and end_ms is not None:
            start, end = start_ms, end_ms
            if end <= start:
                return False
            activity_id = _support_activity(self._features, start, end)
            if activity_id is None:
                return False
            self._untimed_activity_watermark = max(self._untimed_activity_watermark, activity_id)
            self._untimed_claimed_until = max(self._untimed_claimed_until, min(end, self.audio_ms))
        else:
            cutoff = self.audio_ms - _RECENT_MS
            for activity_id, features in groupby(self._features, key=lambda item: item.activity_id):
                if activity_id <= self._untimed_activity_watermark:
                    continue
                burst = list(features)
                if _support_activity(burst, cutoff, self.audio_ms) is not None:
                    self._untimed_activity_watermark = activity_id
                    self._untimed_claimed_until = max(self._untimed_claimed_until, burst[-1].end_ms)
                    break
            else:
                tail = self._features[-1]
                if (
                    tail.activity_id != self._untimed_activity_watermark
                    or not (tail.vad_supported or tail.energy_supported)
                    or _support_activity(
                        self._features,
                        max(cutoff, self._untimed_claimed_until),
                        self.audio_ms,
                    )
                    != tail.activity_id
                ):
                    return False
                self._untimed_claimed_until = self.audio_ms
        self._accepted[segment_id] = None
        if len(self._accepted) > _MAX_SEGMENTS:
            self._accepted.popitem(last=False)
        return True


def _support_activity(features: Iterable[_Feature], start: float, end: float) -> int | None:
    vad_ms = energy_ms = 0.0
    previous_end = -math.inf
    for feature in features:
        left, right = max(start, feature.start_ms), min(end, feature.end_ms)
        if right <= left:
            continue
        if left > previous_end + _EPSILON:
            vad_ms = energy_ms = 0.0
        duration = right - left
        vad_ms = vad_ms + duration if feature.vad_supported else 0.0
        energy_ms = energy_ms + duration if feature.energy_supported else 0.0
        if vad_ms >= _VAD_CONFIRM_MS - _EPSILON or energy_ms >= _WEAK_CONFIRM_MS - _EPSILON:
            return feature.activity_id
        previous_end = right
    return None
