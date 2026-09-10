"""Columns engine: tabular text (CSV/TSV/JSON array of objects) whose header
names a personal-data column. The whole cell becomes one span, no regex.

Format is sniffed from the text, never from a file name. Offsets are exact:
a cell whose raw text cannot be mapped back exactly is skipped; a CSV with
multi-line quoted cells is left to the rules engine (flag `columns_skipped`).
"""
import csv
import io
import json
import re
import unicodedata
from typing import List, Optional, Tuple

from .spans import Mask, Span

# header (normalised) -> category. None = free text, left to rules.
PROFILES = {
    "generic": {
        "email": "EMAIL", "e_mail": "EMAIL", "mail": "EMAIL", "email_address": "EMAIL",
        "indirizzo_email": "EMAIL", "pec": "EMAIL", "emails": "EMAIL",
        "nome": "PERSONA", "cognome": "PERSONA", "name": "PERSONA", "first_name": "PERSONA",
        "last_name": "PERSONA", "firstname": "PERSONA", "lastname": "PERSONA",
        "nominativo": "PERSONA", "full_name": "PERSONA", "referente": "PERSONA",
        "contatto": "PERSONA", "nome_e_cognome": "PERSONA", "nome_cognome": "PERSONA",
        "telefono": "TEL", "tel": "TEL", "phone": "TEL", "cellulare": "TEL", "mobile": "TEL",
        "cell": "TEL", "phone_number": "TEL", "telephone": "TEL", "fax": "TEL", "sms": "TEL",
        "indirizzo": "INDIRIZZO", "address": "INDIRIZZO", "address_1": "INDIRIZZO",
        "address_2": "INDIRIZZO", "address1": "INDIRIZZO", "address2": "INDIRIZZO",
        "via": "INDIRIZZO", "indirizzo_completo": "INDIRIZZO", "street": "INDIRIZZO",
        "cap": "CAP_COMUNE", "postcode": "CAP_COMUNE", "zip": "CAP_COMUNE", "zip_code": "CAP_COMUNE",
        "citta": "CAP_COMUNE", "city": "CAP_COMUNE", "comune": "CAP_COMUNE",
        "codice_fiscale": "CF", "cf": "CF", "fiscal_code": "CF", "dni": "CF", "cod_fisc": "CF",
        "piva": "PIVA", "p_iva": "PIVA", "partita_iva": "PIVA", "vat": "PIVA", "vat_number": "PIVA",
        "siret": "PIVA", "vat_id": "PIVA", "iban": "IBAN",
        "data_di_nascita": "DATA_NASCITA", "birthday": "DATA_NASCITA", "birth_date": "DATA_NASCITA",
        "dob": "DATA_NASCITA", "nascita": "DATA_NASCITA", "date_of_birth": "DATA_NASCITA",
        "company": "ORG", "azienda": "ORG", "ragione_sociale": "ORG", "organization": "ORG",
        "societa": "ORG", "ditta": "ORG",
        "website": "DOMINIO", "sito": "DOMINIO", "domain": "DOMINIO", "domini": "DOMINIO",
        "dominio": "DOMINIO", "url": "DOMINIO", "site": "DOMINIO", "nome_dominio": "DOMINIO",
        "nomedominio": "DOMINIO", "domain_name": "DOMINIO", "hostname": "DOMINIO",
        "ip": "IP", "ip_address": "IP", "ip_registration_newsletter": "IP",
        "passwd": "SECRET", "password": "SECRET", "secure_key": "SECRET", "token": "SECRET",
        "reset_password_token": "SECRET", "api_key": "SECRET", "sconto_token": "SECRET",
        "note": None, "notes": None, "messaggio": None, "message": None, "descrizione": None,
    },
    "woocommerce": {
        "billing_first_name": "PERSONA", "billing_last_name": "PERSONA", "billing_email": "EMAIL",
        "billing_phone": "TEL", "billing_address_1": "INDIRIZZO", "billing_address_2": "INDIRIZZO",
        "billing_city": "CAP_COMUNE", "billing_postcode": "CAP_COMUNE", "billing_company": "ORG",
        "shipping_first_name": "PERSONA", "shipping_last_name": "PERSONA", "shipping_phone": "TEL",
        "shipping_address_1": "INDIRIZZO", "shipping_address_2": "INDIRIZZO",
        "shipping_city": "CAP_COMUNE", "shipping_postcode": "CAP_COMUNE", "shipping_company": "ORG",
        "customer_email": "EMAIL", "customer_user": "PERSONA", "customer_ip_address": "IP",
        "user_email": "EMAIL", "user_login": "PERSONA", "display_name": "PERSONA",
    },
    "cf7": {"your_name": "PERSONA", "your_email": "EMAIL", "your_tel": "TEL", "your_phone": "TEL",
            "your_message": None, "your_subject": None},
    "brevo": {"sms": "TEL", "whatsapp": "TEL", "landline_number": "TEL"},
    "mailchimp": {"email_address": "EMAIL", "first_name": "PERSONA", "last_name": "PERSONA",
                  "phone": "TEL", "address": "INDIRIZZO", "birthday": "DATA_NASCITA",
                  "tags_mailchimp": None},
    "prestashop": {"customer_e_mail": "EMAIL", "customer_email": "EMAIL", "mobile_phone": "TEL",
                   "home_phone": "TEL", "vat_number": "PIVA", "identification_number": "CF",
                   "id_customer": None, "id_gender": None, "customer_id": None,
                   "indirizzo_email": "EMAIL", "data_di_nascita": "DATA_NASCITA",
                   "indirizzo_completo": "INDIRIZZO", "citta": "CAP_COMUNE",
                   "ps_shop_url": None},
    "pixelbox": {"cliente": "ORG", "crm": "ORG", "fic_cliente": "ORG", "cliente_crm": "ORG",
                 "cliente_crm_match": "ORG", "clientestem": "ORG", "cliente_stem": "ORG",
                 "cliente_fic": "ORG", "aziende_fic": "ORG", "email_crm": "EMAIL",
                 "email": "EMAIL", "pec": "EMAIL", "domini": "DOMINIO", "match_dominio": "DOMINIO",
                 "dominio": "DOMINIO"},
}

