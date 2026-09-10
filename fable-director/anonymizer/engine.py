"""The three pure functions every adapter calls. Nothing here knows about
files, hooks or Claude Code.

  scan(text, config)            -> report (counts only, no map touched)
  redact(text, pmap, config)    -> (text with [CAT_N], report)
  restore(text, pmap)           -> (text with values, {"restored", "unknown"})

Report = {"counts": {cat: n}, "by_engine": {engine: n}, "ms": float,
          "chars": n, "columns_skipped": bool, "placeholders_new": n}.
Never a value: reports are safe to log.
"""
import importlib
import time
from typing import Dict, List, Tuple

from . import columns as _columns
from .spans import Span, apply_spans, merge_spans

ENGINES = ("columns", "rules", "dictionary", "ner")


def _engine_module(name: str):
    if name not in ENGINES:
        raise ValueError(f"unknown engine {name!r} (allowed: {', '.join(ENGINES)})")
    return importlib.import_module(f".{name}", __package__)


def collect(text: str, config: dict, engines=None) -> Tuple[List[Span], Dict[str, int]]:
    names = list(engines if engines is not None else config.get("engines", ["columns", "rules", "dictionary"]))
    if "ner" in names and not config.get("ner", {}).get("enabled"):
        names.remove("ner")
    taken: List[Span] = []
    by_engine: Dict[str, int] = {}
    for name in names:
        mod = _engine_module(name)
        new = mod.find_spans(text, config, taken)
        taken = merge_spans(taken + new, len(text))
        by_engine[name] = len(new)
    return taken, by_engine


def _columns_skipped(text: str, names) -> bool:
    if "columns" not in names:
        return False
    fmt = _columns.detect(text)
    return bool(fmt and fmt[0] == "csv" and any(ln.count('"') % 2 for ln in text.split("\n")))


def _report(text, spans, by_engine, t0, config, engines) -> dict:
    counts: Dict[str, int] = {}
    for s in spans:
        counts[s.cat] = counts.get(s.cat, 0) + 1
    names = engines if engines is not None else config.get("engines", [])
    return {"counts": counts, "by_engine": by_engine,
            "ms": round((time.perf_counter() - t0) * 1000, 2), "chars": len(text),
            "columns_skipped": _columns_skipped(text, names)}


def scan(text: str, config: dict, engines=None) -> dict:
    t0 = time.perf_counter()
    spans, by_engine = collect(text, config, engines)
    return _report(text, spans, by_engine, t0, config, engines)


def redact(text: str, pmap, config: dict, engines=None) -> Tuple[str, dict]:
    t0 = time.perf_counter()
    spans, by_engine = collect(text, config, engines)
    before = sum(pmap.counts().values())
    out = apply_spans(text, spans, pmap.placeholder_for)
    rep = _report(text, spans, by_engine, t0, config, engines)
    rep["placeholders_new"] = sum(pmap.counts().values()) - before
    return out, rep


def restore(text: str, pmap) -> Tuple[str, dict]:
    return pmap.restore(text)
