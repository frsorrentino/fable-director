"""Spans: what every engine produces, and the single replacement pass.

Each engine returns spans on the ORIGINAL text. `merge_spans` drops overlaps
(longest wins, then the engine earlier in ENGINE_PRIORITY), `apply_spans`
rewrites the text once, right to left, so offsets never move under an engine.
"""
from typing import Callable, List, NamedTuple


class Span(NamedTuple):
    start: int
    end: int
    cat: str
    value: str
    source: str


ENGINE_PRIORITY = {"columns": 0, "rules": 1, "ner": 2, "dictionary": 3}

# Every category an engine may emit. `restore` only touches placeholders of
# these categories (or of categories present in the map), so MAX_RETRIES_3
# in ordinary text is never mistaken for a placeholder.
CATEGORIES = ("EMAIL", "TEL", "CF", "PIVA", "IBAN", "CARTA", "TARGA", "IP",
              "DOMINIO", "URL", "INDIRIZZO", "CAP_COMUNE", "DATA_NASCITA",
              "PROTOCOLLO", "PERSONA", "ORG", "SECRET")


class Mask:
    """Character mask of taken regions: O(1) overlap checks for engines."""

    def __init__(self, n: int, spans=()):
        self._m = bytearray(n)
        for s in spans:
            self.take(s.start, s.end)

    def free(self, start: int, end: int) -> bool:
        return self._m.find(b"\x01", start, end) == -1

    def take(self, start: int, end: int) -> None:
        self._m[start:end] = b"\x01" * (end - start)


def merge_spans(spans: List[Span], n: int = 0) -> List[Span]:
    """Sorted, non-overlapping spans. Longest wins; ties go to the engine
    with the lower ENGINE_PRIORITY (unknown engines last)."""
    order = sorted(spans, key=lambda s: (-(s.end - s.start),
                                         ENGINE_PRIORITY.get(s.source, 99),
                                         s.start))
    mask = Mask(max(n, max((s.end for s in spans), default=0)))
    taken: List[Span] = []
    for s in order:
        if s.end <= s.start or not mask.free(s.start, s.end):
            continue
        mask.take(s.start, s.end)
        taken.append(s)
    return sorted(taken, key=lambda s: s.start)


def overlaps(span: Span, spans: List[Span]) -> bool:
    return any(span.start < t.end and t.start < span.end for t in spans)


def apply_spans(text: str, spans: List[Span], placeholder: Callable[[Span], str]) -> str:
    """Replace every span with placeholder(span). `spans` must be merged.
    Placeholders are assigned in reading order, so [CAT_1] is the first
    occurrence in the text; the rebuild is a single join."""
    parts = []
    pos = 0
    for s in sorted(spans, key=lambda s: s.start):
        parts.append(text[pos:s.start])
        parts.append(placeholder(s))
        pos = s.end
    parts.append(text[pos:])
    return "".join(parts)
