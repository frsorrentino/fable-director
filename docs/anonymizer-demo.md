# Anonymizer — demo da terminale (fase A)

Sequenza pronta da incollare. Mostra prima/dopo, la mappa dei segnaposto e il ripristino verificato byte per byte. Sostituisci `MAIL` con il tuo testo (una mail di un cliente, un export CSV): se non ne hai uno a portata, il primo blocco ne copia uno sintetico dal corpus pubblico.

```bash
A=~/Desktop/workspaces/personali/fable-director/fable-director-marketplace/fable-director/scripts/anonymizer.py
D=$(mktemp -d); MAIL=$D/mail.txt
cp ~/Desktop/workspaces/personali/fable-director/fable-director-marketplace/fable-director/anonymizer/corpus-public/docs/email-thread.txt "$MAIL"
#   ^ oppure: cp /percorso/della/tua/mail.txt "$MAIL"

# 1. Cosa vede il motore, senza scrivere niente
python3 "$A" scan "$MAIL"

# 2. Prima
cat "$MAIL"

# 3. Redazione: testo con [CAT_N], mappa in ~/.claude/fable-director/anonymizer/maps/demo.json (chmod 600)
python3 "$A" redact "$MAIL" --map demo --out "$D/mail.redacted.txt"
cat "$D/mail.redacted.txt"

# 4. La mappa (valori restano qui, mai nel testo che esce)
python3 -c "import json;d=json.load(open('$HOME/.claude/fable-director/anonymizer/maps/demo.json'));[print(k, n, e['forms']) for k,v in d['entries'].items() for n,e in v.items()]"

# 5. Ripristino, verificato byte per byte
python3 "$A" restore "$D/mail.redacted.txt" --map demo --out "$D/mail.restored.txt"
cmp "$MAIL" "$D/mail.restored.txt" && echo "RIPRISTINO ESATTO"
sha256sum "$MAIL" "$D/mail.restored.txt"

# 6. Stessa mappa, testo nuovo: i segnaposto restano gli stessi (stesso valore → stesso numero)
printf 'Risposta al cliente: confermo il numero %s\n' "$(python3 -c "import json;d=json.load(open('$HOME/.claude/fable-director/anonymizer/maps/demo.json'));print(d['entries']['TEL']['1']['forms'][0])")" | python3 "$A" redact --stdin --map demo

# 7. Con un dizionario di progetto (nomi, aziende, domini che le regole non vedono)
cat > "$D/.fd-anonymizer.json" <<'JSON'
{"dictionary": {"PERSONA": ["Cosimo Ferraguti"], "ORG": ["Panificio Corradengo di Ilario Corradengo"], "DOMINIO": ["panificiocorradengo.example"]},
 "whitelist": ["Pixelfarm"]}
JSON
(cd "$D" && python3 "$A" redact "$MAIL" --map demo2 | head -5)

# 8. Stato: config, profili di colonne, dizionario, mappe con età
python3 "$A" status
# pulizia
rm -f ~/.claude/fable-director/anonymizer/maps/demo.json ~/.claude/fable-director/anonymizer/maps/demo2.json; rm -rf "$D"
```

Cosa aspettarsi sul testo sintetico: `scan` conta EMAIL 2, TEL 4, PIVA 1, INDIRIZZO 1, CARTA 1, TARGA 1; il nome «Cosimo Ferraguti» e il dominio nudo «panificiocorradengo.example» nell'oggetto **non** vengono presi al passo 3 (nomi e domini senza `https://` li vede solo il dizionario, passo 7, o il NER di fase C); `cmp` tace e stampa `RIPRISTINO ESATTO`; al passo 6 il telefono riceve ancora `[TEL_1]`.

Provato il 10/09/2026 alle 09:55 sul testo sintetico: `cmp` identici, sha256 uguali, mappa con 10 valori.
