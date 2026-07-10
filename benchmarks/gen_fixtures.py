#!/usr/bin/env python3
"""Genera fixture deterministiche per il benchmark (seed fisso → riproducibile)."""
import json, random, string, sys
from pathlib import Path

ROOT = Path(__file__).parent / "fixtures"
random.seed(42)

def gen_batch(n=30):
    d = ROOT / "batch"; d.mkdir(parents=True, exist_ok=True)
    for i in range(1, n + 1):
        nums = [random.randint(1, 999) for _ in range(random.randint(5, 15))]
        (d / f"item{i:03d}.txt").write_text("\n".join(map(str, nums)) + "\n")

def gen_classify(n=30):
    d = ROOT / "classify"; d.mkdir(parents=True, exist_ok=True)
    kinds = [
        lambda: f"user{random.randint(1,99)}@example.com",
        lambda: f"https://site{random.randint(1,99)}.org/path",
        lambda: f"+39 3{random.randint(10,99)} {random.randint(1000000,9999999)}",
        lambda: "".join(random.choices(string.ascii_letters, k=random.randint(4, 10))),
    ]
    lines = [random.choice(kinds)() for _ in range(n)]
    (d / "items.txt").write_text("\n".join(lines) + "\n")

POS = {
    "batteria": ["La batteria dura davvero due giorni pieni, ben oltre le mie aspettative.",
                 "Ricarica completa in meno di un'ora, non ci speravo.",
                 "Dopo tre mesi la batteria tiene ancora come il primo giorno."],
    "spedizione": ["Spedizione rapidissima, arrivato in ventiquattro ore con imballo perfetto.",
                   "Corriere puntuale e pacco integro, esperienza impeccabile.",
                   "Tracking preciso e consegna anticipata di un giorno."],
    "qualita": ["Materiali solidi e finiture curate, si sente che è costruito bene.",
                "La qualità costruttiva è superiore a prodotti che costano il doppio.",
                "Ogni dettaglio è rifinito, niente scricchiolii o giochi."],
    "prezzo": ["A questo prezzo non c'è nulla di paragonabile sul mercato.",
               "Rapporto qualità prezzo eccellente, rifarei l'acquisto domani.",
               "Costa poco e rende come i modelli premium."],
    "assistenza": ["L'assistenza ha risposto in un'ora e risolto tutto al primo contatto.",
                   "Servizio clienti gentile e competente, mi hanno seguito fino alla soluzione.",
                   "Sostituzione gestita in tre giorni senza discussioni."],
    "usabilita": ["Interfaccia intuitiva, mia madre lo usa senza manuale.",
                  "Configurazione in cinque minuti, tutto funziona al primo colpo.",
                  "Comandi semplici e ben posizionati, ergonomia ottima."],
}
# Vincolo: le frasi NEG non devono evocare pericolo fisico (parti che si
# staccano, scosse, surriscaldamento) — quella dimensione è SOLO in SAFETY,
# altrimenti la ground truth segnalazione_sicurezza=NO produce falsi positivi
# sistematici in entrambi gli arm (visto nel run del 2026-07-08: precision 85%).
NEG = {
    "batteria": ["La batteria si scarica in mezza giornata anche in standby.",
                 "Dopo due settimane la durata è crollata del quaranta percento.",
                 "Si spegne al venti percento di carica residua, inaccettabile."],
    "spedizione": ["Pacco arrivato con una settimana di ritardo e scatola schiacciata.",
                   "Il corriere ha lasciato il pacco sotto la pioggia senza avvisare.",
                   "Tracking fermo per giorni, nessuna comunicazione."],
    "qualita": ["La plastica è sottile e dopo una settimana si è crepata sul bordo.",
                "Assemblaggio grossolano e accoppiamenti imprecisi, sembra un prototipo.",
                "Il rivestimento si è scolorito al primo lavaggio."],
    "prezzo": ["Costa troppo per quello che offre, ci sono alternative migliori a metà prezzo.",
               "Prezzo gonfiato dal marketing, il valore reale è la metà.",
               "Per questa cifra mi aspettavo molto di più."],
    "assistenza": ["Tre email all'assistenza e nessuna risposta in due settimane.",
                   "Il call center rimbalza la pratica da un operatore all'altro.",
                   "Garanzia negata con motivazioni pretestuose."],
    "usabilita": ["Il manuale è incomprensibile e l'app si blocca di continuo.",
                  "Menu contorti, servono dieci passaggi per la funzione base.",
                  "I pulsanti sono minuscoli e mal posizionati."],
}
SAFETY = [
    "Durante la ricarica l'alimentatore si è surriscaldato fino a fumare, ho dovuto staccarlo.",
    "Ho preso una scossa toccando il pannello posteriore mentre era collegato.",
    "Dopo dieci minuti d'uso ha iniziato a uscire odore di bruciato e una scintilla dal connettore.",
    "La lama si è staccata di colpo durante l'uso mancando di poco la mia mano.",
    "Il rivestimento rilascia un odore chimico fortissimo che fa bruciare gli occhi.",
    "Una parte piccola si è staccata ed è finita in bocca a mio figlio, rischio soffocamento serio.",
]
FILLER = ["L'ho comprato il mese scorso per uso quotidiano.",
          "Lo uso principalmente a casa, ogni giorno.",
          "Era un regalo per mio marito, lo usiamo entrambi.",
          "Prima avevo il modello vecchio della stessa marca.",
          "L'ho scelto dopo aver confrontato diverse recensioni."]


