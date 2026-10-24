"""Script units, punctuation boundaries and conservative local fuzzy alignment.

Offsets always refer to the original script so the client can highlight and reposition
without reconstructing normalized recognition text.
"""

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

try:
    from pypinyin import Style, lazy_pinyin
except ImportError:  # pragma: no cover - optional during bootstrap
    Style = None
    lazy_pinyin = None

BOUNDARIES = frozenset("，,。.？?！!；;")
_WORD = re.compile(r"[A-Za-z]+(?:['’\-][A-Za-z]+)*")
_NUMBER = re.compile(r"[0-9]+(?:\.[0-9]+)*(?:[%％])?")


@dataclass(frozen=True, slots=True)
class ContentUnit:
    text: str
    start: int
    end: int
    normalized: str


@dataclass(frozen=True, slots=True)
class MatchResult:
    position: int
    unit_index: int
    confidence: float
    matched_text: str = ""


@dataclass(frozen=True, slots=True)
class PromptSegment:
    text: str
    start: int
    end: int
    next_position: int


def content_units(text: str) -> list[ContentUnit]:
    """Keep Chinese characters, English words and decimal numbers indivisible."""
    units: list[ContentUnit] = []
    position = 0
    while position < len(text):
        character = text[position]
        match = _NUMBER.match(text, position) or _WORD.match(text, position)
        if match:
            end = match.end()
        elif unicodedata.category(character).startswith(("L", "N")):
            end = position + 1
        else:
            position += 1
            continue
        value = text[position:end]
        units.append(ContentUnit(value, position, end, value.casefold().replace("’", "'")))
        position = end
    return units


def _is_boundary(text: str, position: int) -> bool:
    character = text[position]
    if character not in BOUNDARIES:
        return False
    if character == "." and 0 < position < len(text) - 1:
        # Neither a decimal point nor a numeric version separator splits a prompt.
        if text[position - 1].isdigit() and text[position + 1].isdigit():
            return False
    return True