_MULTI_CATS = {"DOMINIO", "EMAIL"}
_EMPTY = {"", "-", "null", "none", "n/a", "na", "nd", "n.d.", "0"}


def normalize_header(h: str) -> str:
    """lower, accents folded, `Cliente_CRM(match)` -> `cliente_crm_match`, `E-mail*` -> `e_mail`."""
    h = unicodedata.normalize("NFKD", h.replace("﻿", "").strip().strip('"\'').lower())
    h = "".join(c for c in h if not unicodedata.combining(c))
    h = re.sub(r"[*]", "", h)
    h = re.sub(r"[\s\-./()\[\]]+", "_", h)
    h = re.sub(r"_+", "_", h)
    return h.strip("_")


def build_headers(config: dict) -> Tuple[dict, List[str]]:
    """Merge the enabled profiles into one lookup. Returns (lookup, conflicts)."""
    lookup, conflicts = {}, []
    profiles = config.get("columns", {}).get("profiles", list(PROFILES))
    for name in profiles:
        for h, cat in PROFILES.get(name, {}).items():
            k = normalize_header(h)
            if k in lookup and lookup[k] != cat and lookup[k] is not None and cat is not None:
                conflicts.append(f"{k}: {lookup[k]} vs {cat} ({name})")
            lookup[k] = cat
    for h, cat in config.get("columns", {}).get("extra", {}).items():
        lookup[normalize_header(h)] = cat
    return lookup, conflicts


def detect(text: str) -> Optional[tuple]:
    t = text.lstrip("﻿")
    stripped = t.lstrip()
    if stripped[:1] in "[{":
        try:
            data = json.loads(t)
        except ValueError:
            return None
        rows = _json_rows(data)
        return ("json",) if rows else None
    lines = [ln for ln in t.splitlines() if ln.strip()][:6]
    if len(lines) < 2:
        return None
    best = None
    for d in (";", "\t", ","):
        try:
            counts = [len(next(csv.reader([ln], delimiter=d))) for ln in lines]
        except csv.Error:
            continue
        if counts[0] < 2:
            continue
        if any(c != counts[0] for c in counts[1:]):
            continue
        if best is None or counts[0] > best[1]:
            best = (d, counts[0])
    if best is None:
        return None
    return ("csv", best[0])