def gen_reviews(n=40, n_safety=6):
    """Shape 04: N recensioni che richiedono giudizio semantico per item
    (sentiment/tema con vocabolario chiuso + segnalazione sicurezza).
    Ground truth in expected/04-reviews.json (fuori da fixtures/: il task
    non deve vederla) — serve ad aggregate.py per l'accuracy per arm."""
    d = ROOT / "reviews"; d.mkdir(parents=True, exist_ok=True)
    themes = list(POS)
    safety_ids = set(random.sample(range(1, n + 1), n_safety))
    expected = {}
    for i in range(1, n + 1):
        rid = f"rev{i:03d}"
        tema = random.choice(themes)
        altro = random.choice([t for t in themes if t != tema])
        sentiment = random.choice(["positivo", "negativo", "misto"])
        if i in safety_ids:
            sentiment = random.choice(["negativo", "misto"])
        frasi = [random.choice(FILLER)]
        if sentiment == "positivo":
            frasi += random.sample(POS[tema], 3) + [random.choice(POS[altro])]
        elif sentiment == "negativo":
            frasi += random.sample(NEG[tema], 3) + [random.choice(NEG[altro])]
        else:  # misto: il tema dominante pesa di più
            frasi += random.sample(NEG[tema], 2) + [random.choice(POS[tema]),
                                                    random.choice(POS[altro])]
        if i in safety_ids:
            frasi.append(random.choice(SAFETY))
        random.shuffle(frasi)
        (d / f"{rid}.txt").write_text(" ".join(frasi) + "\n")
        expected[rid] = {"sentiment": sentiment, "tema": tema,
                         "segnalazione_sicurezza": "YES" if i in safety_ids else "NO"}
    exp_dir = Path(__file__).parent / "expected"
    exp_dir.mkdir(parents=True, exist_ok=True)
    (exp_dir / "04-reviews.json").write_text(
        json.dumps(expected, ensure_ascii=False, indent=1))


def gen_reviews_xl(n=240, n_safety=36):
    """Shape 05: come la 04 ma worker-heavy — N recensioni LUNGHE (12-16 frasi,
    ~400-700 token l'una, ~150k token totali di lettura obbligata). È la forma
    dove il differenziale di topologia può emergere (ancora cookbook ~2.5×:
    84-98% dei token di input sui worker): l'orchestratore non può evitare che
    qualcuno legga tutto — la domanda misurata è A CHE TARIFFA. Ground truth
    in expected/05-reviews.json, stessa struttura della 04.
    Il tema dominante resta non ambiguo: frasi del tema dominante SEMPRE in
    maggioranza stretta su ogni altro tema."""
    d = ROOT / "reviews_xl"; d.mkdir(parents=True, exist_ok=True)
    themes = list(POS)
    safety_ids = set(random.sample(range(1, n + 1), n_safety))
    expected = {}
    for i in range(1, n + 1):
        rid = f"rev{i:03d}"
        tema = random.choice(themes)
        others = [t for t in themes if t != tema]
        sentiment = random.choice(["positivo", "negativo", "misto"])
        if i in safety_ids:
            sentiment = random.choice(["negativo", "misto"])
        frasi = random.choices(FILLER, k=5)
        # dominante: 12-16 frasi; ogni altro tema al massimo 3 → mai ambiguo
        n_dom = random.randint(12, 16)
        if sentiment == "positivo":
            frasi += random.choices(POS[tema], k=n_dom)
            for t in random.sample(others, 3):
                frasi += random.choices(POS[t], k=random.randint(2, 3))
        elif sentiment == "negativo":
            frasi += random.choices(NEG[tema], k=n_dom)
            for t in random.sample(others, 3):
                frasi += random.choices(NEG[t], k=random.randint(2, 3))
        else:  # misto: dominante negativo in maggioranza + positivi dello stesso tema
            frasi += random.choices(NEG[tema], k=n_dom - 3)
            frasi += random.choices(POS[tema], k=3)
            for t in random.sample(others, 3):
                frasi += random.choices(POS[t], k=random.randint(2, 3))
        if i in safety_ids:
            frasi.append(random.choice(SAFETY))
        random.shuffle(frasi)
        (d / f"{rid}.txt").write_text(" ".join(frasi) + "\n")
        expected[rid] = {"sentiment": sentiment, "tema": tema,
                         "segnalazione_sicurezza": "YES" if i in safety_ids else "NO"}
    exp_dir = Path(__file__).parent / "expected"
    exp_dir.mkdir(parents=True, exist_ok=True)
    (exp_dir / "05-reviews.json").write_text(
        json.dumps(expected, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    ROOT.mkdir(parents=True, exist_ok=True)
    gen_batch(); gen_classify(); gen_reviews(); gen_reviews_xl()
    print(f"fixtures generate in {ROOT}")
