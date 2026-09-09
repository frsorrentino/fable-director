# Anonymizer — piano tecnico e misura di fase 1

Data: 2026-09-09 (sera). Stato: piano, nessun codice nel plugin. Il codice parte dopo l'ok di Franz.
Cornice: il piano «Pixel Anonymizer» dell'agenzia (2 settembre 2026, approvato e non avviato, documento interno): questo è la parte «motore + plugin» di quel piano vista da fable-director, e va riportata lì quando è ferma.

## 1. Scopo e confini

fable-director governa il costo; questo modulo governa i dati che escono dalla macchina sulla **rotta esterna** (`external-exec.py`, `cross-verify.py`, `media-analyze.py`, prefissi di famiglia). Il nome è «anonymizer», la tecnica è la pseudonimizzazione: i valori restano sulla macchina in una mappa, il fornitore riceve segnaposto stabili, l'output torna con i valori.

Direzione confermata da Franz il 09/09 (sera): il modulo copre anche ciò che Claude Code manda ad Anthropic (prompt, output degli strumenti, file scritti), con gli hook disponibili oggi (`updatedPrompt`, `updatedToolResult`, `updatedInput`) e predisposto ai Function Hooks (#91870, prototipo dietro flag) quando usciranno: fase E in §6, regola di separazione in §2b.

Spento di default. Nessun valore personale nei log: solo conteggi e categorie.

## 2. Architettura

```
testo in uscita ──► rules(it) ──► columns ──► ner (a richiesta) ──► dictionary ──► segnaposto + mappa
                                                                                      │
risposta del fornitore ◄───────────────────────── restore (mappa inversa) ◄───────────┘
```

Cascata nell'ordine: ogni motore vede il testo già ridotto dal precedente, così il NER lavora su meno rumore e i segnaposto non vengono riconosciuti due volte.

| Componente | File | Cosa fa |
|---|---|---|
| CLI | `scripts/anonymizer.py` | `scan` (conteggi, nessuna scrittura), `redact FILE --map M`, `restore FILE --map M`, `status`, `test` (corpus), sempre `STATUS:/OUTPUT:/DETAIL:` come gli altri script |
| Regole | `anonymizer/rules/it.py`, `rules/base.py` | regex con check digit dove esiste (CF, P.IVA, IBAN); pacchetti per paese, `it` completo, gli altri solo per le categorie misurate |
| Colonne | `anonymizer/columns.py` | CSV/TSV/JSON tabellare: intestazione nota → categoria (WooCommerce `billing_*`, Contact Form 7 `your-*`, Brevo/Mailchimp `EMAIL,NOME,COGNOME,…`, PrestaShop `Customer e-mail*, Lastname*…`, portali dell'agenzia); la cella intera diventa segnaposto, senza regex |
| NER | `anonymizer/ner_onnx.py` | GLiNER multilingue PII in ONNX su `onnxruntime`, caricato per chiamata in un sottoprocesso, mai residente; soglia e categorie da config; assente = `STATUS: unavailable`, mai fallback silenzioso |
| Dizionario | `anonymizer/dictionary.py` | liste generate fuori dal plugin (dal gestionale dell'agenzia: clienti, referenti, domini) in un file JSON per progetto; match esatto e per token, rete di sicurezza dopo il NER |
| Mappa | `anonymizer/pmap.py` | segnaposto stabili per sessione (`CLIENTE_7`, `EMAIL_3`): stesso valore → stesso segnaposto, anche con maiuscole diverse; file `~/.claude/fable-director/anonymizer/maps/<cwd-slug>/<budget-id>.json`, `chmod 600`, scadenza configurabile |
| Guardia | `scripts/pii-guard.py` (PreToolUse, matcher `Bash`) | se il comando invoca uno script della rotta esterna e il testo in uscita contiene PII diretti secondo `rules`, avvisa (default) o nega (`guard: deny`) quando il modulo è spento |
| Registro | evento `anonymizer` in telemetria | motore, categorie e conteggi, tempo, `restored: n` — mai i valori |

### 2b. Motore e adattatori: la regola che rende indolore il passaggio ai Function Hooks

Il motore (`anonymizer/engine.py`) espone tre funzioni pure e nient'altro: `redact(text, map, config) -> text`, `restore(text, map) -> text`, `scan(text, config) -> counts`. Non sa che esistono hook, `external-exec.py` o Claude Code. Gli adattatori sono file sottili che leggono il payload di un evento e chiamano quelle tre funzioni:

| Adattatore | Oggi (hook a processo) | Domani (Function Hooks) |
|---|---|---|
| prompt in uscita | `scripts/anon-prompt.py` su `UserPromptSubmit` → `updatedPrompt` | modulo TS su `turn.start`, stessa chiamata `redact` via processo residente |
| output strumenti | `scripts/anon-toolresult.py` su `PostToolUse` (Bash, Read, Grep, WebFetch; MCP via `updatedMCPToolOutput`) → `updatedToolResult` | middleware sul risultato, stesso `redact` |
| ritorno nei file e comandi | `scripts/anon-toolinput.py` su `PreToolUse` (Write, Edit, Bash) → `updatedInput` | middleware sull'input, stesso `restore` |
| rotta esterna | innesto in `external-exec.py` / `cross-verify.py` | invariato |

Con gli hook a processo ogni chiamata paga lo spawn di Python (67-94 ms misurati su questo Chromebook), tre volte a turno: il motore va caricato in un **processo residente** (`anonymizer/daemon.py`, socket Unix in `~/.claude/fable-director/anonymizer/`, avviato al primo uso, spento dopo N minuti di inattività) e gli adattatori diventano client da pochi millisecondi. Lo stesso demone serve i Function Hooks quando arriveranno: cambia solo chi bussa al socket. Il NER, se abilitato, vive nel demone e si scarica dopo il timeout, mai residente a vuoto.

Integrazione in `external-exec.py`: flag `--anonymize on|off|auto` (auto = config); prima della chiamata `redact` su spec e input, dopo la chiamata `restore` sull'output prima di `--out`. Stesso innesto in `cross-verify.py`; `media-analyze.py` manda file binari: solo blocco, niente redazione (fase 4 del piano dell'agenzia).

Classe dati: con il masker in Claude Code la classe diventa la modalità di lavoro del progetto: `confidential` = Claude lavora, ma solo su testo pulito (prompt, output, file); `restricted` = muro per sanitario e giudiziario, come nel piano dell'agenzia. Il brief chiede che `restricted` diventi «esce solo pseudonimizzato». Oggi `restricted` è un muro (README: «`--data-class restricted` blocks the external routes»). Proposta: tenere il muro e aggiungere `--data-class confidential` = esce solo dopo `redact`, con evento a registro. Cambiare il significato di una classe promessa nel README a chi ha già installato il plugin è la cosa da evitare. Decisione di Franz (§7).

## 3. Configurazione

`~/.claude/fable-director/anonymizer.json` (globale), `.fd-anonymizer.json` nel progetto (override e dizionario):

```json
{
  "enabled": false,
  "engines": ["rules", "columns", "ner", "dictionary"],
  "packs": ["it"],
  "rules": {"tel_requires_context": true, "url": "domain-only"},
  "columns": {"profiles": ["woocommerce", "cf7", "brevo", "prestashop", "pixelbox"], "extra": {}},
  "ner": {"model": "onnx-community/gliner_multi_pii-v1", "file": "model_int8.onnx", "threshold": 0.5,
          "labels": ["person", "organization", "address", "date of birth"], "max_mb": 800, "timeout_s": 60},
  "dictionary": {"files": [".fd-anonymizer.json"], "whitelist": ["Pixelfarm", "WooCommerce"]},
  "placeholders": {"style": "UPPER_N", "map_ttl_days": 30},
  "guard": "warn",
  "log": "counts"
}
```

Nel file di progetto: `dictionary` (nomi, domini, referenti), `whitelist` (marchi, comuni, nomi presenti nel codice), `columns.extra` (intestazioni proprie), `block_paths` (cartelle con export da non leggere).

## 4. Dipendenze facoltative e peso (verificato sul Chromebook il 09/09)

| Motore | Dipendenze | Disco | RAM in esecuzione | Note |
|---|---|---|---|---|
| rules, columns, dictionary, mappa | stdlib | 0 | trascurabile | 0,86 ms/KB misurati |
| ner via ONNX | `onnxruntime` 1.29.0 (wheel aarch64 cp311, 20,8 MB, scaricato), `tokenizers` (~3 MB), modello `onnx-community/gliner_multi_pii-v1` `model_int8.onnx` 349 MB (fp16 580 MB, fp32 1,16 GB) | ~375 MB | stima 600-800 MB, da misurare | **non esiste una libreria GLiNER senza PyTorch**: `gliner` 0.2.29 richiede `torch>=2.0` anche con l'extra `onnx`; `gliner2` 2.0.0 (Fastino) è torch-free solo come client API, il locale è `[local]` = torch; transformers.js 4.2.0 non supporta GLiNER; su PyPI non esistono `gliner-onnx`, `onnx-gliner`, `gliner-lite`, `glinerx`, `pygliner`. Il wrapper ONNX (tokenizzazione, costruzione di `input_ids/words_mask/span_idx`, decodifica dei logit in span) va scritto: ~200 righe, da validare contro la libreria torch in un venv usa-e-getta |
| ner via torch (solo banco di prova) | `gliner[onnx]` + `torch` CPU aarch64 + modello 1,16 GB | ~1,5 GB | >1,5 GB | mai nel pacchetto; serve solo per misurare il wrapper ONNX e per il confronto dei modelli |
| alternativa modello | `fastino/gliner2-privacy-filter-PII-multi` (Apache 2.0, lingue en/fr/es/de/it/pt/nl, 1,23 GB safetensors) | 1,23 GB | >1,5 GB | ha l'italiano dichiarato, ma solo torch; `DeepMount00/GLiNER_PII_ITA` (it, Apache 2.0, 1,16 GB) idem. Candidati per il confronto, non per il pacchetto finché non esiste una conversione ONNX |

Il Chromebook ha 6,6 GB di RAM (3,7 disponibili) e 14 GB liberi su disco: il NER in ONNX int8 ci sta; un venv torch ci sta come banco di prova, non in produzione.

## 5. Misura di fase 1 — quello che c'è davvero

**Corpus.** Il brief chiedeva ~100 prompt della rotta esterna dell'ultimo mese. La telemetria non conserva testo (65 eventi `external_exec`, solo conteggi). Dai transcript degli ultimi 45 giorni: 95 run reali in 16 sessioni (tipi: legal 60%, cross-verify 14%, audio 3%, altro). Testo ricostruibile: **12 documenti, 71,7 KB** (spec e input scritti in sessione con `Write`); 27 input non ricostruibili (PDF e audio, file temporanei sotto variabili di shell). Corpus in `~/.claude/fable-director/anonymizer-corpus/` (chmod 700, fuori dal repo). Per il tabellare: tre export reali (newsletter Brevo/Mailchimp 24k righe, anagrafica rivenditori 150 righe, indirizzi PrestaShop 16 righe), misurati sulle prime 300 righe.

**rules (it)** sui 12 documenti: 60 ms totali, 0,86 ms/KB.

| Categoria | Trovati | Revisione del campione |
|---|---|---|
| URL | 15 | tutti corretti (badge, GitHub) — non personali: la regola va ridotta al dominio o esclusa dai segnaposto |
| TEL | 6 | 3 su 6 falsi positivi: numeri di protocollo/serie letti come telefoni |
| PROTOCOLLO | 6 | corretti |
| IP | 2 | `127.0.0.1`, `0.0.0.0`: corretti come formato, non personali |
| EMAIL | 1 | corretto |
| INDIRIZZO | 1 | falso positivo (`via SGR 2`, sequenza ANSI) |
| CF, P.IVA, IBAN, TARGA, CARTA, DATA_NASCITA | 0 | nessuna occorrenza nel corpus |

Lettura: il corpus legale è povero di identificatori formattati e pieno di **nomi di persona**, che le regole non vedono per costruzione. Sulla rotta legale la categoria che conta è PERSONA: senza NER (o dizionario) il motore a regole è quasi vuoto lì.

**columns** sui tre export (465 righe), confronto con `rules` sullo stesso testo serializzato:

| Categoria | celle secondo columns | viste da rules | Nota |
|---|---|---|---|
| EMAIL | 460 | 460 | 100% |
| PERSONA | 674 | 0 | rules non ha nomi |
| DATA_NASCITA | 260 | 0 | senza «nato il» la regola non scatta |
| INDIRIZZO | 116 | 75 | 65%: mancano le forme senza via/piazza |
| TEL | 19 | 3 | 0 su 16 nel formato estero di PrestaShop |

Tempo columns+rules: 20 ms per 300 righe. Sul tabellare `columns` è indispensabile e da solo copre il 100% delle colonne mappate; `rules` serve per le colonne libere (note, messaggi).

**ner**: non misurato. Non esiste la via senza PyTorch pronta (§4); misurarlo stasera avrebbe voluto dire un venv torch da 1,5 GB. È il primo passo della fase C.

**Precisione da correggere nelle regole prima del codice**: TEL solo con contesto (`tel`, `cell`, `+39`, `0039`) o formato mobile `3xx`; INDIRIZZO con nome proprio dopo il toponimo e opzionale CAP; URL ridotto al dominio; IP escluso per loopback e privati.

## 6. Piano di lavoro (dopo l'ok)

| Fase | Consegna | Accettazione | Stima |
|---|---|---|---|
| A | `rules(it)`, `columns`, `dictionary`, mappa e `restore`, CLI `scan/redact/restore/test`, corpus con verità nota (i 12 documenti + 20 annotati a mano) | recall ≥ 99% sulle categorie con formato, precisione ≥ 90%; `restore(redact(x)) == x` su tutto il corpus | 1 giorno |
| B | innesto in `external-exec.py` e `cross-verify.py`, guardia PreToolUse, classe dati, evento a registro, `report` con blocco anonymizer | una run esterna vera con `--anonymize on`: nel prompt inviato nessun valore del dizionario, output restituito con i valori | mezza giornata |
| C | wrapper ONNX di GLiNER, misura contro la libreria torch (venv usa-e-getta) sui 32 documenti, confronto `gliner_multi_pii` vs `GLiNER2-PII-multi` sui nomi italiani | nomi: recall ≥ 95%, precisione ≥ 90% con dizionario; tempo per documento sul Chromebook; RAM di picco | 1 giorno |
| D | generatore del dizionario lato gestionale (clienti, referenti, domini → `.fd-anonymizer.json` per progetto); aggiornamento del piano dell'agenzia e della sua guida IA | dizionario rigenerabile con un comando; piano dell'agenzia allineato | mezza giornata |
| E | masker in Claude Code: demone residente, tre adattatori hook (§2b), mappa per `session_id` che sopravvive alla compattazione, lista bianca per progetto (nomi di variabili, classi, URL, marchi, comuni), blocchi a monte (cartelle con export, URL di back office), comando `rivela` (traduce in locale una risposta con segnaposto), NER solo su prompt e `Read` di file dati, mai sugli output di Bash | una sessione vera su un progetto cliente con `confidential`: nel transcript nessun valore del dizionario né match delle regole; i file scritti da Claude contengono i valori giusti; latenza aggiunta per turno < 30 ms con demone caldo | 2 giorni |
| F | migrazione ai Function Hooks quando escono dal prototipo: gli adattatori a processo diventano un modulo in-process che parla con lo stesso demone; nessuna modifica al motore | stessa prova della fase E, latenza < 5 ms | mezza giornata, data ignota |

**Buchi dichiarati della fase E** (verificati nella documentazione degli hook, da riverificare a ogni versione di Claude Code): i file citati con `@` nel prompt, `CLAUDE.md` e il contesto di sistema non passano dagli hook; le immagini non si toccano (bloccate o passano intere); ciò che è già nel contesto non si toglie più (il filtro va acceso prima della sessione, `status` lo dice); strumenti MCP solo via `updatedMCPToolOutput`, da provare su chrome-bridge; le fughe indirette («il sindaco del paese») restano a una persona. Si mitigano con i blocchi a monte, non si chiudono.

## 7. Decisioni — prese da Franz il 2026-09-10 (00:05)

1. Classe dati: `restricted` resta un muro; nasce `confidential` = Claude lavora solo su testo pulito (prompt, output degli strumenti, file). Modalità normale dei progetti cliente.
2. Guardia: avvisa nel plugin pubblico; `guard: deny` come scelta di agenzia in `.fd-anonymizer.json`.
3. Corpus con verità nota: lo annota il modello (i 12 documenti esterni più ~20 documenti reali dei progetti: contratti, email, ticket, output di query), Franz revisiona a campione; si rimisura a ogni modifica delle regole e a ogni release che tocca il motore (`anonymizer test` nella suite).
4. Modello NER: si decide in fase C con i numeri sul corpus (`gliner_multi_pii` ONNX contro `GLiNER2-PII-multi` convertito).
5. Motore dentro il plugin (`fable-director/anonymizer/`), CLI autonoma fin dal primo giorno; il proxy dell'agenzia lo importerà.

Prossimo passo: fase A in una sessione nuova (brainstorming → piano di implementazione → codice con test), budget aperto con `--data-class restricted` perché il corpus è materiale cliente.

Le domande com'erano prima della decisione:

1. **Classe dati**: `restricted` cambia significato (brief) oppure nuova classe `confidential` (modalità di lavoro con masker acceso) e `restricted` resta un muro (raccomandato, e coerente con la fase E).
2. **Guardia**: avviso o rifiuto quando il modulo è spento e il testo contiene PII diretti. Raccomandato: avviso nel plugin pubblico, rifiuto come scelta di agenzia in `.fd-anonymizer.json`.
3. **Corpus con verità nota**: chi annota i 20 documenti aggiuntivi e con quale cadenza si rimisura (il piano dell'agenzia prevede 200 documenti a regime).
4. **Modello NER**: `gliner_multi_pii-v1` (ONNX pronto, italiano non dichiarato ma multilingue) o `GLiNER2-PII-multi` (italiano dichiarato, solo torch, conversione ONNX da fare). Si decide in fase C con i numeri.
5. **Dove vive il motore**: dentro il plugin (`fable-director/anonymizer/`, CLI autonoma) e il proxy dell'agenzia lo importa; oppure pacchetto separato come nel piano dell'agenzia. Raccomandato: dentro il plugin finché non c'è il proxy, con CLI già autonoma così lo spostamento è un `mv`.
