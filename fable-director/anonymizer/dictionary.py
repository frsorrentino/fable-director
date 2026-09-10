"""Dictionary engine: per-project lists (people, organisations, domains,
e-mails) generated outside the plugin. Safety net after rules and NER.

Matching: the whole entry, casefolded, on word boundaries; for PERSONA also
single tokens of >= 4 letters when the occurrence starts with a capital
(so `Rossi` is redacted, the colour `rossi` is not); for ORG also the stem
without the company suffix (`Ortensia Web S.r.l.s.` -> `Ortensia Web`).
"""
import re
from typing import List

from .spans import Mask, Span

_SUFFIX = re.compile(r"(?i)[\s,]+(?:s\.?\s?r\.?\s?l\.?\s?s?\.?|s\.?\s?p\.?\s?a\.?|s\.?\s?n\.?\s?c\.?|s\.?\s?a\.?\s?s\.?|s\.?\s?s\.?|srls?|spa|snc|sas|soc\.?\s+coop\.?|societ[àa]\s+cooperativa|coop\.?|onlus|ltd\.?|gmbh|llc|inc\.?|s\.?\s?c\.?\s?a\s?r\.?\s?l\.?|s\.?\s?c\.?\s?s\.?|di\s+[A-Za-zÀ-ÿ'’ ]+)\s*$")
_PARTICLES = {"di", "de", "del", "della", "dei", "degli", "delle", "da", "van", "von", "la", "le", "lo"}
_ORDER = ["EMAIL", "DOMINIO", "ORG", "PERSONA"]


def stems(org: str) -> List[str]:
    """Casefolded forms of an organisation: full and without suffix."""
    full = re.sub(r"\s+", " ", org.strip()).casefold()
    out = [full]
    short = full
    for _ in range(2):
        s2 = _SUFFIX.sub("", short).strip(" ,")
        if s2 and s2 != short and len(s2) >= 3:
            short = s2
        else:
            break
    if short != full:
        out.append(short)
    return out


def _entries(config: dict) -> dict:
    d = config.get("dictionary", {})
    out = {}
    for cat in _ORDER:
        vals = [str(v).strip() for v in d.get(cat, []) if str(v).strip()]
        out[cat] = list(dict.fromkeys(vals))
    return out


_cache = {}


def _patterns(config: dict):
    entries = _entries(config)
    key = tuple((c, tuple(v)) for c, v in entries.items())
    if key in _cache:
        return _cache[key]
    pats = []  # (cat, regex, needs_capital)
    for cat, vals in entries.items():
        forms = set()
        tokens = set()
        for v in vals:
            if cat == "ORG":
                forms.update(stems(v))
            else:
                forms.add(re.sub(r"\s+", " ", v).casefold())
            if cat == "PERSONA":
                for t in re.split(r"[\s'’-]+", v):
                    t = t.strip(".,")
                    if len(t) >= 4 and t.casefold() not in _PARTICLES:
                        tokens.add(t.casefold())
        forms = {f for f in forms if f}
        if forms:
            alt = "|".join(re.escape(f).replace(r"\ ", r"\s+") for f in sorted(forms, key=len, reverse=True))
            pats.append((cat, re.compile(r"(?<![\w@.-])(?:" + alt + r")(?![\w-])(?!\.[a-z])", re.IGNORECASE), False))
        if tokens:
            alt = "|".join(re.escape(t) for t in sorted(tokens, key=len, reverse=True))
            pats.append((cat, re.compile(r"(?<![\w@.-])(?:" + alt + r")(?![\w-])", re.IGNORECASE), True))
    _cache[key] = pats
    return pats


def find_spans(text: str, config: dict, taken: List[Span]) -> List[Span]:
    wl = {str(w).casefold() for w in config.get("whitelist", [])}
    found: List[Span] = []
    mask = Mask(len(text), taken)
    for cat, rx, needs_capital in _patterns(config):
        for m in rx.finditer(text):
            v = m.group(0)
            if needs_capital and not v[0].isupper():
                continue
            if v.casefold() in wl:
                continue
            if not mask.free(m.start(), m.end()):
                continue
            mask.take(m.start(), m.end())
            found.append(Span(m.start(), m.end(), cat, v, "dictionary"))
    return sorted(found, key=lambda s: s.start)
