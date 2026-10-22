"""Script units, punctuation boundaries and conservative local fuzzy alignment.

Offsets always refer to the original script so the client can highlight and reposition
without reconstructing normalized recognition text.
"""

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

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
