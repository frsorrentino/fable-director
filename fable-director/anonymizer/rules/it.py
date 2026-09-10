"""Italian pack: CF, P.IVA, TEL, TARGA, INDIRIZZO, CAP_COMUNE, DATA_NASCITA, PROTOCOLLO."""
import re

from . import Rule
from .base import cf_ok, piva_ok

_TOPONYM = r"(?:via|viale|piazza|piazzale|piazzetta|corso|largo|vicolo|vico|strada|contrada|c\.da|località|loc\.|frazione|fraz\.|borgo|lungomare|salita|traversa|trav\.)"
_WORD = r"[A-Za-zÀ-ÿ'’.]+"
_PARTICLES = {"di", "del", "della", "dei", "degli", "delle", "de", "da", "d'", "san", "santa",
              "santo", "s.", "ss.", "la", "le", "lo", "il", "e", "dell'", "dei", "al", "alla",
              "ai", "sul", "sulla", "xx", "iv", "xxv", "ii", "iii"}


def _name_ok(name: str) -> bool:
    """Street name tokens: Titlecase, a lowercase particle, or ALL-CAPS >= 4 letters.
    Rejects 'via SGR 2' (ANSI sequence) and lowercase noise."""
    toks = [t for t in re.split(r"\s+", name.strip()) if t]
    if not toks:
        return False
    strong = False
    for t in toks:
        core = t.strip(".'’,")
        if not core:
            continue
        if core.lower() in _PARTICLES or re.fullmatch(r"[IVXLC]+", core):
            continue
        if core[0].isupper() and core[1:].islower():
            strong = True
        elif core.isupper() and len(core) >= 4:
            strong = True
        elif core.isupper() and len(core) < 4:
            return False
        elif core.islower():
            return False
    return strong


_ADDR = re.compile(
    r"\b((?i:" + _TOPONYM + r"))\s+"
    r"((?:" + _WORD + r",?\s+){1,5}?)"
    r",?\s*(?i:n\.?\s*|n°\s*|nr\.?\s*)?(\d{1,4}(?:[A-Za-z]|\s[A-Z])?(?:/[A-Za-z0-9]{1,3})?)(?![A-Za-z0-9])"
    r"(?:\s*[,–-]?\s*(\d{5})\s+((?:[A-ZÀ-Ü][a-zà-ÿ'’]+|[A-ZÀ-Ü]{2,})(?:\s+(?:[A-ZÀ-Ü][a-zà-ÿ'’]+|[a-z]{1,3}|[A-ZÀ-Ü]{2,}))*)"
    r"(?:\s*\(?[A-Z]{2}\)?)?)?")


def _addr_check(m: re.Match) -> bool:
    return _name_ok(m.group(2))


_NOT_COMUNE = {"euro", "eur", "usd", "utenti", "righe", "record", "byte", "token", "pratiche", "domini", "clienti", "email",
               "visite", "iscritti", "contatti", "ordini", "prodotti", "anni", "giorni", "mesi", "ore", "minuti", "secondi",
               "kb", "mb", "gb", "tb", "km", "mq", "pezzi", "unità", "articoli", "parole", "caratteri", "pagine", "file"}


def _cap_check(m: re.Match) -> bool:
    first = m.group(2).split()[0].strip("'’").casefold()
    return first not in _NOT_COMUNE


_TEL_CTX = r"(?i)(?:tel|telefono|cell|cellulare|mobile|phone|whatsapp|wa|fax)\.?\s*:?\s*"
_NUM = r"(?:\+\d{1,3}[\s.-]?|0039[\s.-]?)?\(?\d{2,4}\)?(?:[\s.-]?\d{2,4}){1,4}"
_TEL_WITH_CTX = re.compile(_TEL_CTX + r"(" + _NUM + r")(?![\d/])")
_TEL_INTL = re.compile(r"(?<![\w/+])((?:\+\d{1,3}|0039)[\s.-]?\(?\d{1,4}\)?(?:[\s.-]?\d{2,5}){1,4})(?![\d/])")
_TEL_MOBILE = re.compile(r"(?<![\w/+.])(3\d{2}[\s.-]?\d{3}[\s.-]?\d{3,4})(?![\d/])")
_TEL_FIXED = re.compile(r"(?<![\w/+.])(0\d{1,3}[\s.-]?\d{5,8})(?![\d/])")