class ScriptTracker:
    def __init__(self, text: str) -> None:
        self.text = text
        self.units = content_units(text)
        if not self.units:
            raise ValueError("讲稿必须包含可朗读的文字、单词或数字。")

    def unit_index_at(self, position: int) -> int:
        """Snap inside a unit to its beginning, otherwise take the next unit."""
        for index, unit in enumerate(self.units):
            if unit.end > position:
                return index
        return len(self.units)

    def snap_position(self, position: int) -> int:
        if position < 0 or position > len(self.text):
            raise ValueError("重新定位的位置超出讲稿范围。")
        index = self.unit_index_at(position)
        return self.units[index].start if index < len(self.units) else len(self.text)

    def segment(self, position: int) -> PromptSegment | None:
        start = self.snap_position(position)
        if start >= len(self.text):
            return None
        end = len(self.text)
        for offset in range(start, len(self.text)):
            if _is_boundary(self.text, offset):
                end = offset + 1
                # Include consecutive punctuation, intervening spaces and closing quotes.
                while end < len(self.text) and (
                    self.text[end].isspace()
                    or _is_boundary(self.text, end)
                    or self.text[end] in "\"'”’」』）》)]"
                ):
                    end += 1
                break
        return PromptSegment(
            text=self.text[start:end].rstrip(),
            start=start,
            end=end,
            next_position=self.snap_position(end),
        )

    def normalize_transcript(self, transcript: str, reading_position: int | None) -> str:
        """Collapse an obvious repeated-prefix artifact only when the script supports it."""
        collapsed = re.sub(r"([^\W\d_])\1{2,}", r"\1", transcript)
        if collapsed == transcript:
            return transcript
        raw_match = self.match(transcript, reading_position)
        collapsed_match = self.match(collapsed, reading_position)
        return collapsed if raw_match is None and collapsed_match is not None else transcript

    def match(self, transcript: str, reading_position: int | None) -> MatchResult | None:
        recognized = content_units(transcript)
        if not recognized:
            return None
        # An utterance can be cumulative; retain enough context while bounding CPU work.
        needle = [unit.normalized for unit in recognized[-64:]]
        anchor = 0 if reading_position is None else self.unit_index_at(reading_position)
        left = max(0, anchor - max(40, len(needle) + 12))
        right = min(len(self.units), anchor + max(80, len(needle) + 12))
        haystack = [unit.normalized for unit in self.units[left:right]]

        # Streaming recognizers often emit an exact cumulative phrase. Resolve
        # every exact occurrence first and choose the one nearest to the cursor;
        # otherwise a fuzzy window with equal scores may select the first repeat.
        exact: list[int] = []
        for start in range(len(haystack) - len(needle) + 1):
            if haystack[start : start + len(needle)] == needle:
                exact.append(left + start + len(needle) - 1)
        if exact:
            end_index = min(exact, key=lambda index: (abs(index - anchor), index < anchor))
            if len(needle) < 3 and abs(end_index - anchor) > 3:
                return None
            return MatchResult(self.units[end_index].start, end_index, 1.0)

        # Compare a bounded set of contiguous windows.  This deliberately favors
        # the occurrence nearest the current reading position when repeated phrases
        # have equal recognition confidence.
        candidates: list[tuple[float, int, int]] = []
        window_lengths = range(max(1, len(needle) - 2), len(needle) + 3)
        for start in range(len(haystack)):
            for length in window_lengths:
                end = start + length
                if end > len(haystack):
                    continue
                score = SequenceMatcher(None, needle, haystack[start:end], autojunk=False).ratio()
                end_index = left + end - 1
                distance = abs(end_index - anchor)
                candidates.append((score, distance, end_index))
        if not candidates:
            return None
        best_confidence = max(item[0] for item in candidates)
        if best_confidence < 0.65:
            return None
        # Prefer the closest equally plausible occurrence to avoid jumping on repeats.
        plausible = [item for item in candidates if item[0] >= best_confidence - 0.035]
        confidence, distance, index = min(plausible, key=lambda item: item[1])
        if len(needle) < 3 and (confidence < 1 or distance > 3):
            return None
        return MatchResult(self.units[index].start, index, confidence)


@dataclass(frozen=True, slots=True)
class AlignmentCandidate:
    start_index: int
    end_index: int
    score: float
    literal_score: float
    phonetic_score: float
    proximity_score: float
    context_score: float
    jump_penalty: float


