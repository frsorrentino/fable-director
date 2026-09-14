# Riscrittura fable-director: direzione e attendibilità (14/09/2026)

Nasce dalla lettura di [macrocode.ai](docs/macrocode-2026-09-14.md) e dalla verifica dei suoi
numeri sulla nostra telemetria. Conclusione in una riga: **la sua tesi regge, i suoi numeri no, e
la prova che regge ce l'avevamo già in casa senza averla letta.**

## 1. Grado di attendibilità della fonte

Marco Mancuso misura il proprio laboratorio, da solo. Applicando la nostra stessa regola di
densità — «`[candidata]` diventa regola confermata solo alla seconda occorrenza indipendente;
n=1 è overfitting» — i suoi numeri valgono come **ipotesi da misurare, mai come prove**.

| Sua affermazione | Attendibilità | Perché |
|---|---|---|
| Regole in prosa → l'agente deve leggere, capire e *scegliere* di obbedire: tre punti di rottura | **Alta** | Ragionamento strutturale, non un dato. E confermato dai nostri dati, sotto |
| 23 → 8 chiamate a strumenti, 15.000 → 50 token di orientamento | **Bassa** | A/B di un solo autore, ampiezza del campione non dichiarata, dato non pubblicato. Inoltre misura agenti che *cercano* le regole nei file: noi le iniettiamo, quindi non è trasferibile così com'è |
| Contesto gerarchico L1/L2/L3, orientamento −85 % | **Media** | Meccanismo solido e replicabile, numero non verificabile |
| Giurisdizione dei file su `session_id`: 10 → 0 violazioni | **Media** | Il meccanismo è verificabile per costruzione, il conteggio no |
| Costo della governance negativo | **Nulla** | L'articolo porta «Full content coming soon»: la tesi è dichiarata, i numeri non esistono |
| Classificazione 60/40 delle distorsioni | **Media** | Tassonomia utile, soglia arbitraria |

Nessun repository pubblico raggiungibile, nessun cliente, nessun caso di studio: tutti i suoi
numeri misurano lui stesso.

## 2. La verifica sui nostri dati — n=161 sessioni, 30 giorni

La sua tesi centrale è che le istruzioni in prosa **falliscono in silenzio**. La nostra telemetria
la conferma in modo netto, e non l'avevamo mai guardata da questa angolazione.

**Eventi imposti da un meccanismo** (hook, gate, CLI obbligatoria):

| Evento | Occorrenze |
|---|---|
| `delegation_outcome` | 1.606 |
| `route_hint` | 444 |
| `session_summary` | 161 (una per sessione) |
| `task_open` / `task_close` | 50 / 53 |
| `gate_deny` · `budget_flag` · `perimeter_deny` | 6 · 4 · 1 |

**Eventi che il kernel chiede al modello di registrare, in prosa:**

| Evento | Occorrenze |
|---|---|
| `reversal` | 27 |
| `verification` | 5 |
| `retry` | 3 |
| `escalation` | **0** |
| `script_promotion` | **0** |

Il kernel chiede cinque tipi di registrazione. **Due non sono mai comparsi in trenta giorni.**
`verification` è comparso su 5 dei 53 task chiusi: **il 9 %**. Nel frattempo ogni evento imposto da
un meccanismo è arrivato senza eccezioni: una `session_summary` per sessione, 161 su 161.

Questa è la seconda occorrenza indipendente che la nostra stessa regola richiede. La tesi passa da
`[candidata]` a confermata: **quello che un hook scrive è registrato, quello che il modello
promette di scrivere non lo è.** Nel kernel la frase c'è già, in fondo alla sezione telemetria. Non
l'avevamo trattata come la scoperta che è.

## 3. Il numero che smonta la scorciatoia

Il kernel iniettato a ogni sessione è **893 parole**, circa 1.200 token, scritti una volta nel
prefisso in cache. Su 161 sessioni fanno ~190.000 token di scrittura in cache al mese, contro
550.896.902 di `cache_creation` totale: lo **0,03 %**.

