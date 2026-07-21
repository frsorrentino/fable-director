#!/usr/bin/env python3
"""Hook UserPromptSubmit: hint deterministico di rotta cross-family/soft-dep.

Il kernel chiede al regista un verdetto di rotta in una riga su ogni task non
banale; questo hook garantisce che la valutazione non venga SALTATA quando un
candidato esiste, senza spendere token: matcha il prompt contro le
`hint_keywords` dichiarate in soft-deps.json e contro i segnali di cardinalità
(asse 4) quando cross-family.json ha provider configurati.

Zero giudizio: l'hint elenca CANDIDATI, la decisione resta al regista — che
deve citare sia l'asse che permette sia quello che vieta (quality_guard e
data_class dell'entry restano sovrani). Niente match = niente output = niente
rumore: il costo dell'hint è zero quando non serve.

Opt-in per entry: solo le voci di soft-deps.json con `hint_keywords` (lista di
parole singole — match a confine di parola — o frasi — match substring)
partecipano. Fail-silent by design: mai bloccare o sporcare il prompt per un
errore di config.
"""
import json
import re
import sys
from pathlib import Path

MAX_CANDIDATES = 3
MIN_PROMPT_LEN = 20

# Segnali asse 4 (cardinalità): conservativi, solo parole forti — un falso
# positivo per turno costerebbe più del beneficio dell'hint.
CARDINALITY = re.compile(
    r"\b(batch|bulk|tutti|tutte|ogni|ciascun\w*|elenco|lista di"
    r"|all (?:the )?files|each|every)\b", re.IGNORECASE)


def base_dir():
    return Path.home() / ".claude" / "fable-director"


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def keyword_hit(prompt_lower, kw):
    kw = kw.lower().strip()
    if not kw:
        return False
    if " " in kw:  # frase: substring
        return kw in prompt_lower
    # parola singola: confine di parola ("form" non deve scattare su "formato")
    return re.search(r"\b" + re.escape(kw) + r"\b", prompt_lower) is not None


def soft_dep_candidates(prompt_lower):
    deps = load_json(base_dir() / "soft-deps.json")
    out = []
    for name, entry in deps.items():
        if name.startswith("_") or not isinstance(entry, dict):
            continue
        for kw in entry.get("hint_keywords", []):
            if keyword_hit(prompt_lower, str(kw)):
                classes = ", ".join(entry.get("classes", [])[:2]) or "?"
                out.append((name, f'- {name} ({classes}; match "{kw}") — '
                                  "dettagli e guardie in soft-deps.json"))
                break
    return out


def cardinality_candidate(prompt_lower):
    m = CARDINALITY.search(prompt_lower)
    if not m:
        return None
    providers = load_json(base_dir() / "cross-family.json").get("providers", {})
    if not providers:
        return None
    names = ", ".join(sorted(providers))
    return ("external-exec",
            f'- external-exec asse 4 (segnale cardinalità "{m.group(0)}") — '
            f"provider: {names}; solo item non quality-sensitive, pre-budget obbligatorio")


def write_event(payload):
    import random
    import sqlite3
    import time
    from datetime import datetime, timezone
    base = base_dir()
    base.mkdir(parents=True, exist_ok=True)
    row = (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "route_hint", json.dumps(payload))
    for attempt in range(4):
        try:
            con = sqlite3.connect(base / "telemetry.db", timeout=1.0)
            con.execute("PRAGMA busy_timeout=1000")
            con.execute("CREATE TABLE IF NOT EXISTS events("
                        "id INTEGER PRIMARY KEY, ts TEXT NOT NULL, "
                        "session_id TEXT, cwd TEXT, event TEXT NOT NULL, "
                        "payload TEXT)")
            con.execute("INSERT INTO events(ts, event, payload) "
                        "VALUES(?,?,?)", row)
            con.commit()
            con.close()
            return
        except sqlite3.OperationalError:
            time.sleep(0.05 * (2 ** attempt) + random.random() * 0.05)


def main():
    data = json.load(sys.stdin)
    prompt = str(data.get("prompt") or "")
    # slash command o prompt troppo corto: mai un task da instradare
    if len(prompt) < MIN_PROMPT_LEN or prompt.lstrip().startswith("/"):
        return
    prompt_lower = prompt.lower()

    candidates = soft_dep_candidates(prompt_lower)
    card = cardinality_candidate(prompt_lower)
    if card:
        candidates.append(card)
    if not candidates:
        return
    candidates = candidates[:MAX_CANDIDATES]

    print("[fd-route-hint] candidati deterministici — da VALUTARE, non seguire "
          "ciecamente; verdetto di rotta in una riga (asse permittente E vietante):")
    for _, line in candidates:
        print(line)
    write_event({"matches": [n for n, _ in candidates],
                 "prompt_len": len(prompt)})


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