class ScriptAligner:
    """Constrain final ASR text to the local script window.

    The aligner treats the script as authoritative. Recognition text supplies
    evidence through literal and pinyin similarity; it never becomes the
    committed transcript itself.
    """

    def __init__(
        self,
        tracker: ScriptTracker,
        *,
        search_before: int = 40,
        search_after: int = 80,
        reliable_threshold: float = 0.68,
    ) -> None:
        self.tracker = tracker
        self.search_before = search_before
        self.search_after = search_after
        self.reliable_threshold = reliable_threshold
        self._script_pinyin = [_pinyin_key(unit.text) for unit in tracker.units]

    def script_window(self, reading_position: int | None) -> str:
        """Return only original text inside the bounded alignment search window."""
        units = self.tracker.units
        anchor = 0 if reading_position is None else self.tracker.unit_index_at(reading_position)
        left = max(0, anchor - self.search_before)
        right = min(len(units), anchor + self.search_after)
        if left >= right:
            return ""
        start, end = units[left].start, units[right - 1].end
        # Never split an English/number unit merely to fit a remote context limit.
        return self.tracker.text[start:end] if end - start <= 4096 else ""

    def align(self, transcript: str, reading_position: int | None) -> MatchResult | None:
        recognized = content_units(transcript)
        if not recognized:
            return None
        # Keep fuzzy comparisons at 64 units. A longer segment may extend the
        # window only after its omitted prefix matches nearby script verbatim.
        # Exceptionally long inputs stay uncommitted rather than expanding work
        # without a bound while the session control lock is held.
        if len(recognized) > 256:
            return None
        needle = [unit.normalized for unit in recognized[-64:]]
        needle_pinyin = [_pinyin_key(unit.text) for unit in recognized[-64:]]
        prefix_length = max(0, len(recognized) - len(needle))
        anchor = 0 if reading_position is None else self.tracker.unit_index_at(reading_position)
        left = max(0, anchor - max(self.search_before, len(needle) + 12))
        right = min(
            len(self.tracker.units),
            anchor + max(self.search_after, len(needle) + 12),
        )
        starts = range(left, right)
        if prefix_length:
            prefix = [unit.normalized for unit in recognized[:prefix_length]]
            starts = [
                start + prefix_length
                for start in starts
                if [unit.normalized for unit in self.tracker.units[start : start + prefix_length]]
                == prefix
            ]
        candidates: list[AlignmentCandidate] = []
        lengths = range(max(1, len(needle) - 2), len(needle) + 3)
        for start in starts:
            for length in lengths:
                end = start + length
                limit = len(self.tracker.units) if prefix_length else right
                if end > limit:
                    continue
                literal = SequenceMatcher(
                    None,
                    needle,
                    [unit.normalized for unit in self.tracker.units[start:end]],
                    autojunk=False,
                ).ratio()
                phonetic = SequenceMatcher(
                    None,
                    needle_pinyin,
                    self._script_pinyin[start:end],
                    autojunk=False,
                ).ratio()
                end_index = end - 1
                full_start = start - prefix_length
                # Sequential reading is close even when a coherent sentence is
                # long: its end is progress, not evidence of a distant jump.
                if full_start <= anchor <= end_index:
                    distance = 0
                elif full_start > anchor:
                    distance = max(0, full_start - anchor - (reading_position is not None))
                else:
                    distance = anchor - end_index
                proximity = max(0.0, 1.0 - min(distance, 80) / 80)
                context = 1.0 if end_index >= anchor else max(0.0, 1.0 - distance / 24)
                jump_penalty = min(0.25, max(0, distance - 8) / 80)
                score = (
                    0.35 * literal
                    + 0.40 * phonetic
                    + 0.18 * proximity
                    + 0.07 * context
                    - jump_penalty
                )
                candidates.append(
                    AlignmentCandidate(
                        full_start,
                        end_index,
                        score,
                        literal,
                        phonetic,
                        proximity,
                        context,
                        jump_penalty,
                    )
                )
        if not candidates:
            return None
        best = max(
            candidates,
            key=lambda candidate: (
                candidate.score,
                candidate.phonetic_score,
                candidate.literal_score,
                -abs(candidate.end_index - anchor),
                candidate.end_index >= anchor,
            ),
        )
        if best.score < self.reliable_threshold:
            return None
        if len(needle) < 3 and abs(best.end_index - anchor) > 3:
            return None
        unit = self.tracker.units[best.end_index]
        start_unit = self.tracker.units[best.start_index]
        return MatchResult(
            position=unit.start,
            unit_index=best.end_index,
            confidence=max(0.0, min(1.0, best.score)),
            matched_text=self.tracker.text[start_unit.start : unit.end],
        )

    def match(self, transcript: str, reading_position: int | None) -> MatchResult | None:
        return self.align(transcript, reading_position)


def _pinyin_key(text: str) -> tuple[str, ...]:
    if lazy_pinyin is None or Style is None:
        return tuple(character.casefold() for character in text)
    try:
        values = lazy_pinyin(text, style=Style.NORMAL, errors="default")
    except Exception:
        values = list(text)
    return tuple(_fuzzy_syllable(value) for value in values)


def _fuzzy_syllable(value: str) -> str:
    value = value.casefold()
    for source, target in (("zh", "z"), ("ch", "c"), ("sh", "s")):
        if value.startswith(source):
            value = target + value[len(source) :]
            break
    for source, target in (("ang", "an"), ("eng", "en"), ("ing", "in")):
        if value.endswith(source):
            value = value[: -len(source)] + target
            break
    if value.startswith("l"):
        value = "n" + value[1:]
    return value
