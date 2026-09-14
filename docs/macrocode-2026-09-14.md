# Cosa prendere da macrocode.ai (14/09/2026)

Fonte: [macrocode.ai](https://www.macrocode.ai/) di Marco Mancuso, Head of AI in Herzum (Rende).
Progetto personale di ricerca, non un'azienda: un laboratorio dove lavora con 12 agenti
specializzati in una pipeline SDLC a quattro livelli (business, architettura, implementazione,
verifica). Articoli letti il 14/09/2026: `mechanical-enforcement`, `the-knowledge-architecture`,
`the-linguistic-api`, `the-immutability-illusion`, `negative-self-referential-tax`.

Lui governa la **correttezza** e non misura il costo. Noi misuriamo il **costo** e non governiamo
il contesto. Le due metà combaciano: qui sotto quello che vale la pena prendere, in ordine di
valore contro i nostri allarmi misurati.

## 1. L'API tipizzata al posto delle regole in prosa — il punto che ci riguarda di più

**La sua tesi:** le regole scritte in prosa per governare agenti **falliscono in silenzio**.
L'agente deve leggerle, capirle e *scegliere* di rispettarle: tre punti di rottura. La sua
alternativa è un'interfaccia che restituisce solo le transizioni lecite dallo stato corrente.
`node aff connect` → `{ mode: "idle", transitions: ["create-cr", "resume-bi", ...] }`.
Se la verifica non è stata eseguita, «submit for review» **non compare nel menù**: non c'è niente
da disobbedire. Ogni entità (change request, backlog, sessione) è una macchina a stati con
transizioni guardate, persistita sul filesystem, senza server.

**Il suo A/B misurato:**

| | Regole in prosa | API tipizzata |
|---|---|---|
| Chiamate a strumenti per task | 23 | 8 |
| Token di orientamento | ~15.000 | ~50 |

**Perché ci riguarda:** il kernel fable-director è ~4.400 parole di prosa iniettate a ogni
sessione, più la skill `delega-efficiente` di altre ~4.400 quando si apre. È esattamente la
forma che lui misura come inferiore. Metà del lavoro però ce l'abbiamo già: il gate PreToolUse
che nega Agent/Task/Workflow senza budget aperto **è** una transizione guardata. Manca l'altra
metà, cioè esporre lo stato invece di raccontarlo.

**Cosa faremmo:** un comando `fd state` che stampa in JSON lo stato corrente e le sole azioni
lecite — budget aperto o chiuso con il consuntivo, quota delle due finestre, rotta suggerita
dagli assi con l'asse che permette e quello che vieta, perimetro di scrittura attivo, azioni
disponibili adesso. Il kernel iniettato si riduce a poche righe: «chiama `fd state` prima di
decidere una rotta; fa fede quello che risponde». Da misurare come ha fatto lui, con un A/B
sulle stesse richieste: chiamate a strumenti e token di orientamento, prima e dopo.

**Cautela:** la prosa del kernel porta anche le *ragioni* (perché l'asse 2 vince sull'asse 4, il
Goodhart, la storia delle misure). Quelle non stanno in un menù di transizioni. La forma giusta è
probabilmente ibrida: stato e azioni dall'API, principi in una skill caricata solo quando gli assi
scattano — che è già come è organizzata `delega-efficiente`.

## 2. Contesto gerarchico e profili tipizzati per agente — contro il nostro allarme più caro

**Il suo meccanismo:** 615.000 token di conoscenza organizzati in tre livelli di sintesi.

| Livello | Contenuto | Costo |
|---|---|---|
| L3 sistema | una riga per entità | ~1.200 token in tutto |
| L2 entità | sommario per prodotto | ~500 token per entità |
| L1 dettaglio | specifica completa | ~2.000 token per file |

Ogni agente ha un **profilo di contesto in JSON** con quattro liste e un tetto:
`always`, `forCurrentTask`, `onDemand`, `never`, `tokenBudget`. Uno sviluppatore di interfaccia
vede le specifiche di schermo a L1 e **mai** i file di backend, con un tetto di 30.000 token.
L'orchestratore esplora **una volta sola** in fase di piano e registra i percorsi nel piano della
change request; da lì in poi risolvere il contesto è una ricerca di percorso, non una decisione
del modello. Orientamento sceso da ~35.000 a ~5.000 token, **meno 85 %**.

**Perché ci riguarda:** il nostro `report` segnala da settimane
«workflow input-dominated: ~480.789 fresh input/agente — corpus ri-letto cold da ogni agente».
È lo stesso problema, e lui lo ha risolto.

**Cosa faremmo:** aggiungere al contratto di delega un profilo di contesto dichiarato accanto a
`--paths`: cosa l'esecutore vede sempre, cosa per il task, cosa su richiesta, cosa mai, e un tetto
di token. Poi il pezzo che vale davvero: un **context-pack** prodotto una volta dall'orchestratore
(già nominato nella skill come «context-pack candidate» quando i file si riaprono) che diventa
obbligatorio sopra una soglia di fan-out, invece che un rimedio dopo il danno.

## 3. Giurisdizione dei file legata all'identità di sessione

**Il suo meccanismo:** un hook `enforce-file-jurisdiction.js` prima di ogni scrittura legge il
`session_id` dal runtime di Claude Code — **che l'agente non può falsificare** — lo confronta con
un file di lock che dice quale worktree gli è assegnato, e blocca ogni scrittura fuori. Risultato
dichiarato: da 10 violazioni non rilevate a zero.

**Cosa abbiamo già:** il perimetro `--paths` con hook sulle scritture e i `never_write` di
`.fd-perimeter.json`. **Cosa aggiunge lui:** il legame con l'identità di sessione invece che con
le sole glob. Con cinque o sei sessioni parallele sugli stessi repo — la nostra normalità, e
l'incidente del 29/07 con le migrazioni SQL inglobate da un'altra sessione lo dimostra — legare il
permesso alla sessione, non solo al percorso, chiude un buco che le glob non vedono.

## 4. Validatori anti-invenzione

Intercettori che rifiutano il lavoro dell'agente su criteri sintattici: conteggio delle sezioni
obbligatorie (contro il lavoro svolto a metà), campi **`source` e `methodology` accanto a ogni
dato numerico** (contro i numeri inventati), perimetro esplicito nei piani (contro lo scope che
si allarga).

Il secondo è quello buono per noi: un numero senza fonte dichiarata non passa. Vale per i report
di fable-director e, fuori da qui, per i pareri e le relazioni agli enti — dove un numero
inventato costa molto più di un token.

## 5. La classificazione 60/40

Su dieci distorsioni sistematiche osservate: **sei imponibili meccanicamente** (completezza, dati
fabbricati, validazione delle precondizioni, scope, granularità dei task, revisione multipla),
**quattro no** (ancoraggio alla frequenza nei dati di addestramento, escalation di complessità,
razionalizzazione a posteriori, pregiudizio tecnologico).

La sua conclusione: il controllo umano non diminuisce con la scala, **si concentra** — dal
procedurale, ormai automatizzabile, all'epistemico. È una versione meglio argomentata del nostro
elenco «Never delegate», e le quattro irriducibili sono un vocabolario migliore del nostro
«aesthetics / client-facing numbers / how to count».

## 6. Git come strato di immutabilità — non per noi, per i clienti

Sostiene che le catene standard (Jira → Confluence → GitHub → TestRail) danno un'immutabilità
*per policy*, non crittografica, e ne elenca sei modi di rompersi: migrazione di fornitore,
abbonamento scaduto, cancellazione di una issue da parte di un amministratore, accesso diretto al
database, perdita di dati del fornitore, prodotto dismesso. La sua alternativa: requisiti,
specifiche, change request e decisioni architetturali **dentro git**, commit firmati GPG, ogni
clone un testimone indipendente. «La manomissione non è impedita: è resa crittograficamente
visibile.» Tre anni e ~600 change request stanno in 30-50 MB.

Non serve a fable-director. Serve al lavoro d'agenzia con la pubblica amministrazione, dove la
tracciabilità va dimostrata a un terzo: è un servizio vendibile, non uno strumento interno.

## Quello che lui non ha

- L'articolo sul costo della governance (`negative-self-referential-tax`) dichiara la tesi —
  «i cambiamenti al framework costano meno di quelli applicativi» — e porta ancora
  «Full content coming soon»: **nessun numero pubblicato**.
- `SDLC-bench`, il banco di prova che dovrebbe misurare la qualità del processo, è annunciato, non
  pubblicato.
- Il rilascio open source v2.0 è annunciato ma dal sito non parte alcun collegamento a un
  repository, e non è emerso da una ricerca pubblica su GitHub.
- Nessun cliente o caso di studio: tutti i suoi numeri misurano il suo stesso laboratorio.

Noi abbiamo 161 sessioni misurate in 30 giorni. È esattamente il pezzo che gli manca.

## Ordine consigliato

1. `fd state` e riduzione del kernel, con A/B misurato come il suo (chiamate a strumenti, token di
   orientamento). È il cambiamento più grande e il più promettente.
2. Profilo di contesto nel contratto di delega e context-pack obbligatorio sopra una soglia di
   fan-out. Attacca l'allarme da 480k token per agente.
3. `source`/`methodology` obbligatori sui numeri nei report.
4. Giurisdizione legata alla sessione accanto alle glob del perimetro.

Nessuno di questi è stato implementato: questo documento è solo la distillazione della lettura.
