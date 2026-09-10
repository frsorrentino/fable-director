# Anonymizer fase A — piano di implementazione

> **For agentic workers:** eseguito inline dal modello di punta in questa sessione (asse 2: codice di produzione del plugin più dati cliente nel corpus). Il codice sta nel repo, scritto una volta sotto TDD: qui interfacce esatte, casi di test e punti di commit, non il codice duplicato. Spec: `docs/superpowers/specs/2026-09-10-anonymizer-fase-a-design.md`.

**Goal:** motore di pseudonimizzazione a span (`rules(it)`, `columns`, `dictionary`), mappa `[CAT_N]` con `restore`, CLI `scan/redact/restore/test/status`, corpus annotato pubblico e privato, suite nella release.

**Architecture:** pacchetto `fable-director/anonymizer/` stdlib pura; i motori producono span sul testo originale, `merge_spans` risolve le sovrapposizioni, una sola sostituzione; la CLI `scripts/anonymizer.py` è l'unico adattatore in fase A.

**Tech Stack:** Python 3.11 stdlib (`re`, `csv`, `json`, `pathlib`, `os`). Nessuna dipendenza.

## Global Constraints

- Nessun valore personale in log, telemetria, `DETAIL:`, rapporto di `test` (solo conteggi e categorie); `--show-misses` è l'unica eccezione esplicita.
- Output CLI sempre `STATUS: ok|error|unavailable` / `OUTPUT:` / `DETAIL:`.
- Mappe `chmod 600` in cartella `chmod 700`.
- Segnaposto `[CAT_N]`, N da 1 per categoria; `restore` tollera `[cat_n]`, `CAT_N`.
- Corpus privato fuori repo (`~/.claude/fable-director/anonymizer-corpus/`); in repo solo dati sintetici.
- Suite: `python3 tests/anonymizer-verify.py` exit 0; ≤ 5 ms/KB su 200 KB.

---

### Task 1: span, merge, apply, pmap

**Files:** create `fable-director/anonymizer/__init__.py`, `spans.py`, `pmap.py`; test `tests/anonymizer-verify.py` (casi A3, A6).

**Interfaces (produces):**
- `spans.Span(start:int, end:int, cat:str, value:str, source:str)` (NamedTuple).
- `spans.ENGINE_PRIORITY = {"columns":0, "rules":1, "ner":2, "dictionary":3}`.
- `spans.merge_spans(spans: list[Span]) -> list[Span]` ordinati, senza sovrapposizioni (vince lunghezza, poi priorità).
- `spans.apply_spans(text, spans, placeholder: Callable[[Span], str]) -> str`.
- `pmap.normalize(cat, value) -> str` chiave per categoria.
- `pmap.PlaceholderMap(style="[UPPER_N]")`: `.placeholder(cat, value) -> str`, `.lookup(cat, n) -> str|None`, `.restore(text) -> (text, {"restored":n, "unknown":n})`, `.save(path)`, `PlaceholderMap.load(path)`, `.counts() -> {cat: n}`, `.created` ISO.
- `pmap.map_path(name, home=None) -> Path` = `~/.claude/fable-director/anonymizer/maps/<name>.json`.

Test: sovrapposizione EMAIL dentro INDIRIZZO più lungo → resta il lungo; a parità columns batte rules; `placeholder("EMAIL","A@b.it")` e `"a@B.IT"` → stesso `[EMAIL_1]`; salvato con mode 600; `restore` di `"[email_1] EMAIL_1 [EMAIL_9]"` → due ripristini, `unknown 1`, `[EMAIL_9]` conservato.

Commit: `feat(anonymizer): span merge and placeholder map`.

### Task 2: rules base + it

**Files:** create `anonymizer/rules/__init__.py`, `rules/base.py`, `rules/it.py`; test A1, A2.

**Interfaces:**
- `rules.base.cf_ok`, `piva_ok`, `iban_ok` (generico mod 97), `luhn_ok`.
- `rules.Rule(cat, regex, validator, group)`; `rules.load_packs(names) -> list[Rule]`; `rules.find_spans(text, config, taken) -> list[Span]` (source `rules`), applica `whitelist`, `tel_requires_context`, `url` mode, esclusioni IP.

