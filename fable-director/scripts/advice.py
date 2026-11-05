#!/usr/bin/env python3
"""Consiglio di modello ed effort per sessione (1.56.0, contratto claude-master
1.37 parte A): il relay lo legge da ~/.claude/fable-director/sessions/<sid>.json,
chiave "advice", e lo mostra nei selettori «Modello» ed «Effort» dell'app.

Regole (la prima che scatta vince; deterministiche, zero token):
  1. quota 5h >= 90%            -> Opus 5.5, effort al massimo medium
     (senza 5 ore: consumo sopra soglia, quota_pace.py -> stessa proposta)
  2. settimana >= 85%           -> Opus 5.5, effort al massimo medium
  3. su Fable, finestra premium -> Opus 5.5 high + /advisor fable: Opus esegue,
     (7D / premium_weekly_fraction  Fable giudica (bound dichiarato in
     di plan-<acct>.json) >= 80%   plan-<acct>.json, mai una misura)
     (anche fuori da Fable quando l'importanza alta proporrebbe Fable)
  4. importanza del lavoro (1.64.0), asse 2 della policy (costo dell'errore):
       alta  -> Fable 5.1 almeno high; xhigh se la settimana e sotto il 50%
       media -> Opus 5.5 almeno high
       bassa -> nessuna proposta da qui (le regole sotto)
     L'effort scelto sopra la proposta resta, salvo quota >= 70% (-> high);
     max non si propone mai: e una scelta della persona, per gli atti finali.
     Fonte, la piu vicina vince:
       a) il compito attivo della cartella, dichiarato da supervisor in
          ~/.claude/supervisor/task-importance.json (CM_IMPORTANCE_FILE per i
          test): {"dirs": {"/cartella": {"importance": "alta"|"media"|"bassa",
          "task", "why", ...}}}, cartella della sessione o una sopra;
          file piu vecchio di 24 h = supervisor fermo, si ignora;
       b) senza compito, la cartella dichiarata: "stakes" in .fd-perimeter.json
          della cartella o di una sopra ("alta" oppure {"level": "alta",
          "why": "pratica legale"}), poi "stakes": {"/cartella": ...} nel
          globale ~/.claude/fable-director/perimeter.json.
  5. sessione principale non di punta -> Opus 5.5: la principale pianifica e
     verifica, l'esecuzione va agli agenti
  6. effort xhigh/max, con 5h o settimana >= 70% -> high: consuma quota in
     silenzio. Sotto il 70% (o senza dati di quota) il max scelto apposta resta.
  altrimenti la scelta attuale.

Un cambio di modello o di effort riscrive la cache: switch_cost_tokens = il
contesto attuale. when = "now" solo a contesto fresco (< 50k) e senza budget
aperto nel cwd; altrimenti "next_task" (dopo /clear o al compito nuovo). Il
consiglio non si applica mai da solo. Quota assente -> nessun numero inventato.

CLI:
  advice.py --session SID   ricalcola l'advice nello snapshot di SID
  advice.py --all           tutte le sessioni con snapshot < 6 h
"""
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import quota_pace  # noqa: E402

FABLE, OPUS = "claude-fable-5-1", "claude-opus-5-5"
TOP = (FABLE, OPUS)
NAMES = {FABLE: "Fable 5.1", OPUS: "Opus 5.5"}
EFFORTS = ("low", "medium", "high", "xhigh", "max")
FRESH_CTX = 50_000
LIVE_S = 6 * 3600


def _name(m):
    return NAMES.get(m, m or "?")


def _cap(effort, top):
    """effort ridotto a `top` se lo supera; sconosciuto -> top."""
    if effort in EFFORTS and EFFORTS.index(effort) <= EFFORTS.index(top):
        return effort
    return top


