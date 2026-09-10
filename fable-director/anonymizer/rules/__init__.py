"""Rule packs: regex + optional validator, cascaded in a fixed order.

A Rule fires on the ORIGINAL text; matches overlapping a span already taken
(by an earlier engine or an earlier rule) are dropped, so a P.IVA never comes
back as a phone number and an e-mail host never becomes a DOMINIO.
"""
import importlib
import re
from typing import Callable, List, NamedTuple, Optional

from ..spans import Mask, Span


class Rule(NamedTuple):
    cat: str
    regex: "re.Pattern"
    validator: Optional[Callable]
    group: int                 # 0 = whole match is the value
    needs_text: bool = False   # validator(match, text) instead of validator(value)
    match_check: bool = False  # validator(match) instead of validator(value)
    option: Optional[str] = None  # rules.<option> must be truthy for the rule to run


# Category order across packs: first come, first served on overlaps.
ORDER = ["EMAIL", "URL", "IBAN", "CF", "PIVA", "CARTA", "DATA_NASCITA", "PROTOCOLLO",
         "INDIRIZZO", "CAP_COMUNE", "TEL", "TARGA", "IP"]


def load_packs(names) -> List[Rule]:
    rules: List[Rule] = []
    for n in names:
        mod = importlib.import_module(f".{n}", __name__)
        rules.extend(mod.RULES)
    rank = {c: i for i, c in enumerate(ORDER)}
    return sorted(rules, key=lambda r: rank.get(r.cat, 99))


_cache = {}


def _rules_for(cfg) -> List[Rule]:
    key = tuple(cfg.get("packs", ["base", "it"]))
    if key not in _cache:
        _cache[key] = load_packs(key)
    return _cache[key]


def host_whitelisted(host: str, whitelist) -> bool:
    h = host.lower().strip(".")
    for w in whitelist:
        w = str(w).lower().strip(".")
        if "." not in w:
            continue
        if h == w or h.endswith("." + w):
            return True
    return False


def _enabled(rule: Rule, ropts: dict) -> bool:
    if rule.option == "tel_no_context":
        return not ropts.get("tel_requires_context", True)
    if rule.cat == "URL":
        return ropts.get("url", "domain-only") != "off"
    return True


def find_spans(text: str, config: dict, taken: List[Span]) -> List[Span]:
    ropts = config.get("rules", {})
    whitelist = config.get("whitelist", [])
    url_mode = ropts.get("url", "domain-only")
    found: List[Span] = []
    mask = Mask(len(text), taken)
    wl_values = {str(w).casefold() for w in whitelist}
    for rule in _rules_for(config):
        if not _enabled(rule, ropts):
            continue
        for m in rule.regex.finditer(text):
            if rule.group:
                value = m.group(rule.group)
                if value is None:
                    continue
                start, end = m.start(rule.group), m.end(rule.group)
            else:
                value, start, end = m.group(0), m.start(), m.end()
            cat = rule.cat
            if cat == "URL":
                host = m.group(1)
                if host_whitelisted(host, whitelist):
                    continue
                if url_mode == "domain-only":
                    cat, value, start, end = "DOMINIO", host, m.start(1), m.end(1)
                else:
                    value, start, end = m.group(0), m.start(), m.end()
            if rule.validator:
                if rule.needs_text:
                    ok = rule.validator(m, text)
                elif rule.match_check:
                    ok = rule.validator(m)
                else:
                    ok = rule.validator(value)
                if not ok:
                    continue
            raw = text[start:end]
            start += len(raw) - len(raw.lstrip())
            end -= len(raw) - len(raw.rstrip())
            value = text[start:end]
            if not value or value.casefold() in wl_values:
                continue
            if not mask.free(start, end):
                continue
            mask.take(start, end)
            found.append(Span(start, end, cat, value, "rules"))
    return sorted(found, key=lambda s: s.start)