Test: positivi/negativi per categoria come in spec §2.3 (TEL senza contesto no, `+39 333 1234567` sì, `via SGR 2` no, `Via Roma 12, 88900 Crotone` sì con CAP incluso, `127.0.0.1`/`192.168.1.1` no, `8.8.8.8` sì, `https://github.com/x` in whitelist no, `https://cliente.it/p` → DOMINIO `cliente.it`, email non produce DOMINIO, CF con check sbagliato no, `IT60X0542811101000000123456` sì, carta Luhn ok sì/no).

Commit: `feat(anonymizer): rules packs base and it`.

### Task 3: columns

**Files:** create `anonymizer/columns.py`; test A4.

**Interfaces:** `columns.detect(text) -> ("csv", delim) | ("json",) | None`; `columns.HEADERS: dict[str,str]` costruito da `PROFILES: dict[str, dict[str,str]]`; `columns.find_spans(text, config, taken) -> list[Span]` (source `columns`), offset esatti per riga (`csv` stdlib con ricostruzione; mismatch → `[]` e flag `columns_skipped`).

Test: CSV `;` Brevo, TSV, JSON tabellare, `domini` con ` | `, testo prosa → `[]`, `columns.extra`.

Commit: `feat(anonymizer): columns engine with header profiles`.

### Task 4: dictionary + config + engine

**Files:** create `anonymizer/dictionary.py`, `config.py`, `engine.py`; test A5 e round-trip minimo.

**Interfaces:**
- `config.DEFAULTS`; `config.load(cwd=None, home=None) -> dict` (default ← globale ← progetto); `config.find_project_file(cwd)`.
- `dictionary.find_spans(text, config, taken)` (source `dictionary`); `dictionary.stems(org) -> list[str]`.
- `engine.scan(text, config) -> Report`; `engine.redact(text, pmap, config) -> (str, Report)`; `engine.restore(text, pmap) -> (str, dict)`. `Report = {"counts": {cat: n}, "by_engine": {eng: n}, "ms": float, "columns_skipped": bool}`.

Commit: `feat(anonymizer): dictionary engine, config, engine facade`.

### Task 5: corpus + fixtures sintetiche

**Files:** create `anonymizer/corpus.py`, `tests/fixtures/anonymizer/gen.py` (deterministico, seed 7), documenti generati `tests/fixtures/anonymizer/docs/*.txt|*.spans.json`, `tests/fixtures/anonymizer/fd-anonymizer.json`; test A7, A8.

**Interfaces:** `corpus.load(dir) -> list[Doc]`; `corpus.evaluate(docs, config, engines) -> Metrics` (per categoria TP/FP/FN, precision, recall; overlap ≥ 0.5; `formatted` set; `passes(thresholds)`); `corpus.roundtrip(docs, config) -> list[failed ids]`; `corpus.FORMATTED = {...}`.

Commit: `feat(anonymizer): corpus metrics and synthetic public fixtures`.

### Task 6: CLI

**Files:** create `fable-director/scripts/anonymizer.py`; test A9, A10.

Comandi come spec §2.7. `test` senza `--corpus`: pubblico + privato se esiste. `status --purge` cancella mappe oltre `map_ttl_days`.

Commit: `feat(anonymizer): CLI scan/redact/restore/test/status`.

### Task 7: corpus privato

Fuori repo. Script usa-e-getta nello scratchpad: sposta i 12 documenti in `docs/`, estrae ~20 documenti reali (spec §2.8), prima annotazione col motore, poi revisione del modello span per span (in particolare PERSONA/ORG mancanti e falsi positivi). `anonymizer.py test` sul privato: numeri nel rapporto finale, regole corrette se sotto soglia sulle categorie con formato.

Nessun commit (fuori repo). Annotare nel piano `docs/plans/2026-09-09-anonymizer.md` i numeri.

### Task 8: documentazione e suite

**Files:** modify `README.md`, `fable-director/README.md`, `docs/INTERNALS.md`, `CHANGELOG.md` (Unreleased), `docs/plans/2026-09-09-anonymizer.md` (§6 riga A: stato). `release.sh` già gira `tests/*.py`: verificare che `anonymizer-verify.py` passi in quel loop insieme al resto della suite.

Commit: `docs: anonymizer phase A`.
