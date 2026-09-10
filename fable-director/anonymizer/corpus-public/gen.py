#!/usr/bin/env python3
"""Generate the PUBLIC synthetic corpus (anonymizer test + tests/anonymizer-verify.py).

Deterministic (seed 7). Every identifier is invented: check digits are valid
so the validators are exercised, but no CF, IBAN, card, plate or person is
real. Truth spans are recorded while the text is built, so annotations are
exact by construction. Re-run after changing a document: the files are
committed, the generator is the source.
"""
import json
import random
import string
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from anonymizer.rules.base import cf_ok, piva_ok, iban_ok, luhn_ok  # noqa: E402

rng = random.Random(7)

FIRST = ["Teodora", "Ilario", "Vittorina", "Cosimo", "Gelsomina", "Baldassarre", "Ortensia", "Ludovico", "Clelia", "Anselmo"]
LAST = ["Vanzetti", "Corradengo", "Pellizzari", "Scarafoni", "Tortorella", "Brancaleone", "Zampolini", "Ferraguti", "Quaranta", "Lombardozzi"]
ORGS = ["Officine Zampolini S.r.l.", "Panificio Corradengo di Ilario Corradengo", "Studio Legale Vanzetti e Associati", "Agriturismo Le Tre Querce S.n.c.", "Quaranta Impianti S.p.A."]
DOMAINS = ["zampolini-officine.example", "panificiocorradengo.example", "vanzettilex.example", "tre-querce.example", "quarantaimpianti.example"]
CITIES = [("88900", "Crotone", "KR"), ("88100", "Catanzaro", "CZ"), ("87100", "Cosenza", "CS"), ("89100", "Reggio Calabria", "RC"), ("20121", "Milano", "MI")]
STREETS = ["Via dei Platani", "Corso Umberto I", "Piazza San Francesco", "Viale della Repubblica", "Vicolo Stretto", "Contrada Fontanelle"]


def person():
    return f"{rng.choice(FIRST)} {rng.choice(LAST)}"


def email(name=None, dom=None):
    n = (name or person()).lower().replace(" ", ".")
    return f"{n}@{dom or rng.choice(DOMAINS)}"


def cf():
    while True:
        s = "".join(rng.choice(string.ascii_uppercase) for _ in range(6)) + f"{rng.randint(50, 99):02d}" \
            + rng.choice("ABCDEHLMPRST") + f"{rng.randint(1, 31):02d}" + rng.choice(string.ascii_uppercase) + f"{rng.randint(100, 999)}"
        for c in string.ascii_uppercase:
            if cf_ok(s + c):
                return s + c


def piva():
    while True:
        base = "".join(rng.choice(string.digits) for _ in range(10))
        for c in string.digits:
            if piva_ok(base + c):
                return base + c


def iban(country="IT"):
    bban = rng.choice(string.ascii_uppercase) + "".join(rng.choice(string.digits) for _ in range(22)) if country == "IT" \
        else "".join(rng.choice(string.digits) for _ in range(18))
    for k in range(2, 99):
        s = f"{country}{k:02d}{bban}"
        if iban_ok(s):
            return s
    raise RuntimeError


def iban_spaced(s):
    return " ".join(s[i:i + 4] for i in range(0, len(s), 4))


def card():
    while True:
        base = "4" + "".join(rng.choice(string.digits) for _ in range(14))
        for c in string.digits:
            if luhn_ok(base + c):
                s = base + c
                return " ".join(s[i:i + 4] for i in range(0, 16, 4))


def plate():
    L = "ABCDEFGHJKLMNPRSTVWXYZ"
    return f"{rng.choice(L)}{rng.choice(L)} {rng.randint(100, 999)} {rng.choice(L)}{rng.choice(L)}"


def mobile():
    return f"3{rng.randint(20, 99)} {rng.randint(100, 999)} {rng.randint(1000, 9999)}"


def fixed():
    return f"0{rng.randint(10, 999)} {rng.randint(100000, 999999)}"


