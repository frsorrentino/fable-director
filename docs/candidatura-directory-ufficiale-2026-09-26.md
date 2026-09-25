# Candidatura di fable-director alla directory Anthropic — 2026-09-26

Preparato il 26/09/2026 su richiesta di Franz (via master), sul modello del dossier di chrome-bridge (`chrome-bridge/docs/candidatura-directory-ufficiale-2026-09-25.md`, che ha già il percorso del portale, i limiti e l'esito dell'invio). Il modulo lo compila Franz da Chrome loggato; qui ci sono il testo di ogni campo, le risposte data handling e la checklist riga per riga. Fonti rilette il 26/09 alle 00:40: `claude.com/docs/plugins/pre-submission-checklist`, `claude.com/docs/plugins/submit`, `claude.com/docs/directory/publish`, `code.claude.com/docs/en/plugins-reference`, `code.claude.com/docs/en/hooks`.

## 0. Differenze rispetto a chrome-bridge

- **Il plugin è in una sottocartella** del repo (`frsorrentino/fable-director`, cartella `fable-director/`; la radice è il marketplace `fsorrentino`). Nel modulo va compilato **Plugin path = `fable-director`**. Il portale legge e scansiona **solo quella cartella**: README della scheda = `fable-director/README.md` (non il README di radice con le card), licenza = `fable-director/LICENSE` (copiata oggi) o il campo `license`.
- **Sottocartella = hold sistematico** «Scripts the validator couldn't follow»: il validatore segue solo script shell piatti; ogni hook nostro lancia `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/x.py"` (17 voci) e `session-kernel.sh` contiene `python3 -c`, sostituzioni di comando e `cat` di altri file. Non blocca: un reviewer legge la versione. L'unica via per toglierlo è spostare il plugin alla radice di un repo suo (scelta strutturale, §7).
- **Nessun server MCP, nessun launcher, nessun lockfile**: niente hold npx/lockfile.
- **Il nome contiene «Fable»**, il nome del modello Anthropic (Claude Fable 5.1): rischio di hold «Name matches a known brand» (§7). Il nome è immutabile dopo la pubblicazione.

## 1. Testo dei campi (letti dal repo, stato del working tree del 26/09)