def _tel_digits_ok(s: str) -> bool:
    n = len(re.sub(r"\D", "", s))
    return 6 <= n <= 15


_PIVA_CTX = re.compile(r"(?i)(?:p\.?\s?iva|partita\s+iva|vat(?:\s*(?:number|id|no\.?))?|c\.?f\.?\s*/\s*p\.?\s?iva)\s*[:.]?\s*(?:IT\s?)?(\d{11})\b")
_PIVA_IT = re.compile(r"\bIT\s?(\d{11})\b")
_PIVA_BARE = re.compile(r"(?<![\d./-])(\d{11})(?![\d./-])")
_NOT_PIVA_BEFORE = re.compile(r"(?i)(?:prot|n\.|nr|numero|ordine|fattura|ddt|pratica|fascicolo|r\.g|tel|cell|fax|iban|cod|codice|atto|id|ref|rif)[.\s:]*$")


def _piva_bare_ok(m: re.Match, text: str) -> bool:
    before = text[max(0, m.start() - 16):m.start()]
    return piva_ok(m.group(1)) and not _NOT_PIVA_BEFORE.search(before)


RULES = [
    Rule("CF", re.compile(r"\b[A-Z]{6}\d{2}[A-EHLMPRST]\d{2}[A-Z]\d{3}[A-Z]\b"), cf_ok, 0),
    Rule("PIVA", _PIVA_CTX, piva_ok, 1),
    Rule("PIVA", _PIVA_IT, piva_ok, 1),
    Rule("PIVA", _PIVA_BARE, _piva_bare_ok, 1, needs_text=True),
    Rule("DATA_NASCITA", re.compile(r"(?i)(?:nat[oa]\s+(?:a\s+[^\d,;:\n]{2,40}?\s+)?il|data\s+di\s+nascita|nascita|born\s+on|date\s+of\s+birth|dob)\s*:?\s*(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d{4}-\d{2}-\d{2})\b"), None, 1),
    Rule("PROTOCOLLO", re.compile(r"(?i)\b(?:prot(?:ocollo)?\.?\s*n?(?:\.|°)?|n\.\s*prot\.?|pratica\s+n\.?|fascicolo\s+n\.?|R\.?G\.?\s*n?\.?)\s*[:\s]?\s*(\d{2,12}(?:/\d{2,4})?)\b(?!/)"), None, 1),
    Rule("INDIRIZZO", _ADDR, _addr_check, 0, match_check=True),
    Rule("CAP_COMUNE", re.compile(r"(?<![\d.,/-])(\d{5})\s+((?:[A-ZÀ-Ü][a-zà-ÿ'’]+|[A-ZÀ-Ü]{3,})(?:\s+(?:[A-ZÀ-Ü][a-zà-ÿ'’]+|[a-z]{1,3}|[A-ZÀ-Ü]{3,}))*)(?:\s*\(?[A-Z]{2}\)?)?(?![\w])"), _cap_check, 0, match_check=True),
    Rule("TEL", _TEL_WITH_CTX, _tel_digits_ok, 1),
    Rule("TEL", _TEL_INTL, _tel_digits_ok, 1),
    Rule("TEL", _TEL_MOBILE, _tel_digits_ok, 1),
    Rule("TEL", _TEL_FIXED, _tel_digits_ok, 1, option="tel_no_context"),
    Rule("TARGA", re.compile(r"\b[A-HJ-NP-TV-Z]{2}\s?\d{3}\s?[A-HJ-NP-TV-Z]{2}\b"), None, 0),
]
