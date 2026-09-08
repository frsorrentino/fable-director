# Brief: media tools (video, voce, OCR) per fable-director

Data: 8 settembre 2026. Origine: sessione cliente med-systems.it, analisi di
sette video iLIFE contro uno script del cliente. Fatto con contact sheet ffmpeg
letti dal top model: ha funzionato ma senza trascrizione, senza verifica dei
tempi di parlato e con una lettura a 1 frame/2 s. Questo brief chiede alla
sessione fable-director di rendere il metodo ripetibile e a costo zero modello.

Scadenza d'uso: subito. Il preventivo video iLIFE è in corso.

## Cosa esiste già (verificato l'8/09, non rifare)

- venv con `faster-whisper` e `edge-tts`: `~/.claude/fable-director/tools/venv`
  (368 MB, Python 3.11, CPU only, 8 core, 6 GB RAM). Log: `tools/install.log`.
- `ffmpeg`/`ffprobe` di sistema; `tesseract` 5 con lingue `eng ita osd`.
- Chiave Gemini in `~/.claude/fable-director/cross-family.json` (provider
  `gemini`, `gemini-stable`, `gemini-image`; endpoint OpenAI-compat per i primi
  due, nativo `/v1beta` per l'image).
- Voce `media-tools` aggiunta a `~/.claude/fable-director/soft-deps.json`
  dalla sessione cliente (classi e hint_keywords): allineare, non duplicare.

Misure reali della sessione cliente:

| Prova | Esito |
|---|---|
| faster-whisper `small` int8, audio 68 s italiano | trascrizione corretta, 84 s (1,25× realtime); primo caricamento 63 s per il download del modello |
| edge-tts `it-IT-DiegoNeural`, 4 frasi | 4 mp3 in ~5 s; le durate hanno smentito il minutaggio del cliente (4 s di slot → 5,9 s parlati) |
| Gemini `gemini-3.6-flash`, generateContent nativo, mp4 10 MB inline base64, `response_mime_type: application/json` | 49 s, 3.692 token in / 1.237 out, 11 shot con timestamp e testo in sovrimpressione corretti |
| stesso test, campo `has_speech` | **falso positivo**: il file non ha traccia audio, il modello ha "trascritto" i sottotitoli bruciati. L'audio si verifica con ffprobe, mai col modello |
| contact sheet ffmpeg `fps=1,tile=8x9,drawtext=%{pts\:hms}` a 400 px | leggibile dal top model in una sola Read; a 320 px e 1 frame/2 s i testi piccoli si perdono |

## Deliverable

Quattro script in `fable-director/scripts/`, una voce di config, una skill,
test, changelog, release. Tutti gli script: stdout con le stesse righe
`STATUS/PROVIDER/CHECK/OUTPUT/DETAIL` di `external-exec.py` dove ha senso, exit
code non zero su errore, nessun fallback silenzioso.

### 1. `video-sheet.sh <file> [--fps N] [--cols N] [--width PX] [--out DIR]`

Zero modello. Stampa la scheda ffprobe (durata, risoluzione, tracce audio con
codec, bitrate) e produce `<basename>-sheet.jpg` con timestamp bruciato per
frame; se il video supera i 90 frame alla fps scelta, spezza in più sheet
numerati. Default: fps 1, 8 colonne, 400 px. Output finale: percorso dei jpg e
la riga "audio: none|<codec> <ch>ch" che è il fatto da cui parte tutto.

### 2. `transcribe.py <file> [--model small|medium] [--lang it] [--out F.srt] [--json]`

Esegue sé stesso con il python del venv se `faster_whisper` non importa.
`vad_filter=True`. Output SRT di default; `--json` produce
`{"segments":[{"start","end","text"}],"duration","model","elapsed"}`. Se il file
non ha traccia audio (ffprobe) esce con `STATUS: error` e lo dice, senza
invocare il modello. Il modello `medium` va scaricato la prima volta (~1,5 GB):
avvisare in output, non scaricare in silenzio.

### 3. `tts-timing.py (--text "…" | --file F) [--voice it-IT-DiegoNeural] [--rate +0%] [--out DIR]`

Il file è una lista di blocchi separati da riga vuota, con etichetta opzionale
in prima riga (`# 1 Attacco (0-4s)`). Per ogni blocco: mp3 via edge-tts e
durata via ffprobe; tabella `etichetta | slot dichiarato | parlato misurato |
delta`. Parsing dello slot dall'etichetta (`(a-bs)`) se presente. Voce di
riserva `it-IT-ElsaNeural`. Richiede rete verso Microsoft: senza rete
`STATUS: unavailable`.

### 4. `media-analyze.py --input FILE --spec "…" [--schema-json] [--out F] [--provider gemini-media] [--type SLUG]`

Rotta esterna a modello, quindi con le stesse guardie di `external-exec.py`:
importare da lì `require_open_budget`, `check_out_perimeter`, `log_exec`,
`http_failure`, `unavailable`, `billing_of` (importlib sul file accanto, non
copia). `--data-class restricted` blocca come già fa external-exec.

Provider nuovo in config, `"type": "media"`:

```json
"gemini-media": {
  "type": "media",
  "base_url": "https://generativelanguage.googleapis.com/v1beta",
  "model": "gemini-3.6-flash",
  "api_key_env": "GEMINI_API_KEY",
  "billing": "free",
  "inline_max_mb": 19,
  "note": "video/audio/immagini via generateContent nativo; sopra inline_max_mb usa la Files API (upload resumable, poi file_data.file_uri)"
}
```

Aggiungerlo al template di `cross-verify.py --init` e, per chi ha già la
config, farlo creare da `--doctor` con un messaggio esplicito (mai scrivere la
config senza dirlo). Mime dall'estensione (mp4, mov, webm, mp3, wav, m4a, png,
jpg, pdf). Chiave solo nell'header `x-goog-api-key`. Con `--schema-json`
impostare `response_mime_type: application/json` e rifare il controllo locale
di validità come external-exec. Loggare `external_exec` con `type` e i token
da `usageMetadata` per la telemetria.

