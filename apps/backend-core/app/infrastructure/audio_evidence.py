"""Bounded acoustic evidence for transcripts; PCM always stays in the caller.

This is an evidence check, not a denoiser or a replacement for VAD/ASR. Quiet
VAD-positive voice is retained, and VAD-missed voice may authorize a transcript
through sustained AC energy above a learned stationary noise floor. Only confirmed
VAD starts the speech clock itself.
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
_NOISE_WINDOW_MS = 600.0
_NOISE_MAX_FEATURES = 100
_NOISE_MAX_VARIATION = 0.12
_NOISE_ENERGY_RATIO = 1.5
_EPSILON = 1e-6


@dataclass(frozen=True, slots=True)
class _Feature:
    start_ms: float
    end_ms: float
    vad_supported: bool
    energy_supported: bool
    activity_id: int


@dataclass(frozen=True, slots=True)
class _Support:
    activity_id: int
    start_ms: float
    end_ms: float
    is_vad: bool


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
        self._accepted: OrderedDict[int, _Support] = OrderedDict()
        self._noise_features: deque[tuple[float, float, float]] = deque(maxlen=_NOISE_MAX_FEATURES)
        self._noise_floor = 0.0
        self._vad_run_ms = 0.0
        self._fallback_run_ms = 0.0
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
        return any(
            0 < duration < _VAD_CONFIRM_MS - _EPSILON
            for duration in (self._vad_run_ms, self._fallback_run_ms)
        )

    def observe(self, pcm: bytes, vad: bool, *, vad_is_fallback: bool = False) -> bool:
        """Report confirmed detector voice, keeping RMS fallback as energy evidence.

        Energy alone cannot distinguish speech from a stationary fan or hum.
        RMS fallback protects a fresh onset but remains subject to stationary
        noise learning. Once learned noise loses energy support it stops the
        fallback clock. A real detector's positive, including quiet voice, stays
        trusted independently of that floor.
        """
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
            detector_voice = bool(vad and not vad_is_fallback)
            vad_supported = bool(detector_voice and ac_rms >= _VAD_MIN_AC_RMS)
            self._observe_noise(start, end, ac_rms, detector_voice)
            energy_supported = ac_rms >= max(
                _WEAK_MIN_AC_RMS, self._noise_floor * _NOISE_ENERGY_RATIO
            )
            if vad_supported:
                self._vad_run_ms += end - start
                confirmed = confirmed or self._vad_run_ms >= _VAD_CONFIRM_MS - _EPSILON
            else:
                self._vad_run_ms = 0.0
            if vad and vad_is_fallback and energy_supported:
                self._fallback_run_ms += end - start
                confirmed = confirmed or self._fallback_run_ms >= _VAD_CONFIRM_MS - _EPSILON
            else:
                self._fallback_run_ms = 0.0
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

    def _observe_noise(self, start: float, end: float, ac_rms: float, vad: bool) -> None:
        """Learn steady energy without detector voice, including RMS-fallback positives.

        The energy-only escape hatch remains available at stream startup and
        above the current floor. Once a whole window proves stationary, revoke
        its provisional energy support, including early noise-only partials.
        This is deliberately narrower than treating all VAD misses as silence.
        """
        if vad:
            self._noise_features.clear()
            return
        self._noise_features.append((start, end, ac_rms))
        while self._noise_features and end - self._noise_features[0][1] >= _NOISE_WINDOW_MS:
            self._noise_features.popleft()
        duration = sum(right - left for left, right, _ in self._noise_features)
        if duration < _NOISE_WINDOW_MS - _EPSILON:
            return
        average = sum((right - left) * rms for left, right, rms in self._noise_features) / duration
        variance = (
            sum((right - left) * (rms - average) ** 2 for left, right, rms in self._noise_features)
            / duration
        )
        if math.sqrt(variance) > max(1.0, average * _NOISE_MAX_VARIATION):
            return
        self._noise_floor = average
        if average < _WEAK_MIN_AC_RMS / _NOISE_ENERGY_RATIO:
            return
        noise_start = self._noise_features[0][0]
        # Retain VAD-supported frames and scalars only. This also removes the
        # first 80 ms of a noise burst instead of leaving it as a reusable anchor.
        self._features = deque(
            (
                _Feature(item.start_ms, item.end_ms, False, False, item.activity_id)
                if not item.vad_supported and item.end_ms > noise_start and item.start_ms < end
                else item
                for item in self._features
            ),
            maxlen=_MAX_FEATURES,
        )
        for segment_id, support in list(self._accepted.items()):
            if not support.is_vad and support.end_ms > noise_start and support.start_ms < end:
                del self._accepted[segment_id]

    def is_supported(self, segment_id: int) -> bool:
        """Whether a previously accepted segment still has an acoustic anchor.

        Stationary-noise learning may withdraw provisional energy-only support
        while an ASR partial is waiting for finalization in the caller.
        """
        return type(segment_id) is int and segment_id in self._accepted

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
        if any(
            value is not None and (type(value) is not int or value < 0)
            for value in (start_ms, end_ms)
        ):
            return False
        if start_ms is not None and end_ms is not None and end_ms <= start_ms:
            return False
        accepted = self._accepted.get(segment_id)
        if accepted is not None:
            # Late finals may add endpoint silence or omit timestamps entirely.
            # They must still refer to their original acoustic anchor, rather
            # than moving an accepted segment into a later silent interval.
            contains_anchor = (start_ms is None or start_ms <= accepted.start_ms + _EPSILON) and (
                end_ms is None or end_ms >= accepted.end_ms - _EPSILON
            )
            if contains_anchor:
                self._accepted.move_to_end(segment_id)
                return True
            support = _support_activity(
                self._features,
                start_ms if start_ms is not None else 0,
                end_ms if end_ms is not None else self.audio_ms,
            )
            if support is None:
                return False
            self._accepted[segment_id] = support
            self._accepted.move_to_end(segment_id)
            return True
        if not self._features:
            return False
        if start_ms is not None and end_ms is not None:
            start, end = start_ms, end_ms
            support = _support_activity(self._features, start, end)
            if support is None:
                return False
            self._untimed_activity_watermark = max(
                self._untimed_activity_watermark, support.activity_id
            )
            self._untimed_claimed_until = max(self._untimed_claimed_until, min(end, self.audio_ms))
        else:
            cutoff = self.audio_ms - _RECENT_MS
            for activity_id, features in groupby(self._features, key=lambda item: item.activity_id):
                if activity_id <= self._untimed_activity_watermark:
                    continue
                burst = list(features)
                support = _support_activity(burst, cutoff, self.audio_ms)
                if support is not None:
                    self._untimed_activity_watermark = activity_id
                    self._untimed_claimed_until = max(self._untimed_claimed_until, burst[-1].end_ms)
                    break
            else:
                tail = self._features[-1]
                support = _support_activity(
                    self._features, max(cutoff, self._untimed_claimed_until), self.audio_ms
                )
                if (
                    tail.activity_id != self._untimed_activity_watermark
                    or not (tail.vad_supported or tail.energy_supported)
                    or support is None
                    or support.activity_id != tail.activity_id
                ):
                    return False
                self._untimed_claimed_until = self.audio_ms
        self._accepted[segment_id] = support
        if len(self._accepted) > _MAX_SEGMENTS:
            self._accepted.popitem(last=False)
        return True


def _support_activity(features: Iterable[_Feature], start: float, end: float) -> _Support | None:
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
        if vad_ms >= _VAD_CONFIRM_MS - _EPSILON:
            return _Support(feature.activity_id, right - vad_ms, right, True)
        if energy_ms >= _WEAK_CONFIRM_MS - _EPSILON:
            return _Support(feature.activity_id, right - energy_ms, right, False)
        previous_end = right
    return None
