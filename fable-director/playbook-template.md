# Orchestration playbook — euristiche di delega

Registro con tetto: MAX 30 righe di euristica. A quota piena consolidare/fondere/eliminare prima di aggiungere (i contatori decidono cosa muore prima).
Formato: `- [candidata|confermata AAAA-MM] area: euristica (root cause in parentesi) (uses:0 ok:0 ko:0)`
Contatori: incrementa `uses` quando applichi l'euristica, `ok`/`ko` in base all'esito oggettivo. Con N piccoli informano solo il consolidamento, mai il ranking automatico.
Una voce nasce `[candidata]` da un singolo incidente; diventa `confermata` solo alla seconda occorrenza indipendente.
Si scrive qui per: rule-of-3 chiusa al livello 3 per fallimento approach/tool, sforamento pre-budget ≥3×, script promosso (riga indice), o import deliberato di pattern collaudati `[seed]` (esenti da doppia conferma, contano nel tetto).

## Euristiche

- [seed 2026-07] chaining bug noti: locate→fix→verify (agente di ricerca read-only → agente di edit → re-test), mai fix senza locate (da cavecrew) (uses:0 ok:0 ko:0)
- [seed 2026-07] codebase ignota: parallel-scout — 2-3 agenti di ricerca read-only da angolazioni diverse, poi aggregazione, prima di qualsiasi edit (da cavecrew) (uses:0 ok:0 ko:0)
- [seed 2026-07] sito dell'edit già noto: single-shot — salta la fase di ricerca, delega direttamente l'edit (da cavecrew) (uses:0 ok:0 ko:0)
- [seed 2026-07] code review delegata: scope via range commit `BASE_SHA..HEAD_SHA`, mai incollare diff o storia sessione nel prompt (da requesting-code-review) (uses:0 ok:0 ko:0)
- [seed 2026-07] mai fidarsi del report di successo di un agente: verificare sempre il diff VCS/artefatto indipendentemente (da verification-before-completion) (uses:0 ok:0 ko:0)
- [seed 2026-07] analisi strutturale ripetuta su file >500 righe: genera skeleton una volta via script (ctags/ast-grep: firme+import+commenti) e punta lì gli agenti; per ricerche puntuali bastano gli idiomi Grep/Read-parziale esistenti (uses:0 ok:0 ko:0)
- [seed 2026-07] handoff (fine sessione, cambio esecutore, task oltre il contesto): consegna = piano su file (file toccati, ordine, trappole, criteri di verifica per passo), mai codice a metà (il divario tra esecutori è massimo in pianificazione, minimo in esecuzione guidata) (uses:0 ok:0 ko:0)
- [seed 2026-07] prompt di delega con failure mode noto: includi UN contro-esempio esplicito (cosa non fare + perché) accanto alle istruzioni positive (l'esecutore replica pattern: il contro-esempio taglia il failure mode più dell'istruzione astratta) (uses:0 ok:0 ko:0)
- [seed 2026-07] delega spot (singolo agente leggero, es. investigator/builder cavecrew): il gate nega senza budget — apri micro-budget onesto della SINGOLA delega (`--type spot-delegation`, stima = solo deliverable atteso), MAI budget-sessione a stima larga: stima gonfiata = enforcement 2×/3× morto (uses:0 ok:0 ko:0)
- [seed 2026-07] ANTI-PATTERN riduzione token: mai sostituire la lettura di un file con chunk retrievati (RAG lossy: top-k vs file intero, −90% token) su lavoro quality-sensitive — il chunking separa codice dipendente e scarta contesto = Goodhart sull'asse 2 (error cost). Ridurre token è LOSSLESS-only: non ripetere lavoro verificato (cache idempotente exact-hash+verified), compressione reversibile. NB il dedup delle riletture identiche è stato misurato irrilevante su traffico reale (0,0–0,1% dei Read bytes, 1278 sessioni + conferma indipendente headroom): prima di costruire un meccanismo di risparmio, misura il bersaglio sui transcript veri. Il caching semantico (match approssimato) ricade nel divieto (uses:0 ok:0 ko:0)

## Script promossi

- [2026-07] `tools/session-cost-report.py` (dir della skill delega-efficiente): rendiconto token per modello/main/subagenti dai transcript JSONL + metriche cache/delega; legge il budget file da solo. Sostituisce ogni stima manuale di costo sessione.
- [2026-07] `scripts/fd-telemetry.py` (root del plugin): budget-open/close (pre-budget machine-readable, enforcement via hook Stop), log eventi oggettivi su SQLite, report aggregato. Sostituisce ogni tracciamento manuale del learning loop.