def address(with_cap=True):
    cap, city, prov = rng.choice(CITIES)
    a = f"{rng.choice(STREETS)} {rng.randint(1, 180)}"
    if with_cap:
        a += f", {cap} {city} ({prov})"
    return a


def dob():
    return f"{rng.randint(1, 28):02d}/{rng.randint(1, 12):02d}/{rng.randint(1950, 2004)}"


class Builder:
    def __init__(self):
        self.parts, self.spans = [], []
        self.pos = 0

    def t(self, s):
        self.parts.append(s)
        self.pos += len(s)
        return self

    def v(self, cat, s):
        self.spans.append({"start": self.pos, "end": self.pos + len(s), "cat": cat, "text": s})
        return self.t(s)

    def text(self):
        return "".join(self.parts)


def write(name, b, annotator="generator"):
    (HERE / "docs" / f"{name}.txt").write_text(b.text(), encoding="utf-8")
    (HERE / "docs" / f"{name}.spans.json").write_text(json.dumps(
        {"doc": name, "annotator": annotator, "reviewed": True, "spans": b.spans}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def doc_legal():
    b = Builder()
    p1, p2 = person(), person()
    b.t("<task>\nSei un avvocato. Rivedi il contratto tra ").v("ORG", ORGS[0]).t(", P.IVA ").v("PIVA", piva())
    b.t(", con sede in ").v("INDIRIZZO", address()).t(", rappresentata da ").v("PERSONA", p1).t(" (CF ").v("CF", cf())
    b.t("), e il sig. ").v("PERSONA", p2).t(", nato a Crotone il ").v("DATA_NASCITA", dob()).t(", residente in ").v("INDIRIZZO", address(False)).t(".\n")
    b.t("Riferimenti: prot. n. ").v("PROTOCOLLO", "4471/2026").t(" del 3 marzo 2026, R.G. n. ").v("PROTOCOLLO", "812/2025").t(".\n")
    b.t("Pagamenti sull'IBAN ").v("IBAN", iban_spaced(iban())).t(" intestato a ").v("ORG", ORGS[2]).t(".\n")
    b.t("Non citare sentenze. Rispondi in italiano, massimo 800 parole. Il D.Lgs. 231/2002 e l'art. 1341 c.c. restano il riferimento.\n")
    b.t("La fattura n. 118/2026 del 12/03/2026 resta impagata; il totale è 1.250,00 euro; l'anno 2024 conta 250 pratiche.\n</task>\n")
    write("legal-spec", b)


def doc_email_thread():
    b = Builder()
    p = person()
    b.t("Da: ").v("PERSONA", p).t(" <").v("EMAIL", email(p)).t(">\nA: supporto <").v("EMAIL", "assistenza@" + DOMAINS[1]).t(">\nOggetto: rinnovo dominio ").v("DOMINIO", DOMAINS[1]).t("\n\n")
    b.t("Buongiorno, vi scrivo per il rinnovo. Potete richiamarmi al cell. ").v("TEL", mobile()).t(" oppure in ufficio (tel. ").v("TEL", fixed()).t(").\n")
    b.t("Il mio collega ").v("PERSONA", person()).t(" risponde al ").v("TEL", "+39 " + mobile()).t(" e via WhatsApp ").v("TEL", mobile()).t(".\n")
    b.t("Il numero d'ordine 2026-00841 e il codice sconto 7781234 non sono telefoni. Fatturato 2025: 3.500 euro.\n")
    b.t("Dati per la fattura: ").v("ORG", ORGS[1]).t(", partita IVA ").v("PIVA", piva()).t(", ").v("INDIRIZZO", address()).t(".\n")
    b.t("Carta usata per il pagamento: ").v("CARTA", card()).t(" (scadenza 09/29). Targa del furgone aziendale ").v("TARGA", plate()).t(".\n")
    b.t("Cordiali saluti\n").v("PERSONA", p).t("\n")
    write("email-thread", b)


def doc_readme():
    b = Builder()
    b.t("# fable-director\n\n[![CI](https://img.shields.io/badge/ci-passing-green)](https://github.com/frsorrentino/fable-director/actions)\n\n")
    b.t("Install from https://github.com/frsorrentino/fable-director or read the docs at https://docs.anthropic.com/claude-code.\n")
    b.t("The demo site of a client lives at https://").v("DOMINIO", DOMAINS[3]).t("/shop and its staging at https://staging.").v("DOMINIO", DOMAINS[3]).t(":8443/admin.\n")
    b.t("Local services: 127.0.0.1:8080, 192.168.1.20, 10.0.0.7; the public probe answers from ").v("IP", "93.184.216.34").t(".\n")
    b.t("Contact: ").v("EMAIL", "dev@" + DOMAINS[3]).t(". Version 1.43.1, Python 3.11.2, budget 2× / 3×, MAX_RETRIES_3 constant.\n")
    write("readme-urls", b)


def doc_csv(name, header, rows, delim=",", quote_idx=()):
    b = Builder()
    b.t(delim.join(header) + "\n")
    for row in rows:
        for i, (val, cat) in enumerate(row):
            if i:
                b.t(delim)
            q = i in quote_idx or (delim in val)
            if q:
                b.t('"')
            if cat and val:
                b.v(cat, val)
            else:
                b.t(val)
            if q:
                b.t('"')
        b.t("\n")
    write(name, b)


def doc_brevo():
    rows = []
    for _ in range(12):
        f, l = rng.choice(FIRST), rng.choice(LAST)
        rows.append([(email(f + " " + l), "EMAIL"), (f, "PERSONA"), (l, "PERSONA"), (mobile() if rng.random() < 0.5 else "", "TEL"),
                     (dob() if rng.random() < 0.4 else "", "DATA_NASCITA"), (rng.choice(["Clienti", "Rivenditori", ""]), None)])
    rows.append([("", None), ("", None), ("", None), ("", None), ("", None), ("", None)])
    doc_csv("export-brevo", ["EMAIL", "NOME", "COGNOME", "SMS", "BIRTHDAY", "TAGS"], rows, ";")


def doc_prestashop():
    rows = []
    for _ in range(10):
        f, l = rng.choice(FIRST), rng.choice(LAST)
        rows.append([(email(f + " " + l), "EMAIL"), (l, "PERSONA"), (f, "PERSONA"), (rng.choice(ORGS) if rng.random() < 0.3 else "", "ORG"),
                     (piva() if rng.random() < 0.3 else "", "PIVA"), (address(False), "INDIRIZZO"), (rng.choice(CITIES)[0], "CAP_COMUNE"),
                     (rng.choice(CITIES)[1], "CAP_COMUNE"), (mobile(), "TEL"), (str(rng.randint(1, 9)), None)])
    doc_csv("export-prestashop", ["Customer e-mail*", "Lastname*", "Firstname*", "Company", "VAT number", "Address 1*", "Postcode", "City*", "Mobile Phone", "Orders"], rows, "\t")


def doc_woocommerce():
    rows = []
    for _ in range(8):
        f, l = rng.choice(FIRST), rng.choice(LAST)
        rows.append([(str(rng.randint(1000, 9999)), None), (f, "PERSONA"), (l, "PERSONA"), (email(f + " " + l), "EMAIL"), (mobile(), "TEL"),
                     (address(False), "INDIRIZZO"), (rng.choice(CITIES)[1], "CAP_COMUNE"), (rng.choice(CITIES)[0], "CAP_COMUNE"), ("wc-completed", None)])
    doc_csv("export-woocommerce", ["order_id", "billing_first_name", "billing_last_name", "billing_email", "billing_phone", "billing_address_1", "billing_city", "billing_postcode", "status"], rows, ",", quote_idx=(5,))


def doc_pixelbox():
    rows = []
    for i, org in enumerate(ORGS):
        doms = [DOMAINS[i], DOMAINS[i].replace(".example", ".test")]
        b_dom = " | ".join(doms)
        rows.append([(org, "ORG"), (piva(), "PIVA"), (email("info", DOMAINS[i]), "EMAIL"), ("", None), (b_dom, "MULTI")])
    b = Builder()
    b.t("cliente,vat,email,pec,domini\n")
    for row in rows:
        for i, (val, cat) in enumerate(row):
            if i:
                b.t(",")
            if cat == "MULTI":
                parts = val.split(" | ")
                for j, part in enumerate(parts):
                    if j:
                        b.t(" | ")
                    b.v("DOMINIO", part)
            elif cat and val:
                b.v(cat, val)
            else:
                b.t(val)
        b.t("\n")
    write("export-pixelbox", b)


def doc_json():
    b = Builder()
    b.t("[\n")
    for i in range(6):
        f, l = rng.choice(FIRST), rng.choice(LAST)
        b.t(' {"id": ' + str(i) + ', "email": "').v("EMAIL", email(f + " " + l)).t('", "first_name": "').v("PERSONA", f).t('", "last_name": "').v("PERSONA", l)
        b.t('", "phone": "').v("TEL", "+39 " + mobile()).t('", "rating": ' + str(rng.randint(1, 5)) + ', "note": "ok"}' + ("," if i < 5 else "") + "\n")
    b.t("]\n")
    write("export-json", b)


def doc_log():
    b = Builder()
    for i in range(10):
        ip = f"{rng.choice([198, 203, 51])}.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}"
        priv = f"10.0.{rng.randint(0, 9)}.{rng.randint(1, 254)}"
        b.t(f"2026-09-0{i % 9 + 1} 12:0{i}:11 nginx[").t(str(1000 + i)).t("]: ").v("IP", ip).t(f" -> {priv} GET /wp-login.php 200 ")
        b.t("user=").v("EMAIL", email()).t("\n")
    b.t("2026-09-09 12:10:00 cron: backup ok, 1.2 GB, upstream ").v("IP", "4.5.6.7").t(" again ").v("IP", "4.5.6.7").t(" (v1.2.3 is a version, 2.1.267 too)\n")
    write("server-log", b)


def doc_contract():
    b = Builder()
    p = person()
    b.t("CONTRATTO DI FORNITURA\n\nTra ").v("ORG", ORGS[4]).t(" (di seguito Fornitore), C.F./P.IVA ").v("PIVA", piva()).t(", sede legale ").v("INDIRIZZO", address())
    b.t(",\ne ").v("PERSONA", p).t(", C.F. ").v("CF", cf()).t(", nato il ").v("DATA_NASCITA", dob()).t(", residente in ").v("INDIRIZZO", address()).t(", email ").v("EMAIL", email(p)).t(", tel ").v("TEL", fixed()).t(".\n\n")
    b.t("Art. 1 - Oggetto. Il Fornitore consegna il veicolo targato ").v("TARGA", plate()).t(" entro 30 giorni.\n")
    b.t("Art. 2 - Pagamento. Bonifico su ").v("IBAN", iban()).t(" oppure ").v("IBAN", iban_spaced(iban("DE"))).t(" entro 60 giorni; interessi ex D.Lgs. 231/2002.\n")
    b.t("Art. 3 - Foro. Foro di Crotone. Pratica n. ").v("PROTOCOLLO", "2026/77").t("; fascicolo n. ").v("PROTOCOLLO", "91").t(".\n")
    b.t("Letto, confermato e sottoscritto. Clausole 2, 3 approvate ex art. 1341 co. 2 c.c.\n")
    write("contract", b)


def main():
    for f in (HERE / "docs").glob("*"):
        f.unlink()
    doc_legal(); doc_email_thread(); doc_readme(); doc_brevo(); doc_prestashop(); doc_woocommerce()
    doc_pixelbox(); doc_json(); doc_log(); doc_contract()
    (HERE / "fd-anonymizer.json").write_text(json.dumps({
        "dictionary": {"PERSONA": [f"{f} {l}" for f, l in zip(FIRST, LAST)], "ORG": ORGS, "DOMINIO": DOMAINS},
        "whitelist": ["Pixelfarm", "Crostini"]}, ensure_ascii=False, indent=1) + "\n")
    print("generated", len(list((HERE / "docs").glob("*.txt"))), "docs")


if __name__ == "__main__":
    main()