def _json_rows(data):
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                return v
        return None
    if isinstance(data, list) and data and all(isinstance(x, dict) for x in data):
        return data
    return None


def _split_multi(value: str) -> List[Tuple[int, str]]:
    """(offset, part) for ' | ', ';' or ',' separated values inside one cell."""
    out = []
    for m in re.finditer(r"[^|;,]+", value):
        part = m.group(0)
        lead = len(part) - len(part.lstrip())
        core = part.strip()
        if core:
            out.append((m.start() + lead, core))
    return out


def _cells(line: str, delim: str):
    """Yield (raw_start, raw_end, value, quoted) per cell, offsets in `line`."""
    pos, n = 0, len(line)
    while pos <= n:
        if pos < n and line[pos] == '"':
            j = pos + 1
            buf = []
            while j < n:
                if line[j] == '"':
                    if j + 1 < n and line[j + 1] == '"':
                        buf.append('"'); j += 2; continue
                    break
                buf.append(line[j]); j += 1
            value = "".join(buf)
            end = j + 1
            # skip to delimiter
            k = end
            while k < n and line[k] != delim:
                k += 1
            yield (pos + 1, j, value, True)
            pos = k + 1
            if k >= n:
                break
        else:
            k = line.find(delim, pos)
            if k == -1:
                k = n
            raw = line[pos:k]
            lead = len(raw) - len(raw.lstrip())
            core = raw.strip()
            yield (pos + lead, pos + lead + len(core), core, False)
            pos = k + 1
            if k >= n:
                break


def find_spans(text: str, config: dict, taken: List[Span]) -> List[Span]:
    fmt = detect(text)
    if not fmt:
        return []
    lookup, _ = build_headers(config)
    wl = {str(w).casefold() for w in config.get("whitelist", [])}
    found: List[Span] = []
    mask = Mask(len(text), taken)

    def add(start, end, cat, value):
        if not cat or value.casefold() in _EMPTY or value.casefold() in wl:
            return
        if cat in _MULTI_CATS:
            for off, part in _split_multi(value):
                if part.casefold() in wl:
                    continue
                a, b = start + off, start + off + len(part)
                if mask.free(a, b):
                    mask.take(a, b)
                    found.append(Span(a, b, cat, part, "columns"))
            return
        if mask.free(start, end):
            mask.take(start, end)
            found.append(Span(start, end, cat, value, "columns"))

    if fmt[0] == "json":
        data = json.loads(text)
        rows = _json_rows(data)
        for key in {k for row in rows for k in row}:
            cat = lookup.get(normalize_header(key))
            if not cat:
                continue
            kq = re.escape(json.dumps(key, ensure_ascii=False))
            for m in re.finditer(kq + r"\s*:\s*\"((?:[^\"\\\\]|\\\\.)*)\"", text):
                raw = m.group(1)
                try:
                    value = json.loads('"' + raw + '"')
                except ValueError:
                    continue
                if value != raw:
                    continue  # escaped content: offsets would not map, skip cell
                add(m.start(1), m.end(1), cat, value)
        return sorted(found, key=lambda s: s.start)

    delim = fmt[1]
    lines = text.split("\n")
    # multi-line quoted cells: a line with an odd number of quotes
    if any(ln.count('"') % 2 for ln in lines):
        return []
    offset = 0
    header = None
    for ln in lines:
        raw = ln[:-1] if ln.endswith("\r") else ln
        if header is None:
            if raw.strip():
                header = [lookup.get(normalize_header(c[2])) for c in _cells(raw, delim)]
        elif raw.strip():
            for i, (s, e, value, quoted) in enumerate(_cells(raw, delim)):
                if i >= len(header) or not value:
                    continue
                if raw[s:e] != value:
                    continue
                add(offset + s, offset + e, header[i], value)
        offset += len(ln) + 1
    return sorted(found, key=lambda s: s.start)