Nel system prompt del contratto aggiungere una riga: "non dichiarare parlato,
lingua o musica: descrivi solo ciò che vedi e leggi; l'audio lo verifica il
chiamante". Motivo: il falso positivo misurato sopra.

### 5. Skill `fable-director/skills/analisi-video/SKILL.md`

Frontmatter come `delega-efficiente`. Trigger: analisi/confronto/riassunto di
video o audio, storyboard vs girato, preventivo montaggio, trascrizione,
verifica minutaggio voce. Procedura fissa, ogni passo a costo zero modello
finché possibile:

1. `video-sheet.sh` su ogni file → fatto audio + sheet.
2. `transcribe.py` solo sui file con traccia audio.
3. `tts-timing.py` se esiste uno script con voce da verificare.
4. Il top model legge sheet e trascrizioni e compila la tabella scene
   (timestamp, inquadratura, testo, riusabile per). Sotto ~10 minuti di video
   totale non serve altro.
5. `media-analyze.py` solo quando serve semantica su video lunghi o molti
   file, con budget aperto e `--type video-shotlist`; incrociare sempre con
   ffprobe per l'audio.
6. Output atteso: tabella scene + tabella lavorazioni (G girato / M grafica /
   V voce / S stock), come in
   `~/Desktop/workspaces/pixelfarm/clienti/med-systems.it/docs/analisi-video-script-ilife-2026-09-08.md`.

La skill rispetta il budget di complessità: aggiungerla implica citare in
`delega-efficiente` una riga sola ("media: skill analisi-video") e nulla più.

### 6. `soft-deps.json` (template del plugin, se esiste) e kernel

Voce `media-tools` con classi `video-analysis`, `audio-transcription`,
`media-review`, `voice-timing`, `ocr-frames`; `detect` = venv presente +
ffprobe; `hint_keywords` italiani e inglesi (video, filmato, spot, reel,
trascrizione, voice over, speaker, montaggio, storyboard, transcribe). Nel
kernel non aggiungere righe: la route-hint già legge le hint_keywords.

### 7. Test, changelog, release

- `tests/media-tools-verify.py`: sheet su un mp4 sintetico (ffmpeg `testsrc`
  + `sine`, 5 s) → jpg esiste e ha le dimensioni attese; transcribe su un file
  senza audio → `STATUS: error` senza caricare il modello; tts-timing con
  `--text` corto (skip pulito se offline); media-analyze senza budget →
  errore del gate, senza chiamate di rete.
- CHANGELOG voce 1.41.0 "media tools: video, voce, OCR a costo zero modello"
  con le misure di questo brief.
- `bash release.sh 1.41.0` (installa nella cache di entrambi gli account).

## File reali per la prova finale

Nello scratchpad della sessione cliente restano fino a fine sessione; copia
stabile sul server `med-sys-staging`, `www/med-sys.app/public_html/wp-content/uploads/`:
`2026/08/spot-ilife-web.mp4` (68 s, con voce), `2026/09/ilife-stress.mp4`
(55 s, senza audio, sottotitoli bruciati), `2026/08/ilifesomm.mp4` (102 s,
senza audio). Script voce del cliente: email 8/09, trascritto nel doc di
analisi citato sopra (§3).

## Fuori scope

Niente MCP server: gli script via Bash costano zero token di schema. Niente
OCR dedicato oltre a tesseract già installato: Gemini legge i testi in
sovrimpressione meglio e tesseract serve solo offline (`tesseract frame.png -
-l ita`). Niente TTS di qualità produzione: edge-tts serve al timing e
all'animatic, lo speaker resta umano.
