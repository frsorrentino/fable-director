#!/usr/bin/env python3
"""Anonymizer CLI — pseudonymise text before it leaves the machine.

Values stay on the machine in a placeholder map; the text carries stable
[CAT_N] placeholders; `restore` puts the values back into the answer.
Phase A: rules (it), columns (tabular exports), dictionary (per project).

Uso:
  anonymizer.py scan    FILE... [--json] [--engines columns,rules,dictionary]
  anonymizer.py redact  FILE | --stdin [--map NAME] [--out FILE] [--engines ...]
  anonymizer.py restore FILE | --stdin --map NAME [--out FILE]
  anonymizer.py test    [--corpus DIR]... [--json] [--strict] [--show-misses]
  anonymizer.py status  [--purge]

scan     counts per category and engine; never writes anything.
redact   replaces every span with [CAT_N]; the map ~/.claude/fable-director/
         anonymizer/maps/NAME.json (0600) is created or extended. Without
         --out the text goes to stdout and the STATUS lines to stderr.
restore  the inverse; unknown placeholders are kept and counted.
test     scores the engine on annotated corpora: the public synthetic one
         shipped with the plugin plus corpus.private_dir (default
         ~/.claude/fable-director/anonymizer-corpus, never inside a repo)
         when present, or the --corpus dirs given. --strict exits 1 when the
         formatted categories miss the thresholds (recall >= 0.99, precision
         >= 0.90). PERSONA/ORG are informative until the NER (phase C).
status   effective config and its sources, engines, header profiles
         (conflicts flagged), dictionary sizes, maps with age; --purge deletes
         maps older than placeholders.map_ttl_days.

Output sempre:
  STATUS: ok | error | unavailable
  OUTPUT: <path | ->
  DETAIL: <breve>
No value ever reaches stdout except the redacted/restored text itself and
`test --show-misses` (explicit, for reviewing annotations).
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))

from anonymizer import __version__, config as _config, corpus as _corpus, engine  # noqa: E402
from anonymizer.columns import build_headers  # noqa: E402
from anonymizer.pmap import PlaceholderMap, map_path  # noqa: E402

PUBLIC_CORPUS = PLUGIN / "anonymizer" / "corpus-public"


def private_corpus(cfg) -> Path:
    return Path(os.path.expanduser(cfg.get("corpus", {}).get("private_dir", "~/.claude/fable-director/anonymizer-corpus")))

USAGE = __doc__.split("Uso:")[1].split("\n\n")[0].strip()


class Fail(Exception):
    def __init__(self, detail, status="error"):
        super().__init__(detail)
        self.status = status


def emit(status, output, detail, stream=None):
    f = stream or sys.stdout
    print(f"STATUS: {status}", file=f)
    print(f"OUTPUT: {output}", file=f)
    print(f"DETAIL: {detail}", file=f)


def parse(argv):
    """Tiny option parser: --flag, --key VALUE (repeatable), positionals."""
    opts, pos = {}, []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            key = a[2:]
            if key in ("json", "strict", "show-misses", "stdin", "purge"):
                opts[key] = True
            else:
                if i + 1 >= len(argv):
                    raise Fail(f"--{key} requires a value")
                opts.setdefault(key, []).append(argv[i + 1])
                i += 1
        else:
            pos.append(a)
        i += 1
    return opts, pos


def read_text(path):
    p = Path(path)
    if not p.is_file():
        raise Fail(f"not a file: {p}")
    raw = p.read_bytes()
    if b"\x00" in raw[:8192]:
        raise Fail(f"not text: {p.name}")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("latin-1")
        except UnicodeDecodeError:
            raise Fail(f"not text: {p.name}") from None


def engines_opt(opts):
    e = opts.get("engines")
    return [x.strip() for x in e[-1].split(",") if x.strip()] if e else None


def load_map(name, must_exist=False):
    path = map_path(name)
    if path.is_file():
        try:
            return PlaceholderMap.load(path), path
        except ValueError as e:
            raise Fail(f"map {path.name}: {e}") from None
    if must_exist:
        raise Fail(f"map not found: {path}")
    return PlaceholderMap(), path


def input_text(opts, pos):
    if opts.get("stdin"):
        return sys.stdin.read(), "-"
    if not pos:
        raise Fail("FILE or --stdin required")
    return read_text(pos[0]), pos[0]


def fmt_counts(rep):
    c = rep["counts"]
    body = ", ".join(f"{k} {v}" for k, v in sorted(c.items())) or "nothing"
    eng = "/".join(f"{k}:{v}" for k, v in rep["by_engine"].items())
    extra = "; columns skipped (multi-line cells)" if rep.get("columns_skipped") else ""
    return f"{body} [{eng}] {rep['ms']} ms{extra}"


# ---------------------------------------------------------------- commands
def cmd_scan(opts, pos, cfg):
    if not pos and not opts.get("stdin"):
        raise Fail("scan needs FILE... or --stdin")
    reports = []
    if opts.get("stdin"):
        rep = engine.scan(sys.stdin.read(), cfg, engines_opt(opts))
        rep["file"] = "-"
        reports.append(rep)
    for p in pos:
        rep = engine.scan(read_text(p), cfg, engines_opt(opts))
        rep["file"] = Path(p).name
        reports.append(rep)
    if opts.get("json"):
        print(json.dumps(reports, ensure_ascii=False, indent=1))
    else:
        for rep in reports:
            print(f"{rep['file']}: {fmt_counts(rep)}")
    total = {}
    for rep in reports:
        for k, v in rep["counts"].items():
            total[k] = total.get(k, 0) + v
    emit("ok", "-", f"{len(reports)} file, " + (", ".join(f"{k} {v}" for k, v in sorted(total.items())) or "nothing found"))


def cmd_redact(opts, pos, cfg):
    text, src = input_text(opts, pos)
    name = (opts.get("map") or ["default"])[-1]
    pm, path = load_map(name)
    out, rep = engine.redact(text, pm, cfg, engines_opt(opts))
    pm.save(path)
    dest = (opts.get("out") or [None])[-1]
    detail = f"{fmt_counts(rep)}; map {path.name}: {sum(pm.counts().values())} values ({rep['placeholders_new']} new)"
    if dest:
        Path(dest).write_text(out, encoding="utf-8")
        emit("ok", dest, detail)
    else:
        sys.stdout.write(out)
        sys.stdout.flush()
        emit("ok", "-", detail, stream=sys.stderr)


def cmd_restore(opts, pos, cfg):
    text, src = input_text(opts, pos)
    if not opts.get("map"):
        raise Fail("restore requires --map NAME")
    pm, path = load_map(opts["map"][-1], must_exist=True)
    out, info = engine.restore(text, pm)
    dest = (opts.get("out") or [None])[-1]
    detail = f"restored {info['restored']}, unknown {info['unknown']} (map {path.name})"
    if dest:
        Path(dest).write_text(out, encoding="utf-8")
        emit("ok", dest, detail)
    else:
        sys.stdout.write(out)
        sys.stdout.flush()
        emit("ok", "-", detail, stream=sys.stderr)


def cmd_test(opts, pos, cfg):
    import copy
    dirs = [Path(d) for d in opts.get("corpus", [])]
    labels = []
    priv = private_corpus(cfg)
    if not dirs:
        dirs.append(PUBLIC_CORPUS)
        if (priv / "docs").is_dir():
            dirs.append(priv)
    results = []
    all_pass = True
    for d in dirs:
        if not (d / "docs").is_dir():
            raise Fail(f"corpus without docs/: {d}")
        docs = _corpus.load(d)
        if not docs:
            raise Fail(f"empty corpus: {d}")
        bad = _corpus.validate(docs)
        if bad:
            raise Fail(f"annotations do not match text in {d.name}: {', '.join(bad[:5])}")
        c = _config._merge(copy.deepcopy(cfg), _corpus.config_override(d))
        m = _corpus.evaluate(docs, c, engines_opt(opts))
        m["roundtrip_failed"] = _corpus.roundtrip(docs, c, engines_opt(opts))
        label = "public" if d == PUBLIC_CORPUS else ("private" if d == priv else d.name)
        m["corpus"] = label
        labels.append(f"{label} {len(docs)} docs")
        ok = m["formatted"]["passes"] and not m["roundtrip_failed"]
        all_pass = all_pass and ok
        if not opts.get("show-misses"):
            m["misses"] = {k: len(v) for k, v in m["misses"].items()}
        results.append(m)
    if opts.get("json"):
        print(json.dumps(results, ensure_ascii=False, indent=1))
    else:
        for m in results:
            f = m["formatted"]
            print(f"== {m['corpus']}: {m['docs']} docs; formatted categories precision {f['precision']} recall {f['recall']} "
                  f"({'PASS' if f['passes'] else 'BELOW THRESHOLD'}); round-trip {'ok' if not m['roundtrip_failed'] else 'FAILED ' + ','.join(m['roundtrip_failed'])}")
            for cat, c in m["per_category"].items():
                flag = " (informative)" if c["informative"] else ""
                print(f"   {cat:13} tp {c['tp']:4} fp {c['fp']:3} fn {c['fn']:3}  p {c['precision']:.3f} r {c['recall']:.3f}{flag}")
            if opts.get("show-misses"):
                for did, ms in m["misses"].items():
                    for x in ms:
                        print(f"   miss {did}: {x['kind']} {x['cat']} @{x['start']}-{x['end']}")
    status = "ok" if all_pass or not opts.get("strict") else "error"
    emit(status, "-", "; ".join(labels) + ("" if all_pass else "; thresholds NOT met"))
    return 0 if status == "ok" else 1


def cmd_status(opts, pos, cfg):
    ttl = int(cfg["placeholders"].get("map_ttl_days", 30))
    print(f"anonymizer {__version__}; enabled: {cfg['enabled']}; engines: {', '.join(cfg['engines'])}; packs: {', '.join(cfg['packs'])}")
    print("config sources: " + (", ".join(cfg.get("_sources") or []) or "defaults only"))
    print(f"rules: tel_requires_context={cfg['rules'].get('tel_requires_context')} url={cfg['rules'].get('url')}")
    lookup, conflicts = build_headers(cfg)
    print(f"columns: {len(lookup)} headers from profiles {', '.join(cfg['columns']['profiles'])}"
          + (f" + {len(cfg['columns']['extra'])} extra" if cfg['columns'].get('extra') else ""))
    for c in conflicts:
        print(f"   CONFLICT {c}")
    d = cfg["dictionary"]
    print("dictionary: " + ", ".join(f"{k} {len(v)}" for k, v in d.items() if isinstance(v, list) and k != "files") + f"; whitelist {len(cfg['whitelist'])}")
    print(f"guard: {cfg['guard']}; placeholders: {cfg['placeholders']['style']}, ttl {ttl} days")
    maps_dir = map_path("x").parent
    now = datetime.now(timezone.utc)
    expired, kept = [], []
    if maps_dir.is_dir():
        for f in sorted(maps_dir.glob("*.json")):
            try:
                pm = PlaceholderMap.load(f)
                age = (now - datetime.fromisoformat(pm.created)).days
            except (ValueError, TypeError):
                print(f"   map {f.name}: unreadable")
                continue
            old = age > ttl
            (expired if old else kept).append(f)
            print(f"   map {f.stem}: {sum(pm.counts().values())} values, {age} days" + (" — expired" if old else ""))
    if opts.get("purge"):
        for f in expired:
            f.unlink()
    detail = f"{len(kept)} maps" + (f", {len(expired)} expired" + (" purged" if opts.get("purge") else "") if expired else "")
    private = "present" if (private_corpus(cfg) / "docs").is_dir() else "absent"
    emit("ok", "-", detail + f"; private corpus {private}" + ("; header conflicts!" if conflicts else ""))


COMMANDS = {"scan": cmd_scan, "redact": cmd_redact, "restore": cmd_restore, "test": cmd_test, "status": cmd_status}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help") or argv[0] not in COMMANDS:
        print("usage:\n  " + USAGE.replace("\n", "\n  "))
        emit("error", "-", "unknown command" if argv and argv[0] not in ("-h", "--help") else "no command")
        return 2
    cmd = argv[0]
    try:
        opts, pos = parse(argv[1:])
        cfg = _config.load()
        rc = COMMANDS[cmd](opts, pos, cfg)
        return rc or 0
    except Fail as e:
        stream = sys.stderr if (cmd in ("redact", "restore") and not opts_has_out(argv)) else sys.stdout
        emit(e.status, "-", str(e), stream=stream)
        return 1
    except _config.ConfigError as e:
        emit("error", "-", f"config: {e}")
        return 1


def opts_has_out(argv):
    return "--out" in argv


if __name__ == "__main__":
    sys.exit(main())