def _pct(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _at_least(effort, target, hot):
    """Almeno `target`; sopra resta la scelta della persona, salvo quota calda."""
    if effort in EFFORTS and EFFORTS.index(effort) > EFFORTS.index(target):
        return "high" if hot else effort
    return target


def compute(snap, quota, plan, budget_open, now, burn=None, importance=None):
    model = snap.get("model") or ""
    effort = snap.get("effort")
    ctx = int(snap.get("ctx_tokens") or 0)
    quota, plan = quota or {}, plan or {}
    # r: solo una finestra viva. Senza, i rami sotto parlano di settimana e ritmo.
    r, w = quota_pace.five_hour_used(quota, now), _pct(quota.get("weekly_used_pct"))
    res = quota_pace.residue_pct(w, quota.get("weekly_resets_at"), now) if r is None else None
    frac = _pct(plan.get("premium_weekly_fraction"))
    base = model.split("[")[0]
    is_fable = base.startswith(FABLE)
    imp = importance or {}
    level = imp.get("level")
    hot = max(r or 0, w or 0) >= 70

    if r is not None and r >= 90:
        m, e = OPUS, _cap(effort, "medium")
        reset = quota.get("five_hour_resets_at")
        at = time.strftime(" fino al reset delle %H:%M", time.localtime(int(reset))) \
            if reset else ""
        why = f"quota 5h al {r:.0f}%: Opus 5.5 a effort {e}{at}; al muro /low-priority"
    elif w is not None and w >= 85:
        m, e = OPUS, _cap(effort, "medium")
        why = f"settimana al {w:.0f}%: Opus 5.5 a effort {e} per i lavori nuovi"
    elif r is None and burn and burn >= quota_pace.burn_ceiling():
        m, e = OPUS, _cap(effort, "medium")
        why = (f"consumo {burn:.1f}%/h, sopra il ritmo settimanale: Opus 5.5 a effort {e} "
               f"per i lavori nuovi")
    elif (is_fable or level == "alta") and w is not None and frac and w / frac >= 80:
        m, e = OPUS, _cap(effort, "high")
        why = (f"finestra Fable ≤{min(100, w / frac):.0f}%: Opus 5.5 esegue, "
               f"/advisor fable per i giudizi")
        if level == "alta":
            why = f"{_imp_label(imp)}, ma {why}"
    elif level == "alta":
        m = FABLE
        e = _at_least(effort, "xhigh" if w is not None and w < 50 else "high", hot)
        quota_s = f", settimana al {w:.0f}%" if w is not None else ""
        why = f"{_imp_label(imp)}: Fable 5.1 {e}{quota_s}"
    elif level == "media":
        m, e = OPUS, _at_least(effort, "high", hot)
        why = f"{_imp_label(imp)}: Opus 5.5 {e}"
    elif base and not any(base.startswith(t) for t in TOP):
        m, e = OPUS, _cap(effort, "high")
        why = "la sessione principale pianifica e verifica: Opus 5.5; l'esecuzione va agli agenti"
    elif effort in ("xhigh", "max") and max(r or 0, w or 0) >= 70:
        m, e = model, "high"
        level = f"5h {r:.0f}%, settimana {w or 0:.0f}%" if r is not None else f"settimana {w or 0:.0f}%"
        why = (f"quota alta ({level}): effort {effort} "
               f"la consuma in silenzio, high basta")
    else:
        m, e = model, effort
        if r is not None and w is not None:
            why = f"quota ok (5h {r:.0f}%, settimana {w:.0f}%): {_name(m)} {e} va bene"
        elif res:
            why = f"quota ok (settimana {w:.0f}%, ritmo {res[0]:.1f}%/g): {_name(m)} {e} va bene"
        else:
            why = f"{_name(m)} {e} va bene"
        if level == "bassa":
            why = f"{_imp_label(imp)}: {why}"

    differs = (m.split("[")[0] != base) or (e != effort)
    when = "now" if ctx < FRESH_CTX and not budget_open else "next_task"
    if differs and when == "next_task":
        why += "; cambia dopo /clear o al compito nuovo"
    return {"model": m, "effort": e, "reason": why[:200], "switch_cost_tokens": ctx,
            "at": int(now), "source": "fable-director", "when": when, "differs": differs}


# --- I/O ------------------------------------------------------------------

BASE = Path.home() / ".claude" / "fable-director"


def _load(p):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return None


def default_account():
    cfg = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return hashlib.sha256(cfg.encode()).hexdigest()[:8]


def cwd_slug(cwd):
    s = str(cwd).replace("\\", "/")
    base = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
    return f"{base}-{hashlib.sha256(s.encode()).hexdigest()[:8]}"


def budget_is_open(cwd):
    if not cwd:
        return False
    b = _load(BASE / "budgets" / f"{cwd_slug(cwd)}.json")
    return bool(b) and b.get("status") == "open"


LEVELS = {"alta": "alta", "media": "media", "bassa": "bassa",
          "high": "alta", "medium": "media", "low": "bassa"}
IMPORTANCE_STALE_S = 24 * 3600


def _norm_dir(p):
    """Confronto cartelle: / e \\ uguali, niente / finale, senza maiuscole."""
    s = str(p or "").replace("\\", "/").rstrip("/")
    return s.casefold()


def _nearest(cwd, mapping):
    """Voce della cartella piu vicina: cwd stessa o la prima sopra."""
    c, best, blen = _norm_dir(cwd), None, -1
    if not c or not isinstance(mapping, dict):
        return None
    for k, v in mapping.items():
        d = _norm_dir(k)
        if d and (c == d or c.startswith(d + "/")) and len(d) > blen:
            best, blen = v, len(d)
    return best


def _level_of(v):
    """"alta" oppure {"level"|"importance": "alta", "why": ...} -> (livello, why)."""
    if isinstance(v, str):
        return LEVELS.get(v.strip().lower()), None
    if isinstance(v, dict):
        lv = v.get("importance") or v.get("level")
        return (LEVELS.get(str(lv).strip().lower()) if lv else None), v.get("why")
    return None, None


def importance_file():
    return Path(os.environ.get("CM_IMPORTANCE_FILE")
                or Path.home() / ".claude" / "supervisor" / "task-importance.json")


def importance_for(cwd, now=None):
    """Importanza del lavoro nella cartella: compito attivo, poi cartella dichiarata.

    -> {"level", "why", "source": "task"|"folder", "task", "dir"} oppure None."""
    if not cwd:
        return None
    now = now or time.time()
    # a) compito attivo, da supervisor
    reg = _load(importance_file())
    if isinstance(reg, dict):
        try:
            fresh = now - float(reg.get("updated_at") or 0) <= IMPORTANCE_STALE_S
        except (TypeError, ValueError):
            fresh = False
        e = _nearest(cwd, reg.get("dirs")) if fresh else None
        if isinstance(e, dict):
            lv, why = _level_of(e)
            if lv:
                return {"level": lv, "why": why, "source": "task", "task": e.get("task")}
    # b) cartella dichiarata: .fd-perimeter.json qui o sopra, poi il globale
    p = Path(str(cwd))
    for d in [p, *p.parents]:
        cfg = _load(d / ".fd-perimeter.json")
        if isinstance(cfg, dict) and "stakes" in cfg:
            lv, why = _level_of(cfg.get("stakes"))
            if lv:
                return {"level": lv, "why": why, "source": "folder", "dir": d.name or str(d)}
    g = _load(BASE / "perimeter.json")
    if isinstance(g, dict):
        v = _nearest(cwd, g.get("stakes"))
        lv, why = _level_of(v)
        if lv:
            return {"level": lv, "why": why, "source": "folder",
                    "dir": Path(str(cwd)).name}
    return None


def _imp_label(imp):
    lv, why = imp.get("level"), imp.get("why")
    if imp.get("source") == "task":
        what = why or f"compito {imp.get('task') or '?'}"
        return f"{what} (importanza {lv})"
    what = why or f"cartella {imp.get('dir') or '?'}"
    return f"{what} (posta {lv}, dichiarata)"


def for_snapshot(snap, now=None):
    acct = snap.get("account") or default_account()
    try:
        imp = importance_for(snap.get("cwd"), now)
    except Exception:
        imp = None
    return compute(snap, _load(BASE / f"quota-{acct}.json"),
                   _load(BASE / f"plan-{acct}.json"),
                   budget_is_open(snap.get("cwd")), now or time.time(),
                   burn=quota_pace.burn_pct_h(now, acct), importance=imp)


def rewrite(sf):
    snap = _load(sf)
    if not isinstance(snap, dict) or not snap.get("model"):
        return False
    snap["advice"] = for_snapshot(snap)
    tmp = sf.with_name(f"{sf.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(snap))
    os.replace(tmp, sf)
    return True


def main(argv):
    sd = BASE / "sessions"
    if len(argv) == 2 and argv[0] == "--session":
        sf = sd / f"{argv[1].replace('/', '-')}.json"
        if not rewrite(sf):
            print(f"no snapshot with a model for {argv[1]}", file=sys.stderr)
            return 1
        return 0
    if argv == ["--all"]:
        n = 0
        for sf in sd.glob("*.json"):
            try:
                if time.time() - sf.stat().st_mtime > LIVE_S:
                    continue
            except OSError:
                continue
            n += rewrite(sf)
        print(f"advice: {n} sessions")
        return 0
    print(__doc__.split("CLI:")[1].strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
