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

Control arm (misura, non feature): una frazione dei prompt con match
(default 10%, `FD_HINT_HOLDOUT` 0..1) NON riceve l'hint ma l'evento viene
loggato con `holdout:true` e i match che AVREBBE mostrato. La scelta è
deterministica sul solo session_id: una sessione = un braccio, sempre
(niente flip a mezzanotte UTC — review 1.35.1). Il confronto
trattato/trattenuto vive in `fd-telemetry.py report` e stampa numeri solo
sopra soglia di sufficienza — sotto, un rapporto sarebbe teatro. Idea dal
control arm di ooples/token-optimizer-mcp (MIT), implementazione nostra:
misura se l'hint CAUSA rotte migliori invece di assumerlo.
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
    # Solo i free: i provider paid non vengono MAI proposti d'ufficio
    # (fail-closed: billing assente = paid). Policy 2026-07-22.
    free = sorted(n for n, p in providers.items()
                  if isinstance(p, dict) and p.get("billing") == "free")
    if not free:
        return None
    names = ", ".join(free)
    return ("external-exec",
            f'- external-exec asse 4 (segnale cardinalità "{m.group(0)}") — '
            f"free-tier provider: {names}; solo item non quality-sensitive, "
            f"pre-budget obbligatorio")


def in_holdout(session_id):
    """Braccio di controllo deterministico per sessione — SOLO session_id
    (review 1.35.1: la componente giorno faceva cambiare braccio a
    mezzanotte UTC a metà sessione, contaminando il confronto; una
    sessione = un braccio, per sempre).

    Senza session_id niente holdout: un braccio non attribuibile non è
    misurabile, e l'hint resta utile.
    """
    import hashlib
    import os
    if not session_id:
        return False
    try:
        frac = float(os.environ.get("FD_HINT_HOLDOUT", "0.1"))
    except ValueError:
        frac = 0.1
    if not (0.0 <= frac <= 1.0):
        frac = 0.1
    h = hashlib.sha256(f"fd-hint-arm:{session_id}".encode()).hexdigest()
    return (int(h[:8], 16) / 0xFFFFFFFF) < frac


def write_event(payload, session_id=None, cwd=None):
    """Unica fonte dell'insert-with-retry: log_event di fd-telemetry
    (review 1.35.1 — tre copie divergevano già). Import fallito o insert
    fallito dopo i retry → evento perso in silenzio: l'hint è best-effort,
    mai bloccare il prompt per la telemetria."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "fd_telemetry", Path(__file__).with_name("fd-telemetry.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.log_event("route_hint", payload, session_id=session_id, cwd=cwd)
    except Exception:
        pass


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

    session_id = str(data.get("session_id") or "") or None
    cwd = str(data.get("cwd") or "") or None
    holdout = in_holdout(session_id)
    if not holdout:
        print("[fd-route-hint] candidati deterministici — da VALUTARE, non "
              "seguire ciecamente; verdetto di rotta in una riga (asse "
              "permittente E vietante):")
        for _, line in candidates:
            print(line)
    write_event({"matches": [n for n, _ in candidates],
                 "prompt_len": len(prompt), "holdout": holdout},
                session_id=session_id, cwd=cwd)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
