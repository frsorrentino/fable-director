#!/usr/bin/env python3
"""API surface di file sorgente: firme con file:riga, niente corpi.

Per i contratti di delega (skill delega-efficiente): quando l'executor deve
CONSUMARE un'API senza modificarla, il contratto allega questa superficie al
posto del file intero — l'input fresco per agente è la voce dominante del
costo di delega, e le firme bastano a chiamare. Zero token modello, zero
dipendenze (idea dal `graft skeleton` di NanoNets/Graft, MIT; qui ridotta a
regex: niente indice da costruire né tenere fresco).

Uso: skeleton.py FILE [FILE...]     (py, js/ts/mjs; altro → riga unsupported)
"""
import re
import sys
from pathlib import Path

PY_SIG = re.compile(r"^(\s*)(?:async\s+)?(def|class)\s+\w")
JS_SIG = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?"
    r"(?:function\s+\w+|class\s+\w+|const\s+\w+\s*=\s*(?:async\s*)?\(|"
    r"interface\s+\w+|type\s+\w+\s*=)")


def py_skeleton(path, lines):
    out = []
    i = 0
    while i < len(lines):
        m = PY_SIG.match(lines[i])
        if m:
            sig = lines[i].rstrip()
            j = i
            # firma multi-riga: accumula fino alla riga che chiude con ':'
            while not sig.rstrip().endswith(":") and j + 1 < len(lines):
                j += 1
                sig += " " + lines[j].strip()
            out.append(f"{path}:{i + 1} {sig.rstrip(':').strip()}")
            # prima riga di docstring, se c'è: è parte del contratto d'uso
            k = j + 1
            if k < len(lines):
                ds = lines[k].strip()
                if ds.startswith(('"""', "'''")):
                    doc = ds.strip("\"'").strip()
                    if doc:
                        out.append(f"{' ' * 4}# {doc[:100]}")
            i = j + 1
        else:
            i += 1
    return out


def js_skeleton(path, lines):
    return [f"{path}:{n + 1} {line.strip().rstrip('{').strip()}"
            for n, line in enumerate(lines) if JS_SIG.match(line)]


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__.strip())
    for f in sys.argv[1:]:
        p = Path(f)
        try:
            lines = p.read_text(errors="replace").splitlines()
        except OSError as e:
            print(f"{f}: unreadable ({e.__class__.__name__})")
            continue
        if p.suffix == ".py":
            rows = py_skeleton(f, lines)
        elif p.suffix in (".js", ".ts", ".mjs", ".tsx", ".jsx"):
            rows = js_skeleton(f, lines)
        else:
            print(f"{f}: unsupported extension — attach the relevant "
                  f"excerpt instead")
            continue
        print(f"# {f} — {len(rows)} signatures, {len(lines)} lines in file")
        print("\n".join(rows) if rows else "(no signatures found)")


if __name__ == "__main__":
    main()
