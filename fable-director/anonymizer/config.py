"""Configuration: DEFAULTS <- ~/.claude/fable-director/anonymizer.json <- .fd-anonymizer.json.

The project file is searched from cwd upwards to the git root or HOME. Its
`dictionary`, `whitelist` and `columns.extra` are merged, not replaced.
"""
import copy
import json
from pathlib import Path
from typing import Optional

DEFAULTS = {
    "enabled": False,
    "engines": ["columns", "rules", "dictionary"],
    "packs": ["base", "it"],
    "rules": {"tel_requires_context": True, "url": "domain-only"},
    "columns": {"profiles": ["generic", "woocommerce", "cf7", "brevo", "mailchimp",
                             "prestashop", "pixelbox"], "extra": {}},
    "ner": {"enabled": False, "model": "onnx-community/gliner_multi_pii-v1",
            "file": "model_int8.onnx", "threshold": 0.5,
            "labels": ["person", "organization", "address", "date of birth"],
            "max_mb": 800, "timeout_s": 60},
    "dictionary": {"files": [], "PERSONA": [], "ORG": [], "DOMINIO": [], "EMAIL": []},
    # Hosts that are infrastructure, never a person: skipped by rules and dictionary.
    "whitelist": ["github.com", "githubusercontent.com", "shields.io", "anthropic.com",
                  "claude.ai", "pypi.org", "npmjs.com", "localhost", "example.com",
                  "example.org", "example.net", "w3.org", "schema.org", "wordpress.org",
                  "prestashop.com", "google.com", "gstatic.com", "googleapis.com", "x.com",
                  "twitter.com", "youtube.com", "apple.com", "microsoft.com", "mozilla.org",
                  "cloudflare.com", "amazonaws.com", "stackoverflow.com", "wikipedia.org"],
    "placeholders": {"style": "[UPPER_N]", "map_ttl_days": 30},
    # Annotated corpora: the public one ships with the plugin; the private one
    # (real project documents) lives OUTSIDE any repository, never in git.
    "corpus": {"private_dir": "~/.claude/fable-director/anonymizer-corpus"},
    "guard": "warn",
    "log": "counts",
}

GLOBAL_FILE = Path(".claude") / "fable-director" / "anonymizer.json"
PROJECT_FILE = ".fd-anonymizer.json"


class ConfigError(ValueError):
    pass


def _read(path: Path) -> dict:
    try:
        d = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path}: invalid JSON ({e})") from None
    if not isinstance(d, dict):
        raise ConfigError(f"{path}: top level must be an object")
    return d


def _merge(base: dict, over: dict) -> dict:
    for k, v in over.items():
        if k == "dictionary" and isinstance(v, dict):
            d = base.setdefault("dictionary", {})
            for cat, items in v.items():
                if isinstance(items, list):
                    d[cat] = list(dict.fromkeys(d.get(cat, []) + items))
                else:
                    d[cat] = items
        elif k == "whitelist" and isinstance(v, list):
            base["whitelist"] = list(dict.fromkeys(base.get("whitelist", []) + v))
        elif isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def find_project_file(cwd: Optional[Path] = None, home: Optional[Path] = None) -> Optional[Path]:
    cur = Path(cwd or Path.cwd()).resolve()
    home = Path(home or Path.home()).resolve()
    while True:
        cand = cur / PROJECT_FILE
        if cand.is_file():
            return cand
        if (cur / ".git").exists() or cur == home or cur.parent == cur:
            return None
        cur = cur.parent


def load(cwd: Optional[Path] = None, home: Optional[Path] = None) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    home = Path(home or Path.home())
    sources = []
    g = home / GLOBAL_FILE
    if g.is_file():
        _merge(cfg, _read(g))
        sources.append(str(g))
    p = find_project_file(cwd, home)
    if p:
        _merge(cfg, _read(p))
        sources.append(str(p))
    for extra in cfg["dictionary"].get("files", []):
        ep = Path(extra)
        if not ep.is_absolute() and p:
            ep = p.parent / ep
        if ep.is_file() and ep != p:
            _merge(cfg, _read(ep))
            sources.append(str(ep))
    # Legacy location: dictionary.whitelist inside the dictionary block.
    wl = cfg["dictionary"].pop("whitelist", None)
    if wl:
        cfg["whitelist"] = list(dict.fromkeys(cfg["whitelist"] + list(wl)))
    cfg["_sources"] = sources
    return cfg
