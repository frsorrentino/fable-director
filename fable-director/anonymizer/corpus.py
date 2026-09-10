"""Annotated corpus: load, validate, score, round-trip.

Layout: <dir>/docs/<id>.txt + <dir>/docs/<id>.spans.json
  {"doc": id, "annotator": "model|human|generator", "reviewed": bool,
   "spans": [{"start", "end", "cat", "text"}]}
Optional <dir>/fd-anonymizer.json: config override (dictionary, whitelist,
columns.extra) merged on top of the effective config when scoring.

A predicted span is a true positive when a truth span of the same category
overlaps it by >= 50% of the shorter one (an address with or without the CAP
must not count as an error). Scores never carry values.
"""
import json
from pathlib import Path
from typing import Dict, List, NamedTuple

from . import engine
from .pmap import PlaceholderMap
from .spans import Span

FORMATTED = frozenset({"EMAIL", "CF", "PIVA", "IBAN", "TEL", "TARGA", "CARTA", "IP", "DOMINIO",
                       "URL", "PROTOCOLLO", "INDIRIZZO", "DATA_NASCITA", "CAP_COMUNE", "SECRET"})
THRESHOLDS = {"recall": 0.99, "precision": 0.90}


class Doc(NamedTuple):
    id: str
    text: str
    spans: List[Span]
    meta: dict


def load(dir_: Path) -> List[Doc]:
    docs_dir = Path(dir_) / "docs"
    out = []
    for txt in sorted(docs_dir.glob("*.txt")):
        ann = txt.with_name(txt.stem + ".spans.json")
        text = txt.read_text(encoding="utf-8")
        meta, spans = {}, []
        if ann.is_file():
            meta = json.loads(ann.read_text(encoding="utf-8"))
            spans = [Span(int(s["start"]), int(s["end"]), s["cat"], s.get("text", text[int(s["start"]):int(s["end"])]), "truth")
                     for s in meta.get("spans", [])]
        out.append(Doc(txt.stem, text, spans, meta))
    return out


def config_override(dir_: Path) -> dict:
    f = Path(dir_) / "fd-anonymizer.json"
    return json.loads(f.read_text()) if f.is_file() else {}


def validate(docs: List[Doc]) -> List[str]:
    """Ids of documents whose annotations do not match their text."""
    bad = []
    for d in docs:
        for s in d.spans:
            if d.text[s.start:s.end] != s.value:
                bad.append(f"{d.id}@{s.start}")
                break
    return bad


def _overlap_ok(a: Span, b: Span) -> bool:
    inter = min(a.end, b.end) - max(a.start, b.start)
    if inter <= 0:
        return False
    return inter / min(a.end - a.start, b.end - b.start) >= 0.5


def evaluate(docs: List[Doc], config: dict, engines=None) -> dict:
    per: Dict[str, dict] = {}
    misses: Dict[str, list] = {}

    def cell(cat):
        return per.setdefault(cat, {"tp": 0, "fp": 0, "fn": 0})

    for d in docs:
        pred, _ = engine.collect(d.text, config, engines)
        used = set()
        for p in pred:
            hit = None
            inside_used = False
            for i, t in enumerate(d.spans):
                if t.cat != p.cat or not _overlap_ok(p, t):
                    continue
                if i in used:
                    # a second prediction inside an already matched truth span
                    # (e.g. name and surname found separately): not an error
                    inside_used = inside_used or (t.start <= p.start and p.end <= t.end)
                    continue
                hit = i
                break
            if hit is None and inside_used:
                continue
            if hit is None:
                cell(p.cat)["fp"] += 1
                misses.setdefault(d.id, []).append({"kind": "fp", "cat": p.cat, "start": p.start, "end": p.end})
            else:
                used.add(hit)
                cell(p.cat)["tp"] += 1
        for i, t in enumerate(d.spans):
            if i not in used:
                cell(t.cat)["fn"] += 1
                misses.setdefault(d.id, []).append({"kind": "fn", "cat": t.cat, "start": t.start, "end": t.end})
    for cat, c in per.items():
        c["precision"] = round(c["tp"] / (c["tp"] + c["fp"]), 4) if c["tp"] + c["fp"] else 1.0
        c["recall"] = round(c["tp"] / (c["tp"] + c["fn"]), 4) if c["tp"] + c["fn"] else 1.0
        c["informative"] = cat not in FORMATTED
    ftp = sum(c["tp"] for k, c in per.items() if k in FORMATTED)
    ffp = sum(c["fp"] for k, c in per.items() if k in FORMATTED)
    ffn = sum(c["fn"] for k, c in per.items() if k in FORMATTED)
    formatted = {"tp": ftp, "fp": ffp, "fn": ffn,
                 "precision": round(ftp / (ftp + ffp), 4) if ftp + ffp else 1.0,
                 "recall": round(ftp / (ftp + ffn), 4) if ftp + ffn else 1.0}
    formatted["passes"] = (formatted["recall"] >= THRESHOLDS["recall"]
                           and formatted["precision"] >= THRESHOLDS["precision"])
    return {"docs": len(docs), "per_category": dict(sorted(per.items())), "formatted": formatted,
            "thresholds": THRESHOLDS, "misses": misses}


def roundtrip(docs: List[Doc], config: dict, engines=None) -> List[str]:
    failed = []
    for d in docs:
        pm = PlaceholderMap()
        red, _ = engine.redact(d.text, pm, config, engines)
        back, _ = engine.restore(red, pm)
        if back != d.text:
            failed.append(d.id)
    return failed
