"""Country-independent rules and check digits."""
import ipaddress
import re

from . import Rule


def cf_ok(s: str) -> bool:
    s = s.upper()
    if len(s) != 16:
        return False
    odd = dict(zip("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ",
                   [1, 0, 5, 7, 9, 13, 15, 17, 19, 21, 1, 0, 5, 7, 9, 13, 15, 17, 19, 21,
                    2, 4, 18, 20, 11, 3, 6, 8, 12, 14, 16, 10, 22, 25, 24, 23]))
    even = {c: (int(c) if c.isdigit() else ord(c) - 65) for c in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"}
    try:
        tot = sum(odd[c] if i % 2 == 0 else even[c] for i, c in enumerate(s[:15]))
    except KeyError:
        return False
    return chr(65 + tot % 26) == s[15]


def piva_ok(s: str) -> bool:
    if not re.fullmatch(r"\d{11}", s):
        return False
    d = [int(c) for c in s]
    tot = sum(d[0:10:2]) + sum((2 * x if 2 * x < 10 else 2 * x - 9) for x in d[1:10:2])
    return (10 - tot % 10) % 10 == d[10]


def iban_ok(s: str) -> bool:
    s = re.sub(r"\s", "", s).upper()
    if not 15 <= len(s) <= 34 or not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]+", s):
        return False
    t = s[4:] + s[:4]
    return int("".join(str(int(c, 36)) for c in t)) % 97 == 1


def luhn_ok(s: str) -> bool:
    digits = [int(c) for c in re.sub(r"\D", "", s)]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def public_ip(s: str) -> bool:
    try:
        ip = ipaddress.ip_address(s)
    except ValueError:
        return False
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified)


RULES = [
    # scp-style git URLs (git@github.com:user/repo) are not e-mail addresses
    Rule("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-]*:[^\s/])"), None, 0),
    # Host group 1 is the DOMINIO span in domain-only mode; the whole match in full mode.
    Rule("URL", re.compile(r"\b(?:https?://|www\.)(?:[^\s/@<>\"')\]]*@)?([A-Za-z0-9.-]+\.[A-Za-z]{2,})(?::\d+)?[^\s<>\"')\]]*"), None, 1),
    Rule("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,4})?\b"), iban_ok, 0),
    Rule("CARTA", re.compile(r"\b(?:\d{4}[\s-]?){3}\d{4}\b"), luhn_ok, 0),
    Rule("IP", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), public_ip, 0),
]