| Passo | Campo | Valore |
|---|---|---|
| Source | Repository | `frsorrentino/fable-director` |
| Source | Plugin path | `fable-director` |
| Source | Branch or tag | vuoto = `main` (come chrome-bridge; ogni push riscansionato, un reviewer pubblica ogni versione finché non arriva l'auto-publish). Alternativa: il tag `v1.52.0` di release.sh, ma a ogni release andrebbe cambiato a mano in Settings |
| Source | Validate | da premere; il report vale per un commit solo |
| Listing details | (da plugin.json + README della cartella) | vedi sotto |
| Data handling | 4 domande | §3 |
| Compliance | contact email + 4 acknowledgement | la mail dell'account claude.ai di Franz; spuntare i quattro |
| Review and submit | How new versions reach the directory | GitHub push webhook (serve admin del repo: sì) |
| Review and submit | Auto-publish passing versions | on |
| Review and submit | Listed on | **Claude Code** (consigliato solo quello, §5) |

### 1a. `fable-director/.claude-plugin/plugin.json` (aggiornato oggi, `claude plugin validate --strict` verde)

- **name** (immutabile): `fable-director`.
- **displayName** (nuovo): `Fable Director`.
- **version**: `1.52.0` (preparata, non rilasciata).
- **description** (immutata, 430 caratteri; il portale la usa come descrizione breve): «Keeps Claude Code from spending your quota on work the top model didn't need to do: declared pre-budgets with deterministic 2x/3x enforcement, cheap-executor routing, objective token telemetry. Small one-offs pay a 5-20% premium (measured) - built for sessions that delegate. New in 1.42: reads video and audio for you at zero Claude tokens - a whole clip in one contact-sheet image, transcription on your CPU, voice-over timing checked before the studio; long footage on free-tier Gemini.» Il «New in 1.42» invecchia: se Franz vuole accorciarla, è il momento (la descrizione breve segue la versione live).
- **author**: `Francesco Sorrentino`, email `info@francescosorrentino.com`, url `https://github.com/frsorrentino` (un solo sistema di scrittura).
- **homepage** `https://github.com/frsorrentino/fable-director#readme`; **repository** `https://github.com/frsorrentino/fable-director`; **license** `MIT`.
- **privacyPolicyUrl** (nuovo): `https://github.com/frsorrentino/fable-director/blob/main/docs/privacy.md` — pagina reale dopo il push. Il repo **non ha GitHub Pages** (`gh api .../pages` → 404): se Franz preferisce un URL senza cromo GitHub, attivare Pages su `main`/`docs` e passare a `https://frsorrentino.github.io/fable-director/privacy` (cambio di un campo). L'alternativa raw (`raw.githubusercontent.com/.../docs/privacy.md`) è testo nudo, sconsigliata per un reviewer.
- **icon** (nuovo): `./.claude-plugin/icon.svg`, quadrata 256×256, un ciak con tre barre (budget, quota, contesto). Claude Code conosce le chiavi `icon`, `screenshots`, `classification`, `privacyPolicyUrl`, `supportUrl`, `bugs`, `termsOfService` (changelog del binario 2.1.283: «Fixed `claude plugin validate` reporting `privacyPolicyUrl`, `supportUrl` and other listing metadata keys as unknown fields»), quindi `--strict` non le segnala.
- **keywords**: `delegation, budget, cost-control, token-savings, telemetry, statusline, subagents`.
- **Descrizione lunga** = `fable-director/README.md` (≥ 40 parole fuori dai blocchi di codice: sì, ~1.100). È il README tecnico («Why a hook and not an instruction», componenti, learning loop) senza le card del README di radice: leggibile e vero, ma non è la scheda commerciale. Oggi ci sono aggiunte la sezione «Data handling» e il comando d'installazione corretto (`fable-director@fsorrentino`: le guide dicevano `@pixelfarm`, nome del marketplace cambiato nella 1.31.0). Se Franz vuole la scheda con le card, va deciso quale README mettere nella cartella (§7).

## 2. Checklist di pre-submission, riga per riga (working tree del 26/09)

**Passa (verificato)**
- Cartella con `.claude-plugin/plugin.json`; `claude plugin validate --strict fable-director` e `--strict .` (marketplace) → `✔ Validation passed` su Claude Code 2.1.283 (validatore più severo dalla 2.1.283).
- Un solo plugin per invio: il marketplace ne elenca due (claude-master via git-subdir), ma si invia la sola cartella `fable-director`.
- Ogni file che gli hook usano sta nella cartella del plugin; nessun percorso di `plugin.json` fuori da essa (nessun campo componente: layout standard).
- Nessun symlink, submodule, LFS; nessun `.DS_Store`/`Thumbs.db`; nomi validi su Windows/macOS, nessuna coppia che differisce solo per maiuscole; nessun `.gitattributes`.
- Repo: 244 file tracciati, `git archive HEAD` 2,6 MB (< 50 MiB); cartella del plugin: 108 file (< 512), nessun file > 256 KiB (il più grande `scripts/fd-telemetry.py`, 125 KiB), solo file di testo + 1 SVG (nessun PNG, PDF, zip, binario).
- `name` in kebab-case ASCII, non riservato, non generico; `displayName` e `author.name` in un solo sistema di scrittura; `description`, `author`, `version` presenti.
- README nella cartella ≥ 40 parole; `LICENSE` MIT nella cartella + campo `license`.
- Nessun launcher (`npx`/`uvx`/…) in hook o script; nessun `.npmrc`/`bunfig.toml`/`uv.toml`; nessun `package.json`/lockfile nella cartella; nessun server MCP, nessun `.mcp.json`.
- Comandi degli hook: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/<file>.py" [argomento piatto]` e `bash "${CLAUDE_PLUGIN_ROOT}/scripts/session-kernel.sh"`: percorso pieno da `${CLAUDE_PLUGIN_ROOT}`, nessun'altra variabile, sostituzione, wildcard o `-c` (la regola che **blocca** per i plugin in sottocartella).
- `hooks/hooks.json` valido, 12 eventi tutti nella hooks reference (DirectoryAdded, PreModelSwitch, PostModelSwitch, SubagentStart/Stop, PostToolUseFailure compresi); `hooks` non elencato in `plugin.json`.
- Front matter YAML valido con `description` testuale in 3 skill, 2 agenti, 6 comandi; cartelle `skills/`, `agents/`, `commands/`, `hooks/` con la grafia attesa.
- Nessuna credenziale reale in nessun file (nessun `${user_config.*}`; le chiavi stanno in `~/.claude/fable-director/cross-family.json` o in variabili d'ambiente, fuori dal repo).
- Sorgente leggibile: Python e bash, niente codice compilato o minificato.
- `claude plugin eval` eseguito il 25/09 (6/6 casi, `docs/plugin-eval-2026-09-25.md`), come la checklist chiede prima dell'invio.

**Hold per reviewer, attesi (non bloccano)**
- **«Scripts the validator couldn't follow»** per ogni hook (plugin in sottocartella + script non shell): 17 voci. Evitabile solo spostando il plugin alla radice di un repo proprio.
- **«Uses a credential from the user's machine»**: `cross-verify.py`/`external-exec.py` leggono `XAI_API_KEY` dall'ambiente e la chiave Gemini dal file di configurazione, e le mandano al provider. Chrome-bridge ne ha presi 8 dello stesso tipo. Risposta per il reviewer: opt-in, documentato in README «Data handling», `docs/privacy.md` e `docs/EXTERNAL-MODELS.md`; `--data-class restricted` rifiuta la via.
- **`allowed-tools: Bash`** nei sei comandi (chrome-bridge ha avuto l'hold su `commands/observe.md`): restringerli ai comandi esatti è una scelta con rischio di rottura (§7).
- **«Name matches a known brand» / «may be confused»** possibile su «Fable» (§7).

**Warning attesi**
- «download-and-run command» nei docs: `curl -fsSL https://antigravity.google/cli/install.sh | bash` in `docs/EXTERNAL-MODELS.md` — **fuori dalla cartella del plugin**, quindi forse non visto; `media-doctor.py --setup` esegue `pip install` in un venv (non da hook: consentito, ma il testo lo dice).
- Icona e `privacyPolicyUrl`: presenti da oggi, il warning di chrome-bridge non dovrebbe ripetersi.

**Fatto oggi nel repo (commit locali, nessun push)**
1. `plugin.json`: `displayName`, `author.url`, `homepage`, `repository`, `privacyPolicyUrl`, `icon`; versione 1.52.0.
2. `.claude-plugin/icon.svg`; `fable-director/LICENSE` (copia della radice).
3. `docs/privacy.md` (cosa legge, cosa scrive, cosa esce, conservazione).
4. Sezione «Data handling» (4 righe) in `README.md` di radice e in `fable-director/README.md`.
5. `fable-director@pixelfarm` → `fable-director@fsorrentino` in README, INSTALL, ONBOARDING, README del plugin (comando morto dalla 1.31.0).

## 3. Data handling: risposte proposte (in inglese, come vanno nel portale)

Opzioni a scelta come per chrome-bridge (Reads only · Yes, listed in README · Not retained · No), con questo testo dove il modulo lascia scrivere:

- **Does the plugin read or store personal data?** — «It reads what Claude Code hands its hooks (session id, model, tool names, the path of the session transcript, from which it takes token counts only), the user's `settings.json`, and the files the user points its media tools or anonymizer at. It stores token telemetry, budgets and handoffs under `~/.claude/fable-director/` on the user's machine; the anonymizer keeps the map between original values and placeholders there too (mode 0600). No message text is stored.»
- **Does it send data to services other than its declared connectors?** — «Not by default. Three opt-in paths exist and are listed in the README and in the privacy policy: external model routes (Google Gemini, the Codex/Antigravity CLI, xAI paid) that receive the text or media the user passes to them after the user configures a key; an error report sent to GitHub as an issue only after the user says yes; and downloads the user starts (`pip install` into a venv, the Whisper model from Hugging Face). A task marked restricted refuses the external routes.»
- **How long does it keep data?** — «Nothing is retained by the developer. Local files stay on the user's machine until the user deletes them; the error log can be disabled.»
- **Intended for people under 18?** — «No.»

## 4. Sicurezza: cosa dire e cosa manca

Cosa c'è e va citato (la scansione cerca «behavior that a plugin doesn't disclose»):
- README «Data handling», «Error reports: local, and only on your yes», INTERNALS «Enforced, advisory, and what leaves your machine», `docs/privacy.md`, `docs/EXTERNAL-MODELS.md`; `SECURITY.md` con private vulnerability reporting.
- Il plugin **scrive in `settings.json`** due volte (statusLine alla prima sessione, `autoUpdate` una tantum): è comportamento che la scansione può classificare come «changing Claude's permission settings» se non dichiarato. Ora è dichiarato in README e privacy con backup e opt-out. Il reviewer potrebbe comunque chiedere perché un plugin tocchi `settings.json` da solo: la risposta è nel CHANGELOG 1.50.1 (installazione senza comando) e in ONBOARDING (auto-update dichiarato).
- Gli hook `PreToolUse` **negano** chiamate (Agent/Task/Workflow senza budget, Write/Edit/Bash fuori perimetro): è la funzione del plugin, dichiarata in README «What is actually enforced». Da dire al reviewer se chiede.

Cosa manca o è debole:
- La chiave xAI letta dall'ambiente (`XAI_API_KEY`) è esattamente il pattern che la checklist mette in hold; la via `userConfig` con `sensitive: true` la eviterebbe, ma cambia il modo in cui la chiave arriva agli script (scelta, §7).
- `docs/privacy.md` è nuovo: rileggerlo prima dell'invio, è la prima cosa che il reviewer confronta con il codice.

## 5. Test, superfici, screenshot

- **Test**: suite `tests/` (52 file) + `tests/transcript-contract/run.py`. Il 26/09 alle 01:00 la corsa è stata interrotta su richiesta della master (macchina riservata a un render): verdi transcript-contract (6 fixture), anonymizer, bg-session, budget-close-session, budget-reopen, cc-features; gli altri 46 file non eseguiti. Da rilanciare per intero prima della release (`release.sh` lo fa). `claude plugin validate --strict` verde su plugin e marketplace alle 00:50.
- **Prompt-audit nativo** (`/doctor prompt-audit fable-director`): un primo giro `claude -p` è scaduto a 15 minuti senza rapporto (48 letture, 6 comandi negati in dontAsk); il secondo è stato fermato per il render. Da rifare in sessione interattiva domattina. Nel frattempo un controllo a zero token dei percorsi citati nella superficie prompt (kernel, skill, agenti, comandi, README, INTERNALS, STATUSLINE, EXTERNAL-MODELS) non trova percorsi inesistenti; il comando stantio `fable-director@pixelfarm` è corretto.
- **Superfici** (`claude.com/docs/plugins/platform-support`): gli hook sono ignorati in Chat; senza hook il plugin è solo tre skill e sei comandi che chiamano script Python assenti in Chat. In Cowork gli hook girano solo «when the Cowork session runs on your computer», ma la statusline, i tool Agent/Workflow e la telemetria dei transcript sono di Claude Code. Consiglio: **Listed on = Claude Code soltanto**. Riga da aggiungere al README della cartella se Franz conferma: «The plugin needs Claude Code: in claude.ai chat only the skills load and they have nothing to run.»
- **Screenshot**: il portale non li chiede. La scheda mostra `fable-director/README.md`, che non ha immagini; le card sono nel README di radice (`assets/readme/`). Vedi §7.
- **Doppio plugin**: chi ha `fable-director@fsorrentino` dal marketplace e attiva la scheda dalla directory si ritrova anche `fable-director@synced`: due copie degli stessi hook (ogni gate e ogni meter due volte per evento). Riga da aggiungere al README accanto al Quickstart quando la scheda è live.

## 6. Cosa fa Franz, in ordine

1. Legge `docs/privacy.md` e le quattro risposte del §3; decide i punti del §7.
2. Rilascia la 1.52.0 (`bash release.sh 1.52.0`: suite, zip, push, tag, install) — la scheda legge `main`.
3. Su claude.ai: GitHub già collegato nell'organizzazione personale (fatto per chrome-bridge il 25/09).
4. `https://claude.ai/directory/manage` → Submit new → Plugin bundle → Repository `frsorrentino/fable-director`, **Plugin path `fable-director`**, branch vuoto → **Validate** → leggere il report (attesi gli hold del §2; nessun blocco atteso).
5. Data handling con il §3; Compliance con la mail; webhook on (come per chrome-bridge, segreto fuori dal repo in `~/.config/`); Listed on = Claude Code; Submit for review.
6. Alla versione «passes every check» o quando il reviewer la libera: **Publish**.

## 7. Decisioni che restano a Franz

1. **Il nome.** «Fable» è il nome del modello Anthropic: il portale può mettere in hold «Name matches a known brand» o un reviewer può chiedere di cambiarlo. Dopo la pubblicazione il nome non cambia più, e cambiarlo ora rompe le installazioni `fable-director@fsorrentino` (salvo `renames` in marketplace.json). Opzioni: tenere e argomentare (il plugin è nato con Fable 5.1, «director» qualifica), oppure `displayName` diverso (es. «Delegation Director») lasciando `name`.
2. **README della scheda.** La directory mostra `fable-director/README.md` (tecnico, senza card). Portare lì il README di radice con le card significa copiare le immagini dentro la cartella (5 PNG, 86-125 KiB l'una: ammesse, riferite con sintassi immagine Markdown) e tenere due README allineati, oppure spostare il plugin alla radice di un repo suo.
3. **Plugin alla radice di un repo proprio** (come chrome-bridge): toglie i 17 hold «Scripts the validator couldn't follow» e il problema del README, ma cambia marketplace, `release.sh`, il git-subdir di claude-master e le installazioni esistenti. Non per questa release.
4. **`allowed-tools` dei sei comandi**: restringere `Bash` ai comandi esatti (`Bash(python3 *fd-status.py*)` ecc.) per togliere l'hold; il rischio è un comando che chiede permesso a metà.
5. **`XAI_API_KEY` via `userConfig`** invece che dall'ambiente: toglie un hold, cambia `cross-verify.py`/`external-exec.py` e la doc.
6. **Descrizione breve**: togliere «New in 1.42» (invecchia sulla scheda).
7. **Listed on**: solo Claude Code (consigliato) o anche Cowork.
8. **privacyPolicyUrl**: blob GitHub (impostato) o GitHub Pages da attivare.