Quindi: **accorciare il kernel non fa risparmiare niente di misurabile.** Chi legge i numeri di
Mancuso e conclude «tagliamo la prosa per risparmiare token» sta ottimizzando lo 0,03 %. La ragione
per riscrivere non è il costo, è che il 91 % delle verifiche non viene registrato e due istruzioni
su cinque non vengono eseguite mai.

## 4. Direzione della riscrittura

Il principio, derivato dai dati e non dalla fonte: **ogni regola del kernel o diventa un
meccanismo, o smette di essere una regola e diventa un principio.** Niente istruzioni che
descrivono un comportamento che nessuno verifica.

### Priorità 1 — meccanizzare i tre eventi rotti (prove: 0 %, 0 %, 9 %)

- `verification`: lo Stop hook **già esegue** il comando dichiarato in `budget-open --verify`.
  Deve registrare lui l'evento con l'esito, invece di chiederlo al modello. Da 9 % a 100 % senza
  cambiare il comportamento di nessuno.
- `escalation`: l'hook conta già i fallimenti (`fail_streak`, 3 occorrenze). Al terzo fallimento
  scrive lui l'evento con la classe dedotta, e lascia al modello solo la diagnosi.
- `script_promotion`: non è osservabile da un hook. Non è una regola: diventa una **domanda** che
  `budget-close` pone quando il tipo di task ricorre per la seconda volta su rotta modello. Il dato
  per farlo è già nella coda di promozione che `report` stampa.

Costo stimato: mezza giornata. È il punto con più prove e meno rischio.

### Priorità 2 — `fd state`, lo stato invece del racconto

Un comando che stampa in JSON: budget aperto o chiuso con il consuntivo, quota delle due finestre,
perimetro di scrittura attivo, rotta suggerita dagli assi con l'asse che permette e quello che
vieta, e **le sole azioni lecite adesso**. Il kernel si riduce a: «prima di decidere una rotta
chiama `fd state`; fa fede quello che risponde».

Metà del meccanismo esiste già: il gate PreToolUse che nega `Agent`/`Task`/`Workflow` senza budget
aperto **è** una transizione guardata. Manca esporre lo stato invece di descriverlo.

Da misurare come ha fatto lui, ma sui nostri dati: chiamate a strumenti per task e occorrenze di
`gate_deny` prima e dopo. Se `gate_deny` scende, il menù sta funzionando.

**Cautela:** la prosa porta anche le *ragioni* — perché l'asse 2 vince sull'asse 4, il fallimento
Goodhart, il prezzo già pagato. Quelle non stanno in un menù di transizioni. Restano nel kernel, ed
è l'unica cosa che ci resta.

### Priorità 3 — profilo di contesto e context-pack (ipotesi, non prova)

Contro il nostro allarme `~480.789 fresh input/agente`. Profilo dichiarato accanto a `--paths`
(sempre / per il task / su richiesta / mai / tetto di token) e context-pack prodotto una volta
dall'orchestratore, obbligatorio sopra una soglia di fan-out. Il numero di Mancuso (−85 %) è una
sua misura: qui va verificato con un A/B nostro su due workflow paragonabili.

### Priorità 4 — giurisdizione legata alla sessione

Accanto alle glob di `--paths`, legare il permesso di scrittura all'identificativo di sessione, che
l'agente non può falsificare. Chiude il buco dell'incidente del 29/07, quando una sessione parallela
inglobò quattro migrazioni SQL di un'altra.

### Cosa NON fare

- Non accorciare il kernel per risparmiare token: sarebbe lo 0,03 %.
- Non adottare i numeri di Mancuso come nostri. Le sue sono ipotesi; le misure le facciamo qui.
- Non toccare gli assi 1 e 2. Nessun dato li mette in discussione.

## 5. Verifica di fine lavoro

Dopo la priorità 1, su 30 giorni di sessioni nuove:

- `verification` ≥ 90 % dei `task_close` (oggi 9 %)
- `escalation` > 0 quando `fail_streak` ≥ 3 (oggi 0 su 3)
- nessuna regressione su `session_summary` (oggi 161 su 161)

Se dopo trenta giorni `verification` è ancora sotto il 90 %, il meccanismo è scritto male e va
rifatto, non riscritto in prosa più insistente.
