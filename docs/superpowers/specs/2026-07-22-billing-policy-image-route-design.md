# Design: policy billing cross-family + rotta immagini Gemini

Data: 2026-07-22 · Target release: 1.24.0 · Stato: approvato da Franz

## Problema

La generazione immagini via API Gemini è a pagamento (verificato live 2026-07-22:
free tier `limit: 0` su tutti i modelli image, 429 `GenerateRequestsPerDayPerProjectPerModel-FreeTier`).
Il cross-family di fable-director nasce sull'assunto "rotte esterne = free tier
che resetta": l'assunto è implicito e non machine-readable. Grok è già a
pagamento ma protetto solo da una nota prose nel config — il modello potrebbe
proporlo o eseguirlo come se fosse gratis.

Decisione utente: **lo strumento propone di default solo rotte free tier; i
provider a pagamento sono ammessi in config ma usati solo a discrezione
esplicita dell'utente.** Enforcement deterministico, non solo prose.

## Decisioni di design (con l'utente)

1. **Enforcement**: guardia deterministica negli script (non solo policy prose,
   non parcheggio in `_disabled_providers`).
2. **Scope**: policy billing + rotta immagini nella stessa release; la rotta
   immagini nasce già classificata paid ed è il primo caso d'uso della guardia.
3. **Consenso**: le rotte paid possono essere *menzionate* solo se nettamente
   superiori senza alternativa free (es. immagini), sempre con costo stimato e
   domanda esplicita PRIMA di eseguire. Mai esecuzione silenziosa.

## 1. Config: campo `billing`

Ogni voce di `providers` in `cross-family.json` guadagna:

- `"billing": "free" | "paid"` — machine-readable, deciso dall'utente.
- `"cost_note"` (opzionale, prose) — es. `"~$0.04/image"`; mostrato da
  `--doctor` e citato nelle richieste di consenso. Zero logica sopra.

**Fail-closed: campo assente = trattato come `paid`.** Un provider non
dichiarato non viene mai proposto né eseguito senza consenso. Migrazione:

- Il config locale dell'utente viene aggiornato in release (gemini,
  gemini-stable → `free`; grok → `paid`).
- `DEFAULT_CONFIG` in `cross-verify.py --init` include il campo.
- `--doctor` segnala le voci senza campo (`billing undeclared → treated as
  PAID (fail-closed): add "billing": "free"|"paid"`).

## 2. Guardia deterministica (`--paid-ok`)

In `external-exec.py` e `cross-verify.py`:

- Risoluzione provider (incluso `default` del config): se
  `billing != "free"` e il flag `--paid-ok` è assente →
  `STATUS: unavailable`, exit 1, detail:
  `provider 'X' is billed (cost_note se presente) — requires explicit user
  consent this conversation; re-run with --paid-ok only after the user agreed`.
- Contratto del flag (documentato nel docstring): `--paid-ok` si passa SOLO
  dopo consenso esplicito dell'utente nella conversazione corrente, mai
  preventivamente, mai "per efficienza".
- Telemetria: ogni run logga `"billing"` nel payload `external_exec`; i
  rifiuti della guardia loggano un evento dedicato (audit: quante volte una
  rotta paid è stata tentata senza consenso).
- `--doctor --ping`: il ping su provider paid consuma quota vera → viene
  saltato con nota, a meno di `--paid-ok`.

## 3. Superfici di proposta: solo free

- `route-hint.py` — `cardinality_candidate()` elenca solo provider con
  `billing == "free"`; zero provider free = nessun hint asse-4.
- `session-kernel.sh` — la frase "PROPOSE the free-tier route" diventa
  esplicita: rotte paid mai proposte di default; menzionabili solo se
  nettamente superiori senza alternativa free, sempre con costo stimato +
  domanda esplicita prima di eseguire (`--paid-ok` solo dopo il sì).
- `skills/delega-efficiente/SKILL.md` — stessa regola integrata nel bullet
  asse-4 esistente, in-place (rispetto del policy complexity budget: nessuna
  sezione nuova).
- `fd-status.py` — la riga "external today" separa i contatori:
  `external today: 12 free, 2 paid`.
- Onboarding XF (`session-kernel.sh`) — invariato: propone solo i free.

## 4. Rotta immagini Gemini (`"type": "image"`)

Terzo tipo di provider accanto a HTTP-chat e CLI. Voce template `gemini-image`
(in `DEFAULT_CONFIG` e nel config utente, chiave riusata):

```json
{
  "type": "image",
  "base_url": "https://generativelanguage.googleapis.com/v1beta",
  "model": "gemini-2.5-flash-image",
  "api_key_env": "GEMINI_API_KEY",
  "billing": "paid",
  "cost_note": "~$0.04/image"
}
```

Comportamento in `external-exec.py`:

- La spec È il prompt immagine. Chiamata REST nativa
  `POST {base_url}/models/{model}:generateContent` (chiave nell'header
  `x-goog-api-key`, mai in query string: finirebbe nei log),
  body `contents[0].parts[0].text = spec`.
- Risposta: primo part con `inlineData` → decode base64 → **bytes su `--out`**
  (`Path.write_bytes`). `--out` è OBBLIGATORIO per type image (binario mai su
  stdout); `check_out_perimeter` già copre il perimetro scrittura del budget.
- Output: `OUTPUT: <path>`, `DETAIL: <mime>, <bytes> bytes`. `CHECK: image`.
- Flag incompatibili → errore esplicito, mai ignorati: `--schema-json`,
  `--schema-file`, `--effort`, `--resume-last`, `--allow-truncate`/`--input`
  (v1 è text-to-image puro; image editing = fuori scope, YAGNI).
- Risposta senza `inlineData` (solo testo, es. rifiuto safety) →
  `STATUS: error`, detail con il testo troncato.
- 429 con `limit: 0` nel messaggio → detail dedicato: `image models need
  billing enabled on the Google project (free tier limit is 0) — not a
  transient quota error`. Altri 429 → messaggi quota esistenti.
- `--doctor`: per i provider image verifica chiave + presenza modello nella
  lista `GET /models` (gratis); generazione di prova solo `--ping --paid-ok`.

Flusso d'uso: utente chiede immagine → verdetto rotta
`gemini-image (paid, ~$0.04/img)` → domanda di consenso → `--paid-ok` →
PNG nel perimetro dichiarato dal budget.

Nota operativa: finché l'utente non abilita il billing sul progetto Google la
rotta resta installata ma risponde con il 429 dedicato. La policy protegge da
subito grok e ogni futuro provider paid.

## 5. Test (estensione suite esistenti)

- `tests/external-exec-verify.py`: guardia billing — free passa; paid senza
  `--paid-ok` rifiuta (exit 1, `STATUS: unavailable`); paid con flag passa;
  campo assente = paid. Type image: `--out` mancante = errore; risposta
  mockata con `inlineData` → bytes scritti; risposta solo testo → error;
  429 `limit: 0` → detail billing; flag incompatibili → errore.
- `tests/route-hint-verify.py`: hint asse-4 con mix free/paid elenca solo i
  free; config con soli paid → nessun hint.
- Suite verdi PRIMA del commit (house rule), release via `bash release.sh
  1.24.0` (bump + CHANGELOG + What's new prima).

## Fuori scope (esplicito)

- Image editing / input immagine (v2 eventuale, su richiesta).
- Stima costi calcolata (solo `cost_note` prose).
- Modelli Imagen (`predict` endpoint diverso; solo famiglia
  `gemini-*-image` via `generateContent`).
- Statusline: nessun segmento nuovo (il dettaglio free/paid vive in
  `fd-status.py`).
