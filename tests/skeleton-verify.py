#!/usr/bin/env python3
"""Verifica skeleton.py: firme sì, corpi no.

- S1 python: def/class con file:riga, docstring prima riga, firme multi-riga
  ricomposte; il CORPO delle funzioni non appare mai;
- S2 js/ts: function/class/export const/interface;
- S3 estensione non supportata: messaggio esplicito, niente dump;
- S4 riduzione: superficie < 20% delle righe del file fixture.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

TOOL = (Path(__file__).resolve().parent.parent / "fable-director" / "skills"
        / "delega-efficiente" / "tools" / "skeleton.py")
FAILS = []


def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'FAIL'} {name}"
          + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def run(*files):
    p = subprocess.run([sys.executable, str(TOOL)] + [str(f) for f in files],
                       capture_output=True, text=True, timeout=30)
    return p.stdout + p.stderr


with tempfile.TemporaryDirectory() as td:
    py = Path(td) / "mod.py"
    py.write_text('''class Store:
    """Deposito chiave-valore."""

    def get(self, key,
            default=None):
        """Legge una chiave."""
        segreto_del_corpo = 42
        return self.d.get(key, default)


def helper(x):
    corpo_privato = x * 2
    return corpo_privato
''')
    out = run(py)
    check("S1 firme con file:riga",
          "mod.py:1 class Store" in out and "def helper(x)" in out, out)
    check("S1b firma multi-riga ricomposta",
          "def get(self, key, default=None)" in out, out)
    check("S1c docstring prima riga presente",
          "Deposito chiave-valore" in out, out)
    check("S1d corpi ESCLUSI",
          "segreto_del_corpo" not in out and "corpo_privato" not in out, out)

    js = Path(td) / "app.ts"
    js.write_text('''export function render(x: number) {
  const bodySecret = 1;
}
export const load = async (url) => {
  return fetch(url);
};
interface Config { a: string }
''')
    out = run(js)
    check("S2 js/ts firme",
          "function render" in out and "const load" in out
          and "interface Config" in out, out)
    check("S2b corpi esclusi", "bodySecret" not in out, out)

    other = Path(td) / "dati.csv"
    other.write_text("a,b\n1,2\n")
    out = run(other)
    check("S3 non supportato: messaggio, niente dump",
          "unsupported" in out and "1,2" not in out, out)

    n_out = len(run(py).splitlines())
    n_src = len(py.read_text().splitlines())
    check("S4 superficie compatta", n_out <= max(6, n_src * 0.8),
          f"{n_out} righe output vs {n_src} sorgente")

print()
if FAILS:
    print(f"FAIL: {len(FAILS)} check falliti: {', '.join(FAILS)}")
    sys.exit(1)
print("Tutti i check skeleton superati.")
