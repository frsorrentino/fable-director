"""Placeholder map: value <-> [CAT_N], stable within a map, persisted 0600.

Normalisation decides "same value": e-mails and domains case-insensitive,
codes without spaces and upper-case, phone numbers digits-only with 0039
folded into +39, names casefolded with squeezed spaces. One entity, one
number. A second surface form of the same entity ("ORTENSIA WEB SRL" after
"Ortensia Web Srl") gets a variant letter, [ORG_3B], so restore is exact:
restore(redact(x)) == x always. A placeholder without letter is the first
form seen.
"""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

from .spans import CATEGORIES

STYLE = "[UPPER_N]"
_CASE_INSENSITIVE = {"EMAIL", "URL", "DOMINIO"}
_CODES = {"CF", "IBAN", "TARGA", "PIVA", "CARTA", "PROTOCOLLO"}


def normalize(cat: str, value: str) -> str:
    v = value.strip()
    if cat in _CASE_INSENSITIVE:
        return v.lower()
    if cat in _CODES:
        return re.sub(r"[\s.-]", "", v).upper()
    if cat == "TEL":
        digits = re.sub(r"[^\d+]", "", v)
        if digits.startswith("0039"):
            digits = "+39" + digits[4:]
        if digits.startswith("00"):
            digits = "+" + digits[2:]
        return digits
    if cat == "IP":
        return v
    return re.sub(r"\s+", " ", v).casefold()


def map_path(name: str, home: Optional[Path] = None) -> Path:
    base = Path(home) if home else Path.home()
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-") or "default"
    return base / ".claude" / "fable-director" / "anonymizer" / "maps" / f"{safe}.json"


class PlaceholderMap:
    def __init__(self, style: str = STYLE):
        self.style = style
        self.created = datetime.now(timezone.utc).isoformat(timespec="seconds")
        # cat -> {"by_key": {key: n}, "values": {n: [form, form2, ...]}}
        self._cats: Dict[str, Dict[str, dict]] = {}

    # -- placeholders ------------------------------------------------------
    @staticmethod
    def _variant(k: int) -> str:
        return "" if k == 0 else (chr(65 + k) if k < 25 else f"V{k}")

    @staticmethod
    def _variant_index(suffix: Optional[str]) -> int:
        if not suffix:
            return 0
        if suffix.startswith("V") and len(suffix) > 1:
            return int(suffix[1:])
        return ord(suffix.upper()) - 65

    def _fmt(self, cat: str, n: int, k: int = 0) -> str:
        return f"[{cat}_{n}{self._variant(k)}]"

    def placeholder(self, cat: str, value: str) -> str:
        key = normalize(cat, value)
        form = value  # exact surface form: restore must be byte-exact
        c = self._cats.setdefault(cat, {"by_key": {}, "values": {}})
        n = c["by_key"].get(key)
        if n is None:
            n = len(c["values"]) + 1
            c["by_key"][key] = n
            c["values"][n] = [form]
            return self._fmt(cat, n)
        forms = c["values"][n]
        if form not in forms:
            forms.append(form)
        return self._fmt(cat, n, forms.index(form))

    def placeholder_for(self, span) -> str:
        return self.placeholder(span.cat, span.value)

    def lookup(self, cat: str, n: int, variant: int = 0) -> Optional[str]:
        forms = self._cats.get(cat, {}).get("values", {}).get(int(n))
        if not forms or variant >= len(forms):
            return None
        return forms[variant]

    def counts(self) -> Dict[str, int]:
        return {cat: len(c["values"]) for cat, c in self._cats.items() if c["values"]}

    # -- restore -----------------------------------------------------------
    _PH = re.compile(r"\[?\b([A-Za-z][A-Za-z_]*?)_(\d+)([A-Za-z]|V\d+)?\b\]?")

    def restore(self, text: str) -> Tuple[str, Dict[str, int]]:
        info = {"restored": 0, "unknown": 0}
        known = {c.upper() for c in self._cats} | set(CATEGORIES)

        def sub(m):
            cat, n = m.group(1).upper(), int(m.group(2))
            if cat not in known:
                return m.group(0)
            v = self.lookup(cat, n, self._variant_index(m.group(3)))
            if v is None:
                info["unknown"] += 1
                return m.group(0)
            info["restored"] += 1
            return v

        return self._PH.sub(sub, text), info

    # -- persistence -------------------------------------------------------
    def to_dict(self) -> dict:
        return {"version": 1, "created": self.created, "style": self.style,
                "entries": {cat: {str(n): {"forms": c["values"][n], "key": key}
                                  for key, n in c["by_key"].items()}
                            for cat, c in self._cats.items()}}

    @classmethod
    def from_dict(cls, d: dict) -> "PlaceholderMap":
        if not isinstance(d, dict) or d.get("version") != 1 or "entries" not in d:
            raise ValueError("not an anonymizer map (version 1)")
        pm = cls(d.get("style", STYLE))
        pm.created = d.get("created", pm.created)
        for cat, entries in d["entries"].items():
            c = pm._cats.setdefault(cat, {"by_key": {}, "values": {}})
            for n, e in entries.items():
                c["by_key"][e["key"]] = int(n)
                c["values"][int(n)] = list(e.get("forms") or [e["value"]])
        return pm

    def save(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=1)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        return path

    @classmethod
    def load(cls, path: Path) -> "PlaceholderMap":
        try:
            d = json.loads(Path(path).read_text())
        except (OSError, json.JSONDecodeError) as e:
            raise ValueError(f"map unreadable: {e}") from None
        return cls.from_dict(d)
