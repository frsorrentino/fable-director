# Billing Policy + Gemini Image Route — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce "free-tier only by default" deterministically across the cross-family stack and add a Gemini image-generation route (`type: "image"`) as the first paid, consent-gated provider.

**Architecture:** A machine-readable `billing` field per provider (fail-closed: absent = paid) is read by a small duplicated helper in `external-exec.py` and `cross-verify.py` (both are standalone stdlib-only scripts by design — 5-line duplication beats a shared module). A `--paid-ok` flag is the only way to run a paid provider. Proposal surfaces (`route-hint.py`, `kernel.md`, `SKILL.md`, doctor, `fd-status.py`) list/propose only `billing: "free"` entries. The image route is a third provider type in `external-exec.py` calling Gemini's native `generateContent` endpoint and writing bytes to a mandatory `--out`.

**Tech Stack:** Python 3 stdlib only (house rule: zero dependencies). Tests are self-contained scripts under `tests/` run by `release.sh` (`for t in tests/*.py`), throwaway-HOME pattern, exit 0 = green.

**Spec:** `docs/superpowers/specs/2026-07-22-billing-policy-image-route-design.md`

## Global Constraints

- Fail-closed: `billing` absent = treated as `"paid"` — never proposed, never run without `--paid-ok`.
- `--paid-ok` contract: passed ONLY after explicit user consent in the current conversation (docstring must say this).
- Binary image output never goes to stdout; `--out` is mandatory for `type: "image"`.
- API key for image route goes in the `x-goog-api-key` header, never in the query string.
- No new dependencies; scripts stay standalone (each runs via `python3 path/script.py`).
- Every suite in `tests/*.py` plus `tests/transcript-contract/run.py` must be green BEFORE each commit (house rule).
- All output strings grep-able and in English (existing script convention); comments may be Italian (existing convention).
- Release only at the end: bump + CHANGELOG + README What's new (plain title, max 5 entries) BEFORE `bash release.sh 1.24.0`.
- Working directory for all commands: `/home/franz/Desktop/workspaces/fable-director/fable-director-marketplace`.

---

### Task 1: Billing guard in `external-exec.py`

**Files:**
- Modify: `fable-director/scripts/external-exec.py` (docstring ~line 44-55; `parse_args` line 382-404; `main` after provider resolution line 558-561; `base_log` line 637)
- Test: `tests/external-exec-verify.py` (fixture `setup()` line 62-96; new checks after E10)

**Interfaces:**
- Produces: `billing_of(prov) -> "free"|"paid"` (module-level function, reused by Task 2's doctor changes); `flags["--paid-ok"]: bool`; `unavailable(...)` message containing the literal substrings `is billed` and `--paid-ok`; telemetry payload key `"billing"` on every run and `"check": "paid-refused"` on refusal.

- [ ] **Step 1: Update existing fixture so it survives fail-closed**

In `tests/external-exec-verify.py` `setup()`, add `"billing": "free"` to both existing stub providers and add two new ones. The `config` dict becomes:

```python
    config = {
        "default": "stub",
        "providers": {
            "stub": {
                "type": "cli",
                "command": [py, str(stub), "run", "{model}", "{effort}",
                            "--out", "{output_file}"],
                "resume_command": [py, str(stub), "resume", "{effort}",
                                   "--out", "{output_file}"],
                "schema_args": ["--schema", "{schema_file}"],
                "model": "stub-model-1",
                "effort": "high",
                "timeout": 2,
                "billing": "free",
            },
            "stub-plain": {
                "type": "cli",
                "command": [py, str(stub), "run", "--out", "{output_file}"],
                "model": "stub-plain-model",
                "billing": "free",
            },
            "stub-paid": {
                "type": "cli",
                "command": [py, str(stub), "run", "--out", "{output_file}"],
                "model": "stub-paid-model",
                "billing": "paid",
                "cost_note": "~$9.99/call",
            },
            "stub-nobilling": {
                "type": "cli",
                "command": [py, str(stub), "run", "--out", "{output_file}"],
                "model": "stub-nobilling-model",
            },
        },
    }
```

- [ ] **Step 2: Write the failing tests (E11-E14)**

In `tests/external-exec-verify.py` `main()`, after the E10 block and before the summary, add:

```python
    # E11 — provider paid senza --paid-ok → unavailable, mai eseguito.
    r = run(home, proj, ["--spec", "hi", "--provider", "stub-paid"])
    check("E11 paid provider without --paid-ok is refused",
          r.returncode == 1 and field(r.stdout, "STATUS") == "unavailable"
          and "is billed" in r.stdout and "--paid-ok" in r.stdout
          and "$9.99" in r.stdout, r.stdout + r.stderr)

    # E12 — stesso provider con --paid-ok → esegue.
    r = run(home, proj, ["--spec", "hi", "--provider", "stub-paid",
                         "--paid-ok"])
    check("E12 paid provider with --paid-ok runs",
          r.returncode == 0 and field(r.stdout, "STATUS") == "ok",
          r.stdout + r.stderr)

    # E13 — billing assente = paid (fail-closed).
    r = run(home, proj, ["--spec", "hi", "--provider", "stub-nobilling"])
    check("E13 missing billing field is fail-closed (treated as paid)",
          r.returncode == 1 and field(r.stdout, "STATUS") == "unavailable"
          and "is billed" in r.stdout, r.stdout + r.stderr)

    # E14 — free provider resta invariato anche con --paid-ok presente.
    r = run(home, proj, ["--spec", "hi", "--paid-ok"])
    check("E14 --paid-ok on a free provider is a no-op",
          r.returncode == 0 and field(r.stdout, "STATUS") == "ok",
          r.stdout + r.stderr)
```

- [ ] **Step 3: Run the suite to verify E11/E13 fail**

Run: `python3 tests/external-exec-verify.py`
Expected: E11 and E13 FAIL (`unrecognized argument: --paid-ok` for E12/E14; E11/E13 run fine today because no guard exists). Exit != 0.

- [ ] **Step 4: Implement the guard**

In `fable-director/scripts/external-exec.py`:

(a) `parse_args` — add to the `flags` dict:

```python
    flags = {"--schema-json": False, "--allow-truncate": False,
             "--doctor": False, "--ping": False, "--resume-last": False,
             "--paid-ok": False}
```

(b) Module-level helper, place right after `unavailable()` (~line 126):

```python
def billing_of(prov):
    """Fail-closed: billing non dichiarato = paid — mai proposto né
    eseguito senza consenso esplicito. La policy vive nel campo del
    config, mai in euristiche sul nome del provider."""
    return "free" if prov.get("billing") == "free" else "paid"
```

(c) In `main()`, immediately after `if not prov: unavailable(...)` (line 560-561), insert:

```python
    if billing_of(prov) != "free" and not flags["--paid-ok"]:
        log_exec({"provider": name, "model": prov.get("model", "?"),
                  "billing": "paid", "ok": False, "check": "paid-refused"})
        unavailable(
            f"provider '{name}' is billed"
            + (f" ({prov['cost_note']})" if prov.get("cost_note") else "")
            + " — requires explicit user consent in this conversation; "
              "re-run with --paid-ok ONLY after the user agreed")
```

(d) `base_log` (line 637) gains the billing class:

```python
    base_log = {"provider": name, "model": prov["model"],
                "billing": billing_of(prov),
                "type": opts.get("--type"), "resume": flags["--resume-last"],
                "chars_in": len(user_msg)}
```

(e) Docstring: in the `Uso:` block add `[--paid-ok]` to the first form, and append one paragraph after the `--resume-last` paragraph:

```
--paid-ok: obbligatorio per provider con "billing" diverso da "free" nel
config (campo assente = paid, fail-closed). Va passato SOLO dopo consenso
esplicito dell'utente nella conversazione corrente — mai preventivamente,
mai "per efficienza". I provider free ignorano il flag.
```

- [ ] **Step 5: Run the suite to verify all green**

Run: `python3 tests/external-exec-verify.py`
Expected: all PASS (E1-E14), exit 0.

- [ ] **Step 6: Commit**

```bash
git add fable-director/scripts/external-exec.py tests/external-exec-verify.py
git commit -m "feat: billing guard in external-exec — paid providers require --paid-ok (fail-closed)"
```

---

### Task 2: Billing-aware doctor in `external-exec.py`

**Files:**
- Modify: `fable-director/scripts/external-exec.py` (`doctor()` line 258-379; call site in `main()` line 537-538)
- Test: `tests/external-exec-verify.py` (new checks E15-E16)

**Interfaces:**
- Consumes: `billing_of(prov)` from Task 1.
- Produces: doctor line contains `billing: free` or `billing: PAID` or `billing UNDECLARED`; undeclared counts as a problem (doctor exit 1); `doctor(ping=False, paid_ok=False)` signature.

- [ ] **Step 1: Write the failing tests**

In `tests/external-exec-verify.py` `main()`, after E14 add (note: doctor needs no budget):

```python
    # E15 — doctor: billing dichiarato mostrato, UNDECLARED è un problema.
    r = run(home, proj, ["--doctor"])
    check("E15 doctor reports billing class per provider",
          "billing: free" in r.stdout and "billing: PAID" in r.stdout
          and "$9.99" in r.stdout, r.stdout + r.stderr)
    check("E16 doctor flags undeclared billing as a problem (exit 1)",
          r.returncode == 1 and "billing UNDECLARED" in r.stdout
          and "fail-closed" in r.stdout, r.stdout + r.stderr)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 tests/external-exec-verify.py`
Expected: E15 and E16 FAIL (no billing lines in doctor output today).

- [ ] **Step 3: Implement**

In `doctor()`, inside the provider loop, right after `checks = []` / `ok = True` (line 293-294), insert:

```python
        if "billing" not in prov:
            ok = False
            checks.append('billing UNDECLARED → treated as PAID '
                          '(fail-closed): add "billing": "free"|"paid" '
                          'to the config entry')
        elif billing_of(prov) == "free":
            checks.append("billing: free")
        else:
            checks.append("billing: PAID"
                          + (f" ({prov['cost_note']})"
                             if prov.get("cost_note") else ""))
```

Change the ping gate (line 330) from `if ping and ok:` to:

```python
        if ping and ok and billing_of(prov) != "free" and not paid_ok:
            checks.append("ping SKIPPED (billed provider — costs real "
                          "money; add --paid-ok to consent)")
        elif ping and ok:
```

Change the signature `def doctor(ping=False):` → `def doctor(ping=False, paid_ok=False):` and the call site in `main()`:

```python
    if flags["--doctor"]:
        doctor(ping=flags["--ping"], paid_ok=flags["--paid-ok"])
```

In the doctor's config-missing onboarding text (line 276-277), replace the sentence `Prefer paid models? Same config entries with your paid API key — the telemetry judges outcomes the same way.` with:

```
Prefer paid models? Same config entries with your paid API key and
"billing": "paid" — consent-gated (--paid-ok), never auto-proposed.
```

- [ ] **Step 4: Run the suite**

Run: `python3 tests/external-exec-verify.py`
Expected: all PASS (E1-E16), exit 0.

- [ ] **Step 5: Commit**

```bash
git add fable-director/scripts/external-exec.py tests/external-exec-verify.py
git commit -m "feat: doctor reports billing class, skips paid pings without --paid-ok"
```

---

### Task 3: Billing guard in `cross-verify.py`

**Files:**
- Modify: `fable-director/scripts/cross-verify.py` (`parse_args` line 235-255; `main` after provider resolution line 353-356; `log_verification` payload line 424-427; docstring `Uso` block ~line 32)
- Create: `tests/cross-verify-billing.py`

**Interfaces:**
- Consumes: nothing from other tasks (duplicated helper by design).
- Produces: same `billing_of(prov)` helper (identical 5 lines) in `cross-verify.py`; `--paid-ok` flag; verification payload key `"billing"`.

- [ ] **Step 1: Write the failing test file**

Create `tests/cross-verify-billing.py`:

```python
#!/usr/bin/env python3
"""Guardia billing di cross-verify.py (1.24.0).

HOME usa-e-getta + provider CLI stub che risponde col JSON verdetto:
  C1  provider free → esegue, STATUS ok
  C2  provider paid senza --paid-ok → unavailable, exit 1, mai eseguito
  C3  provider paid con --paid-ok → esegue
  C4  billing assente = paid (fail-closed)

Usage: python3 tests/cross-verify-billing.py   (exit 0 = all green)
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "fable-director" / "scripts" / "cross-verify.py"

passed, failed = [], []

STUB = '''#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
out = args[args.index("--out") + 1] if "--out" in args else None
sys.stdin.read()
text = json.dumps({"verdict": "supported", "reasoning": "stub"})
if out:
    open(out, "w").write(text)
else:
    print(text)
'''


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}"
          + ("" if ok else f"\\n      {evidence}"))


def setup():
    home = Path(tempfile.mkdtemp(prefix="fd-xvb-home-"))
    proj = Path(tempfile.mkdtemp(prefix="fd-xvb-proj-"))
    stub = home / "stub-cli.py"
    stub.write_text(STUB)
    py = sys.executable
    base = {"type": "cli",
            "command": [py, str(stub), "run", "--out", "{output_file}"],
            "model": "stub-v"}
    config = {"default": "vfree", "providers": {
        "vfree": {**base, "billing": "free"},
        "vpaid": {**base, "billing": "paid", "cost_note": "~$0.003/verify"},
        "vnobilling": dict(base),
    }}
    cfg_dir = home / ".claude" / "fable-director"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "cross-family.json").write_text(json.dumps(config))
    return home, proj


def run(home, proj, args):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--claim", "x"] + args,
        capture_output=True, env=env, cwd=proj, timeout=60,
        encoding="utf-8", errors="replace")


def field(stdout, key):
    m = re.search(rf"^{key}: (.*)$", stdout, re.MULTILINE)
    return m.group(1) if m else ""


def main():
    home, proj = setup()

    r = run(home, proj, ["--provider", "vfree"])
    check("C1 free provider verifies",
          r.returncode == 0 and field(r.stdout, "STATUS") == "ok",
          r.stdout + r.stderr)

    r = run(home, proj, ["--provider", "vpaid"])
    check("C2 paid provider without --paid-ok is refused",
          r.returncode == 1 and field(r.stdout, "STATUS") == "unavailable"
          and "is billed" in r.stdout and "--paid-ok" in r.stdout
          and "$0.003" in r.stdout, r.stdout + r.stderr)

    r = run(home, proj, ["--provider", "vpaid", "--paid-ok"])
    check("C3 paid provider with --paid-ok verifies",
          r.returncode == 0 and field(r.stdout, "STATUS") == "ok",
          r.stdout + r.stderr)

    r = run(home, proj, ["--provider", "vnobilling"])
    check("C4 missing billing field is fail-closed",
          r.returncode == 1 and field(r.stdout, "STATUS") == "unavailable"
          and "is billed" in r.stdout, r.stdout + r.stderr)

    print(f"\\n{len(passed)} passed, {len(failed)} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 tests/cross-verify-billing.py`
Expected: C2/C4 FAIL (no guard), C3 FAIL (`argomento non riconosciuto: --paid-ok`). Exit 1.

- [ ] **Step 3: Implement**

In `fable-director/scripts/cross-verify.py`:

(a) Helper after `unavailable()` (~line 165) — identical to Task 1's:

```python
def billing_of(prov):
    """Fail-closed: billing non dichiarato = paid — mai proposto né
    eseguito senza consenso esplicito. La policy vive nel campo del
    config, mai in euristiche sul nome del provider."""
    return "free" if prov.get("billing") == "free" else "paid"
```

(b) `parse_args` — add the flag key to `opts` and its branch:

```python
    opts = {"--claim": None, "--rubric": None, "--context-file": None,
            "--provider": None, "--timeout": None, "--type": None,
            "--allow-truncate": False, "--paid-ok": False}
```

and inside the loop, next to the `--allow-truncate` branch:

```python
        if argv[i] == "--paid-ok":
            opts["--paid-ok"] = True
            i += 1
```

(c) In `main()`, right after `if not prov: unavailable(...)` (line 355-356):

```python
    if billing_of(prov) != "free" and not opts["--paid-ok"]:
        unavailable(
            f"provider '{name}' is billed"
            + (f" ({prov['cost_note']})" if prov.get("cost_note") else "")
            + " — requires explicit user consent in this conversation; "
              "re-run with --paid-ok ONLY after the user agreed")
```

(d) `log_verification` payload (line 424) gains `"billing": billing_of(prov)`:

```python
    log_verification({"kind": "cross-family", "provider": name,
                      "model": prov["model"], "verdict": verdict,
                      "billing": billing_of(prov),
                      "type": opts.get("--type"),
                      "found": verdict == "refuted"})
```

(e) Docstring `Uso` block: add `[--paid-ok]` to the invocation line and one sentence: `--paid-ok: obbligatorio per provider "billing" != "free" (assente = paid, fail-closed); solo dopo consenso esplicito dell'utente.`

- [ ] **Step 4: Run both suites**

Run: `python3 tests/cross-verify-billing.py && python3 tests/external-exec-verify.py`
Expected: all PASS, exit 0.

- [ ] **Step 5: Commit**

```bash
git add fable-director/scripts/cross-verify.py tests/cross-verify-billing.py
git commit -m "feat: billing guard in cross-verify — paid verifiers require --paid-ok"
```

---

### Task 4: `route-hint.py` proposes only free providers

**Files:**
- Modify: `fable-director/scripts/route-hint.py` (`cardinality_candidate()` line 72-82)
- Test: `tests/route-hint-verify.py` (fixture config + new checks R15-R16)

**Interfaces:**
- Consumes: `cross-family.json` `providers.*.billing`.
- Produces: axis-4 hint line listing only free providers, with the literal text `free-tier provider:`.

- [ ] **Step 1: Update fixture and write failing tests**

In `tests/route-hint-verify.py`, find where the synthetic `cross-family.json` is written for R5 and add `"billing": "free"` to its provider entries (R5 asserts the hint appears — with fail-closed filtering it would vanish otherwise). Then add two checks at the end, following the file's existing helper conventions (it writes configs into the throwaway HOME and runs the hook with a prompt on stdin — reuse the same helpers used by R5/R6):

```python
    # R15 — mix free/paid: l'hint asse-4 elenca SOLO i free.
    write_xfam({"providers": {
        "gfree": {"model": "m", "billing": "free"},
        "gpaid": {"model": "m", "billing": "paid"},
        "gnone": {"model": "m"},
    }})
    out = run_hook("processa in batch tutti i file del progetto")
    check("R15 axis-4 hint lists only free providers",
          "gfree" in out and "gpaid" not in out and "gnone" not in out)

    # R16 — soli provider paid/undeclared: nessuna rotta free → silenzio.
    write_xfam({"providers": {
        "gpaid": {"model": "m", "billing": "paid"},
        "gnone": {"model": "m"},
    }})
    out = run_hook("processa in batch tutti i file del progetto")
    check("R16 no free providers → no axis-4 hint", "external-exec" not in out)
```

(Adapt `write_xfam`/`run_hook` to the actual helper names in the file — the suite already has equivalents for R5/R6; if they differ, use those. Do not invent new subprocess plumbing.)

- [ ] **Step 2: Run to verify R15/R16 fail**

Run: `python3 tests/route-hint-verify.py`
Expected: R15 FAIL (paid names listed today), R16 FAIL (hint emitted). Exit != 0.

- [ ] **Step 3: Implement**

Replace `cardinality_candidate()` in `fable-director/scripts/route-hint.py`:

```python
def cardinality_candidate(prompt_lower):
    m = CARDINALITY.search(prompt_lower)
    if not m:
        return None
    providers = load_json(base_dir() / "cross-family.json").get("providers", {})
    # Solo i free: i provider paid non vengono MAI proposti d'ufficio
    # (fail-closed: billing assente = paid). Policy 2026-07-22.
    free = sorted(n for n, p in providers.items()
                  if isinstance(p, dict) and p.get("billing") == "free")
    if not free:
        return None
    names = ", ".join(free)
    return ("external-exec",
            f'- external-exec asse 4 (segnale cardinalità "{m.group(0)}") — '
            f"free-tier provider: {names}; solo item non quality-sensitive, "
            f"pre-budget obbligatorio")
```

- [ ] **Step 4: Run the suite**

Run: `python3 tests/route-hint-verify.py`
Expected: all PASS (R1-R16), exit 0.

- [ ] **Step 5: Commit**

```bash
git add fable-director/scripts/route-hint.py tests/route-hint-verify.py
git commit -m "feat: route-hint lists only free-tier providers in axis-4 hints"
```

---

### Task 5: Image route (`type: "image"`) in `external-exec.py`

**Files:**
- Modify: `fable-director/scripts/external-exec.py` (new `call_image()` after `call_http()` line 429; validation + dispatch + output branches in `main()`; docstring)
- Create: `tests/image-route-verify.py`

**Interfaces:**
- Consumes: `billing_of`, `--paid-ok` (Task 1), `check_out_perimeter` (existing).
- Produces: `call_image(prov, name, api_key, prompt, timeout) -> (img_bytes|None, mime|None, text|None)`; output contract `CHECK: image`, `OUTPUT: <path>`, `DETAIL: <mime>, <N> bytes`; telemetry `"check": "image", "bytes_out": N`.

- [ ] **Step 1: Write the failing test file**

Create `tests/image-route-verify.py`:

```python
#!/usr/bin/env python3
"""Rotta immagini di external-exec.py (type "image", 1.24.0).

Mini server HTTP locale che imita generateContent di Gemini:
  I1  risposta con inlineData → bytes scritti su --out, CHECK image
  I2  --out mancante → errore esplicito (binario mai su stdout)
  I3  risposta solo testo (es. rifiuto safety) → error, testo citato
  I4  429 con "limit: 0" → detail billing dedicato, non quota generica
  I5  flag incompatibili (--schema-json) → errore esplicito
  I6  chiave API nell'header x-goog-api-key, MAI in query string

Usage: python3 tests/image-route-verify.py   (exit 0 = all green)
"""
import base64
import hashlib
import http.server
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "fable-director" / "scripts" / "external-exec.py"

PNG_BYTES = b"\\x89PNG\\r\\n\\x1a\\nfake-image-payload"
PNG_B64 = base64.b64encode(PNG_BYTES).decode()

passed, failed = [], []
seen = {"path": "", "key_header": ""}


class Handler(http.server.BaseHTTPRequestHandler):
    mode = "ok"

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        seen["path"] = self.path
        seen["key_header"] = self.headers.get("x-goog-api-key", "")
        if Handler.mode == "billing429":
            body = json.dumps({"error": {"code": 429, "message":
                "Quota exceeded for metric ... limit: 0, model: img"}}).encode()
            self.send_response(429)
        elif Handler.mode == "textonly":
            body = json.dumps({"candidates": [{"content": {"parts": [
                {"text": "cannot draw that, sorry"}]}}]}).encode()
            self.send_response(200)
        else:
            body = json.dumps({"candidates": [{"content": {"parts": [
                {"inlineData": {"mimeType": "image/png",
                                "data": PNG_B64}}]}}]}).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}"
          + ("" if ok else f"\\n      {evidence}"))


def slug(cwd):
    s = str(cwd).replace("\\\\", "/")
    return (re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
            + "-" + hashlib.sha256(s.encode()).hexdigest()[:8])


def setup(port):
    home = Path(tempfile.mkdtemp(prefix="fd-img-home-"))
    proj = Path(tempfile.mkdtemp(prefix="fd-img-proj-"))
    cfg_dir = home / ".claude" / "fable-director"
    (cfg_dir / "budgets").mkdir(parents=True)
    config = {"default": "img", "providers": {"img": {
        "type": "image",
        "base_url": f"http://127.0.0.1:{port}/v1beta",
        "model": "img-model",
        "api_key": "test-key-123",
        "billing": "free",
    }}}
    (cfg_dir / "cross-family.json").write_text(json.dumps(config))
    (cfg_dir / "budgets" / f"{slug(proj)}.json").write_text(json.dumps({
        "status": "open",
        "declared_at": datetime.now(timezone.utc).isoformat(),
    }))
    return home, proj


def run(home, proj, args):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    return subprocess.run([sys.executable, str(SCRIPT)] + args,
                          capture_output=True, env=env, cwd=proj, timeout=60,
                          encoding="utf-8", errors="replace")


def field(stdout, key):
    m = re.search(rf"^{key}: (.*)$", stdout, re.MULTILINE)
    return m.group(1) if m else ""


def main():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    home, proj = setup(srv.server_address[1])
    outpng = proj / "out.png"

    # I1 — happy path: bytes su --out.
    Handler.mode = "ok"
    r = run(home, proj, ["--spec", "a red circle", "--out", str(outpng)])
    check("I1 inlineData bytes written to --out",
          r.returncode == 0 and field(r.stdout, "CHECK") == "image"
          and outpng.read_bytes() == PNG_BYTES
          and "image/png" in field(r.stdout, "DETAIL"),
          r.stdout + r.stderr)

    # I6 — chiave nell'header, mai in query (finirebbe nei log).
    check("I6 API key in x-goog-api-key header, not in URL",
          seen["key_header"] == "test-key-123" and "key=" not in seen["path"]
          and seen["path"].endswith("/models/img-model:generateContent"),
          json.dumps(seen))

    # I2 — --out mancante.
    r = run(home, proj, ["--spec", "a red circle"])
    check("I2 missing --out is a loud error",
          r.returncode == 1 and field(r.stdout, "STATUS") == "error"
          and "--out" in r.stdout, r.stdout + r.stderr)

    # I3 — risposta solo testo.
    Handler.mode = "textonly"
    r = run(home, proj, ["--spec", "x", "--out", str(outpng)])
    check("I3 text-only response is an error citing the text",
          r.returncode == 1 and field(r.stdout, "STATUS") == "error"
          and "cannot draw that" in r.stdout, r.stdout + r.stderr)

    # I4 — 429 limit: 0 → messaggio billing, non quota generica.
    Handler.mode = "billing429"
    r = run(home, proj, ["--spec", "x", "--out", str(outpng)])
    check("I4 429 limit:0 maps to billing-not-enabled detail",
          r.returncode == 1 and field(r.stdout, "STATUS") == "unavailable"
          and "billing enabled" in r.stdout, r.stdout + r.stderr)

    # I5 — flag incompatibili.
    Handler.mode = "ok"
    r = run(home, proj, ["--spec", "x", "--out", str(outpng),
                         "--schema-json"])
    check("I5 --schema-json on image provider is a loud error",
          r.returncode == 1 and field(r.stdout, "STATUS") == "error"
          and "image" in r.stdout, r.stdout + r.stderr)

    srv.shutdown()
    print(f"\\n{len(passed)} passed, {len(failed)} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 tests/image-route-verify.py`
Expected: I1 fails (script treats `type: "image"` as HTTP-chat and calls `/chat/completions` → 404/None). Exit 1.

- [ ] **Step 3: Implement**

In `fable-director/scripts/external-exec.py`:

(a) New function after `call_http()` (~line 429):

```python
def call_image(prov, name, api_key, prompt, timeout):
    """Provider "type": "image" (famiglia gemini-*-image): endpoint nativo
    generateContent, risposta inlineData base64. Chiave SOLO nell'header
    x-goog-api-key: in query string finirebbe nei log. Ritorna
    (bytes, mime, None) oppure (None, None, testo-del-provider)."""
    import base64
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
    url = (prov["base_url"].rstrip("/")
           + f"/models/{prov['model']}:generateContent")
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json", "x-goog-api-key": api_key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode(errors="replace"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        if e.code == 429 and "limit: 0" in detail:
            unavailable(
                f"image models need billing enabled on the Google project "
                f"(free tier limit is 0) — not a transient quota error "
                f"[{name}]")
        unavailable(f"HTTP {e.code} from {name} (rate limit / endpoint changed?)")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        unavailable(f"network/timeout towards {name}: {e}")
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError):
        return None, None, None
    for p in parts:
        blob = p.get("inlineData") or p.get("inline_data") or {}
        if blob.get("data"):
            mime = blob.get("mimeType") or blob.get("mime_type") or "image/png"
            return base64.b64decode(blob["data"]), mime, None
    text = " ".join(str(p.get("text", "")) for p in parts).strip()
    return None, None, (text[:300] or None)
```

(b) In `main()`, right after the billing guard from Task 1, insert the image validation block:

```python
    is_image = prov.get("type") == "image"
    if is_image:
        for bad, val in (("--schema-json", flags["--schema-json"]),
                         ("--schema-file", opts["--schema-file"]),
                         ("--effort", opts["--effort"]),
                         ("--resume-last", flags["--resume-last"]),
                         ("--allow-truncate", flags["--allow-truncate"]),
                         ("--input", inputs)):
            if val:
                out("error", name, prov.get("model", "?"), detail=(
                    f"{bad} is not supported for image providers "
                    f"(v1 is text-to-image only)"))
                sys.exit(1)
        if not opts["--out"]:
            out("error", name, prov.get("model", "?"), detail=(
                "--out FILE is required for image providers (binary "
                "output never goes to stdout)"))
            sys.exit(1)
```

Note: the existing `if not is_cli:` block (line 569-585) rejects `--effort`/`--resume-last` with a CLI-specific message and fetches `api_key` — the image validation above runs BEFORE it, so image providers get the clearer message; the `api_key` fetch (line 582-585) already covers image providers since `is_cli` is False. Guard the two CLI-semantics errors inside `if not is_cli:` with `if not is_image` — they are already handled above:

```python
    if not is_cli:
        if not is_image and opts["--effort"]:
            ...existing error...
        if not is_image and flags["--resume-last"]:
            ...existing error...
        api_key = ...existing...
```

(c) Dispatch (line 626-630) becomes:

```python
        if is_image:
            img_bytes, img_mime, img_text = call_image(
                prov, name, api_key, spec_text, timeout)
        elif is_cli:
            content = call_cli(prov, name, user_msg, timeout, opts, schema_path)
        else:
            content = call_http(prov, name, api_key, user_msg, timeout)
```

(d) Immediately after the `finally` block that removes `ACTIVE_PATH` (line 635), add the image epilogue BEFORE the existing `base_log`/empty-content handling:

```python
    if is_image:
        base_log = {"provider": name, "model": prov["model"],
                    "billing": billing_of(prov),
                    "type": opts.get("--type"), "chars_in": len(spec_text)}
        if img_bytes is None:
            out("error", name, prov["model"], detail=(
                "no image in provider response"
                + (f" — provider said: {img_text}" if img_text else "")))
            log_exec({**base_log, "ok": False, "check": "no-image"})
            sys.exit(1)
        try:
            Path(opts["--out"]).write_bytes(img_bytes)
        except OSError as e:
            out("error", name, prov["model"], "image", "-",
                f"--out write failed: {e}")
            sys.exit(1)
        out("ok", name, prov["model"], "image", opts["--out"],
            f"{img_mime}, {len(img_bytes)} bytes")
        log_exec({**base_log, "ok": True, "check": "image",
                  "bytes_out": len(img_bytes)})
        sys.exit(0)
```

(e) Docstring: extend the summary with one paragraph:

```
Provider "type": "image" (es. gemini-image): la spec È il prompt; endpoint
nativo generateContent, bytes su --out (OBBLIGATORIO — binario mai su
stdout, perimetro scrittura del budget rispettato). Incompatibili e
rumorosi: --schema-*, --effort, --resume-last, --allow-truncate, --input
(v1 text-to-image puro). 429 "limit: 0" = billing non abilitato sul
progetto Google, messaggio dedicato.
```

- [ ] **Step 4: Run the new suite and the full regression**

Run: `python3 tests/image-route-verify.py && python3 tests/external-exec-verify.py && python3 tests/cross-verify-billing.py && python3 tests/route-hint-verify.py`
Expected: all PASS, exit 0 each.

- [ ] **Step 5: Commit**

```bash
git add fable-director/scripts/external-exec.py tests/image-route-verify.py
git commit -m "feat: image provider type — native generateContent, bytes to mandatory --out"
```

---

### Task 6: `DEFAULT_CONFIG` billing fields + `fd-status.py` free/paid split

**Files:**
- Modify: `fable-director/scripts/cross-verify.py` (`DEFAULT_CONFIG` line 71-125)
- Modify: `fable-director/scripts/fd-status.py` (external-today block line 176-185)

**Interfaces:**
- Consumes: telemetry `counts` per provider (existing) + `providers.*.billing` from config.
- Produces: `DEFAULT_CONFIG` entries all carry `billing`; new `gemini-image` template entry; fd-status line suffix `— N free, M PAID` only when M > 0.

- [ ] **Step 1: Update `DEFAULT_CONFIG`**

In `cross-verify.py` `DEFAULT_CONFIG`, add to each provider:

- `gemini`: `"billing": "free",`
- `gemini-stable`: `"billing": "free",`
- `codex`: `"billing": "free",` and append to its note: `. billing free = flat nel piano ChatGPT (nessun costo marginale per chiamata)`
- `grok`: `"billing": "paid", "cost_note": "~$0.003/verify",`

Add a new entry after `grok`:

```python
        "gemini-image": {
            "type": "image",
            "base_url": "https://generativelanguage.googleapis.com/v1beta",
            "model": "gemini-2.5-flash-image",
            "api_key_env": "GEMINI_API_KEY",
            "billing": "paid",
            "cost_note": "~$0.04/image",
            "note": "generazione immagini (external-exec.py --out img.png). "
                    "A PAGAMENTO: il free tier AI Studio ha limit 0 sui "
                    "modelli image (verificato 2026-07-22) — serve billing "
                    "abilitato sul progetto Google. Solo su consenso "
                    "esplicito (--paid-ok)"
        }
```

- [ ] **Step 2: Update fd-status**

In `fd-status.py`, replace the `if counts:` branch (lines 176-182) with:

```python
        if counts:
            provs = (xf_cfg or {}).get("providers") or {}
            parts = []
            for k, v in sorted(counts.items()):
                rpd = ((provs.get(k) or {}).get("limits") or {}).get("rpd")
                parts.append(f"{k}×{v}" + (f"/{rpd} rpd" if rpd else ""))
            # Split free/paid: fail-closed, billing assente = paid. La riga
            # segnala spesa vera solo quando c'è (zero rumore altrimenti).
            nfree = sum(v for k, v in counts.items()
                        if (provs.get(k) or {}).get("billing") == "free")
            npaid = sum(counts.values()) - nfree
            lines.append("external today: " + ", ".join(parts)
                         + (f" — {nfree} free, {npaid} PAID"
                            if npaid else ""))
```

- [ ] **Step 3: Verify by hand (no suite covers fd-status output)**

Run: `python3 fable-director/scripts/fd-status.py` (with the real HOME)
Expected: the `external today:` line renders; with only free calls logged today there is NO `PAID` suffix. Also run `python3 fable-director/scripts/cross-verify.py --init` against a throwaway HOME to confirm the new config is valid JSON:

```bash
H=$(mktemp -d) && HOME=$H python3 fable-director/scripts/cross-verify.py --init \
  && HOME=$H python3 -c "import json,os;json.load(open(os.path.expanduser('~/.claude/fable-director/cross-family.json')));print('config JSON OK')"
```

- [ ] **Step 4: Full regression**

Run: `for t in tests/*.py; do python3 "$t" || break; done; python3 tests/transcript-contract/run.py`
Expected: every suite exit 0. (`windows-verify.py` self-skips off-Windows; if any suite pins doctor/status output changed here, fix the assertion to the new text — the new text is the spec.)

- [ ] **Step 5: Commit**

```bash
git add fable-director/scripts/cross-verify.py fable-director/scripts/fd-status.py
git commit -m "feat: billing in DEFAULT_CONFIG (+gemini-image template), fd-status splits free/paid"
```

---

### Task 7: Policy prose — `kernel.md`, `SKILL.md`, XF onboarding

**Files:**
- Modify: `fable-director/kernel.md` (axis 4, line 7)
- Modify: `fable-director/skills/delega-efficiente/SKILL.md` (axis-4 bullet, line 35)
- Modify: `fable-director/scripts/session-kernel.sh` (onboarding, line 34)

**Interfaces:**
- Consumes: the `--paid-ok` guard semantics from Tasks 1-3 (prose must match the code exactly: fail-closed, consent per conversation, cost cited).
- Produces: kernel/skill text other sessions rely on.

- [ ] **Step 1: kernel.md axis 4**

In `fable-director/kernel.md` line 7, replace the final sentence fragment `→ PROPOSE the free-tier route to the user (`external-exec.py`; separate ledger, never counted as Claude tokens — free tiers reset daily).` with:

```
→ PROPOSE the free-tier route to the user (`external-exec.py`; separate ledger, never counted as Claude tokens — free tiers reset daily). Paid providers (`billing:"paid"`, e.g. image generation): NEVER proposed by default, NEVER run without explicit user consent in this conversation (`--paid-ok` only after the user agreed); mention one only when clearly superior with no free alternative, always citing its cost first.
```

- [ ] **Step 2: SKILL.md axis-4 bullet**

In `fable-director/skills/delega-efficiente/SKILL.md` line 35, after the sentence `axis 2 items never take this route.` insert (in-place, same bullet — the skill's complexity budget forbids new sections):

```
Providers with `billing:"paid"` are consent-gated: never proposed by default, `--paid-ok` only after an explicit user yes with the cost cited (fail-closed — a provider without `billing` counts as paid).
```

- [ ] **Step 3: session-kernel.sh onboarding**

In `fable-director/scripts/session-kernel.sh` line 34, replace `(paid API keys work in the same config entries)` with `(paid API keys work in the same config entries with billing:"paid" — consent-gated, never auto-proposed)`.

- [ ] **Step 4: Full regression (transcript-contract pins hook text)**

Run: `python3 tests/transcript-contract/run.py && for t in tests/*.py; do python3 "$t" || break; done`
Expected: all green. If `transcript-contract` pins the old kernel/onboarding strings, update its expected text to the new wording (the new wording is the spec).

- [ ] **Step 5: Commit**

```bash
git add fable-director/kernel.md fable-director/skills/delega-efficiente/SKILL.md fable-director/scripts/session-kernel.sh
git commit -m "docs: paid providers consent-gated in kernel, skill and onboarding prose"
```

---

### Task 8: User config migration + release 1.24.0

**Files:**
- Modify: `~/.claude/fable-director/cross-family.json` (outside the repo — user machine)
- Modify: `fable-director/.claude-plugin/plugin.json` (version 1.23.0 → 1.24.0)
- Modify: `CHANGELOG.md`, `README.md` (What's new — plain simple title, max 5 entries)

**Interfaces:**
- Consumes: everything above.
- Produces: released plugin 1.24.0 installed on both accounts (release.sh does it).

- [ ] **Step 1: Migrate the local user config**

Edit `~/.claude/fable-director/cross-family.json`: add `"billing": "free"` to `gemini` and `gemini-stable`; add `"billing": "paid", "cost_note": "~$0.003/verify"` to `grok`; add `"billing": "free"` to `_disabled_providers.codex` (ready for its 2026-08-08 return); append the `gemini-image` entry from Task 6 Step 1 to `providers`. Then verify:

Run: `python3 fable-director/scripts/external-exec.py --doctor`
Expected: exit 0, every provider shows `billing: free` or `billing: PAID (...)`, no `billing UNDECLARED`. (`gemini-image` will show API key present; a live generation still 429s until Franz enables Google billing — expected, documented.)

- [ ] **Step 2: Bump + changelog + What's new**

- `fable-director/.claude-plugin/plugin.json`: `"version": "1.24.0"`.
- `CHANGELOG.md`: new `## 1.24.0` section: billing field fail-closed; `--paid-ok` guard in external-exec/cross-verify; route-hint/kernel propose free only; doctor billing-aware; fd-status free/paid split; image provider type + `gemini-image` template.
- `README.md` What's new: add ONE entry with a plain simple title (e.g. `- **1.24.0 — Paid providers consent-gated + Gemini image route**: ...`), prune to max 5 entries.

- [ ] **Step 3: Full suite green, then release**

Run: `for t in tests/*.py; do python3 "$t" || break; done && python3 tests/transcript-contract/run.py`
Expected: all exit 0.

Run: `bash release.sh 1.24.0`
Expected: preflight OK (version consistent), suites green, zip built, push + tag + release, install on both accounts (house procedure — see memory `release-checklist`).

- [ ] **Step 4: Commit anything release.sh didn't (it normally commits/pushes itself; verify)**

```bash
git status --short   # expected: clean
git log --oneline -3 # expected: release commit + tag on top
```

---

## Self-Review (done at planning time)

- **Spec coverage:** §1 billing field → Tasks 1, 6, 8; §2 guard → Tasks 1, 2, 3; §3 proposal surfaces → Tasks 4, 6 (fd-status), 7; §4 image route → Task 5, template in Task 6, config in Task 8; §5 tests → E11-E16, C1-C4, R15-R16, I1-I6; release → Task 8. Fuori scope respected (no Imagen, no image editing, no statusline change).
- **Consistency:** `billing_of` duplicated by declared design choice; guard message `is billed ... --paid-ok` identical in both scripts; `CHECK: image` used in code, test, and telemetry alike.
- **Known risk:** exact helper names in `tests/route-hint-verify.py` (Task 4 Step 1) must be adapted to the file's real helpers — instruction included; `transcript-contract` may pin old prose — instruction included (new wording wins).
