#!/usr/bin/env python3
"""Rendiconto token di sessioni Claude Code dai transcript JSONL — zero token di modello.

Somma i blocchi `usage` (input/output/cache) per modello e per file transcript,
separando main loop e subagenti. Confronta col pre-budget dichiarato (soglia 3×).

Uso:
  session-cost-report.py [PROJECT_DIR] [--session SESSION_ID] [--budget N_OUTPUT_TOKENS]
                         [--budget-input N_INPUT_TOKENS]

  PROJECT_DIR     dir progetto (default: quella del cwd corrente, cercata in
                  $CLAUDE_CONFIG_DIR/projects, altrimenti ~/.claude/projects)
  --session       filtra i file il cui nome contiene SESSION_ID
  --budget        output token attesi dichiarati nel pre-budget; stampa ratio e flag ≥3×
  --budget-input  input token attesi (task read-heavy: scansioni repo, log, ricerca)

Senza --budget, se esiste un budget file per il cwd
(~/.claude/fable-director/budgets/<slug>.json, status open/flagged) le stime
vengono lette da lì. Oltre ai totali stampa le metriche derivate di cache e
delega (allarmi, non target: ottimizzarle al ribasso è Goodhart reentry).
"""
import json
import re
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

USAGE_KEYS = ("input_tokens", "output_tokens",
              "cache_read_input_tokens", "cache_creation_input_tokens")


def _fdt():
    """fd-telemetry.py del plugin (stessa installazione): fonte unica dei
    moltiplicatori eq per modello (model-economics.json). None se assente:
    il report resta in token puri, senza inventare tariffe."""
    import importlib.util
    cand = Path(__file__).resolve().parents[3] / "scripts" / "fd-telemetry.py"
    if not cand.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location("fdt", cand)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


BANDS = ["1-25", "26-50", "51-100", "101-200", "201-400", "401-800", "801+"]


def depth_band(n):
    for b in BANDS:
        if b.endswith("+"):
            return b
        lo, hi = b.split("-")
        if int(lo) <= n <= int(hi):
            return b
    return BANDS[-1]


def find_usage(obj, model_hint=None, in_tool_result=False):
    """Cerca ricorsivamente blocchi usage con i campi token; yield (model, usage).

    Salta gli usage annidati sotto toolUseResult: sono l'aggregato di un
    subagent già contato dal suo file agent-*.jsonl (che porta anche il
    modello) — contarli entrambi raddoppierebbe i subagenti."""
    if isinstance(obj, dict):
        model = obj.get("model", model_hint)
        usage = obj.get("usage")
        if not in_tool_result and isinstance(usage, dict) \
                and any(k in usage for k in USAGE_KEYS):
            yield (model or "unknown", usage)
        for k, v in obj.items():
            yield from find_usage(v, model, in_tool_result or k == "toolUseResult")
    elif isinstance(obj, list):
        for v in obj:
            yield from find_usage(v, model_hint, in_tool_result)


def project_dirs_for_cwd():
    cwd_slug = "-" + str(Path.cwd()).strip("/").replace("/", "-").replace(".", "-")
    dirs = []
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    # Host con nome custom per la dir transcript (CLAUDE_CODE_PROJECT_DIR_NAME,
    # CC ≥2.1.234): lo slug derivato dal cwd non esiste su disco — si prova
    # prima il nome dichiarato; var assente o dir mancante → percorso classico.
    custom = os.environ.get("CLAUDE_CODE_PROJECT_DIR_NAME")
    bases = [Path(cfg) / "projects"] if cfg else []
    default = Path.home() / ".claude" / "projects"
    # CLAUDE_CONFIG_DIR può puntare a ~/.claude: senza dedup la stessa dir
    # verrebbe scansionata due volte e i totali del report raddoppierebbero.
    if not any(b.resolve() == default.resolve() for b in bases):
        bases.append(default)
    for base in bases:
        if not base.is_dir():
            continue
        if custom and (base / custom).is_dir():
            dirs.append(base / custom)
            continue
        exact = base / cwd_slug
        if exact.is_dir():
            dirs.append(exact)
        else:
            # fallback: match sul nome della dir corrente
            tail = Path.cwd().name.replace(".", "-")
            dirs.extend(p for p in base.iterdir() if p.is_dir() and tail in p.name)
    return list(dict.fromkeys(dirs))


def load_budget_file():
    """Budget file scritto da fd-telemetry.py budget-open per il cwd corrente.
    Slug identico a cwd_slug() in fd-telemetry.py (canonico + hash)."""
    import hashlib
    import re
    s = str(Path.cwd()).replace("\\", "/")
    slug = (re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
            + "-" + hashlib.sha256(s.encode()).hexdigest()[:8])
    bfile = Path.home() / ".claude" / "fable-director" / "budgets" / f"{slug}.json"
    if not bfile.is_file():
        return None
    try:
        b = json.loads(bfile.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return b if b.get("status") in ("open", "flagged") else None


def main():
    args = sys.argv[1:]
    budget = None
    budget_input = None
    session_filter = None
    since = None   # --since YYYY-MM-DD: confronto prima/dopo (turni per profondità)
    dirs = []
    while args:
        a = args.pop(0)
        if a == "--budget":
            budget = int(args.pop(0))
        elif a == "--budget-input":
            budget_input = int(args.pop(0))
        elif a == "--session":
            session_filter = args.pop(0)
        elif a == "--since":
            since = datetime.fromisoformat(args.pop(0)).replace(tzinfo=timezone.utc)
        else:
            dirs.append(Path(a))
    budget_task = None
    declared = None
    if budget is None:
        bf = load_budget_file()
        if bf:
            budget = bf.get("expected_output_tokens") or None
            budget_input = budget_input or bf.get("expected_input_tokens") or None
            budget_task = bf.get("task")
            # Scope del confronto pre-budget: dal declared_at in poi, come lo
            # Stop hook. Confrontare la stima di UN task coi totali di
            # sessione/progetto intera produce ratio spuri (visto 32× con
            # subagenti a 1.08× della stima).
            try:
                declared = datetime.fromisoformat(
                    str(bf.get("declared_at")).replace("Z", "+00:00"))
                if declared.tzinfo is None:
                    declared = declared.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                declared = None
    if not dirs:
        dirs = project_dirs_for_cwd()
    if not dirs:
        sys.exit("Nessuna project dir trovata: passala come argomento.")

    per_model = defaultdict(lambda: defaultdict(int))
    per_file = defaultdict(lambda: defaultdict(int))
    bad_lines = 0
    files = []
    for d in dirs:
        files.extend(sorted(d.rglob("*.jsonl")))
    if session_filter:
        files = [f for f in files if session_filter in str(f)]
    if not files:
        sys.exit(f"Nessun transcript .jsonl in: {', '.join(map(str, dirs))}")

    scoped = defaultdict(int)   # solo record >= declared_at (per il pre-budget)
    scoped_no_ts = 0
    fdt = _fdt()
    # Attrito e costo per turno per profondità (metodo: makinggainz/
    # claude-code-measure-efficiency, 2026-08). Ogni turno del main thread
    # porta (ts, eq, banda di profondità, periodo); i subagent vanno al turno
    # che li ha lanciati (l'ultimo turno main precedente nel tempo).
    corr_re = getattr(fdt, "CORRECTION_RE", None) if fdt else None
    if corr_re is None:
        corr_re = re.compile(r"(?i)\b(non funziona|sbagliat[oaie]|rivedi|rifai|hai saltato|annulla|revert|undo|"
                             r"that'?s wrong|does ?n'?t work|you missed|wrong)\b")
    fr = {"before": defaultdict(int), "after": defaultdict(int)}
    edits_by_file = {"before": defaultdict(int), "after": defaultdict(int)}
    main_turns = []     # (ts, eq, band, period)
    sub_costs = []      # (ts, eq)
    for f in files:
        kind = "subagent" if "agent" in f.name else "main"
        session_start = None
        turn_idx = 0
        with open(f, errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    bad_lines += 1
                    continue
                rts = None
                try:
                    rts = datetime.fromisoformat(str(rec.get("timestamp")).replace("Z", "+00:00"))
                    if rts.tzinfo is None:
                        rts = rts.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    pass
                if session_start is None and rts is not None:
                    session_start = rts
                period = "after" if (since is not None and session_start is not None
                                     and session_start >= since) else "before"
                # attrito
                msg = rec.get("message") or {}
                content = msg.get("content")
                if rec.get("type") == "user" and isinstance(content, list):
                    fr[period]["tool_errors"] += sum(
                        1 for b in content if isinstance(b, dict)
                        and b.get("type") == "tool_result" and b.get("is_error"))
                if rec.get("type") == "user" and not rec.get("isMeta") and not rec.get("isSidechain"):
                    if isinstance(content, str):
                        ht = content.strip()
                    elif isinstance(content, list) and not any(
                            isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                        ht = " ".join(str(b.get("text") or "") for b in content
                                      if isinstance(b, dict) and b.get("type") == "text").strip()
                    else:
                        ht = ""
                    if ht and not ht.startswith("<"):
                        fr[period]["human_turns"] += 1
                        if corr_re.search(ht):
                            fr[period]["corrections"] += 1
                if rec.get("type") == "assistant" and isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "tool_use":
                            fr[period]["tool_calls"] += 1
                            if b.get("name") in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
                                fp = str((b.get("input") or {}).get("file_path") or
                                         (b.get("input") or {}).get("notebook_path") or "")
                                if fp:
                                    edits_by_file[period][(f.name, fp)] += 1
                in_scope = True
                if declared is not None:
                    try:
                        ts = datetime.fromisoformat(
                            str(rec.get("timestamp")).replace("Z", "+00:00"))
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                        in_scope = ts >= declared
                    except (ValueError, TypeError):
                        # record senza timestamp: escluso dallo scope budget
                        # (stessa scelta dello Stop hook), contato a parte
                        in_scope = False
                        scoped_no_ts += 1
                for model, usage in find_usage(rec):
                    for k in USAGE_KEYS:
                        v = usage.get(k) or 0
                        per_model[model][k] += v
                        per_file[(kind, f.name)][k] += v
                        if in_scope:
                            scoped[k] += v
                    if fdt and rts is not None:
                        eq = fdt.eq_tokens(usage.get("input_tokens") or 0, usage.get("output_tokens") or 0,
                                           usage.get("cache_read_input_tokens") or 0,
                                           usage.get("cache_creation_input_tokens") or 0, model=model)
                        if kind == "main":
                            turn_idx += 1
                            main_turns.append([rts, eq, depth_band(turn_idx), period])
                        else:
                            sub_costs.append((rts, eq))

    def fmt(n):
        return f"{n:,}".replace(",", ".")

    print(f"# Report token — {len(files)} transcript, "
          f"{bad_lines} righe illeggibili (ignorate)\n")
    eq_col = f" {'eq':>12} {'cr×':>5}" if fdt else ""
    print(f"{'modello':<40} {'input':>12} {'output':>12} {'cache_read':>12} {'cache_new':>12}{eq_col}")
    tot = defaultdict(int)
    tot_eq = 0
    for model, u in sorted(per_model.items()):
        line = (f"{model:<40} {fmt(u['input_tokens']):>12} {fmt(u['output_tokens']):>12} "
                f"{fmt(u['cache_read_input_tokens']):>12} {fmt(u['cache_creation_input_tokens']):>12}")
        if fdt:
            # eq alla tariffa DEL modello della riga (cache_read 0,025× su
            # Fable 5.1, 0,1× altrove — model-economics.json): il totale in
            # eq è la somma delle righe, mai una tariffa unica sul totale.
            eq = fdt.eq_tokens(u["input_tokens"], u["output_tokens"],
                               u["cache_read_input_tokens"],
                               u["cache_creation_input_tokens"], model=model)
            tot_eq += eq
            line += f" {fmt(eq):>12} {fdt.eq_mult(model)['cache_read']:>5.3f}"
        print(line)
        for k in USAGE_KEYS:
            tot[k] += u[k]
    line = (f"{'TOTALE':<40} {fmt(tot['input_tokens']):>12} {fmt(tot['output_tokens']):>12} "
            f"{fmt(tot['cache_read_input_tokens']):>12} {fmt(tot['cache_creation_input_tokens']):>12}")
    if fdt:
        line += f" {fmt(tot_eq):>12}"
    print(line)

    main_out = sum(u["output_tokens"] for (k, _), u in per_file.items() if k == "main")
    sub_out = sum(u["output_tokens"] for (k, _), u in per_file.items() if k == "subagent")
    print(f"\noutput main loop: {fmt(main_out)}  |  output subagenti: {fmt(sub_out)}")

    # Metriche derivate — ALLARMI, non target da ottimizzare.
    inp, out = tot["input_tokens"], tot["output_tokens"]
    cr, cc = tot["cache_read_input_tokens"], tot["cache_creation_input_tokens"]
    total_in = inp + cr + cc
    alarms = []
    print("\n## Metriche derivate (allarmi, non target)")
    if cr + cc:
        hit = cr / (cr + cc)
        eff = cr / total_in if total_in else 0
        inv_s = "∞ (cache mai riletta)" if cr == 0 else f"{cc / cr:.2f}"
        print(f"cache_hit_ratio: {hit:.2f}   cache_efficiency: {eff:.2f}   "
              f"cache_investment: {inv_s}")
        if hit < 0.7:
            alarms.append("cache_hit_ratio < 0.7: prefisso instabile (cambi modello/plugin a metà sessione?)")
        if cr and cc / cr > 1:
            alarms.append("cache_investment > 1: cache scritta più di quanta riletta")
    if sub_out:
        overhead = sub_out / out if out else 0
        coord_s = f"{main_out / sub_out:.2f}"
        print(f"delegation_overhead: {overhead:.2f}   coordination_cost: {coord_s}")
        if main_out / sub_out > 1:
            alarms.append("coordination_cost > 1: orchestratore spende più dei subagenti")
    for a in alarms:
        print(f"⚠ {a}")

    # Attrito — proxy di rework, non correttezza.
    def fr_line(d, ed):
        tc, te = d["tool_calls"], d["tool_errors"]
        ht, co = d["human_turns"], d["corrections"]
        edits = sum(ed.values())
        churn = sum(n - 1 for n in ed.values() if n > 1)
        return (f"tool error rate {(100 * te / tc) if tc else 0:.1f}% ({te}/{tc})   "
                f"correction rate {(100 * co / ht) if ht else 0:.1f}% ({co}/{ht} turni umani)   "
                f"edit churn {(100 * churn / edits) if edits else 0:.1f}% ({churn}/{edits})")
    print("\n## Attrito (proxy di rework dal transcript, non correttezza)")
    if since is None:
        print(fr_line(fr["before"], edits_by_file["before"]))
    else:
        print(f"prima di {since.date()}: " + fr_line(fr["before"], edits_by_file["before"]))
        print(f"da {since.date()}:      " + fr_line(fr["after"], edits_by_file["after"]))

    # Costo per turno per profondità (eq): il cache read al turno N dipende
    # da N; confrontare periodi con mix di profondità diverso senza
    # standardizzare confonde efficienza e lunghezza delle sessioni.
    if main_turns:
        if sub_costs:
            import bisect
            main_turns.sort(key=lambda t: t[0])
            keys = [t[0] for t in main_turns]
            for sts, seq in sub_costs:
                i = bisect.bisect_right(keys, sts) - 1
                if i >= 0:
                    main_turns[i][1] += seq
        print("\n## Costo per turno per profondità (eq; subagent attribuiti al turno che li lancia)")
        by = {}
        for _, eq, band, period in main_turns:
            by.setdefault((period, band), []).append(eq)
        periods = ["before", "after"] if since is not None else ["before"]
        head = f"{'banda':<9}" + "".join(f"{'turni':>8}{'eq/turno':>12}" for _ in periods)
        if since is not None:
            head += "   (prima | da " + str(since.date()) + ")"
        print(head)
        for band in BANDS:
            cells = []
            for pr in periods:
                v = by.get((pr, band), [])
                cells.append(f"{len(v):>8}{(fmt(int(sum(v) / len(v))) if v else '-'):>12}")
            print(f"{band:<9}" + "".join(cells))
        raw = {pr: [t[1] for t in main_turns if t[3] == pr] for pr in periods}
        for pr in periods:
            if raw[pr]:
                print(f"{'media ' + ('prima' if pr == 'before' else 'dopo'):<9}{len(raw[pr]):>8}"
                      f"{fmt(int(sum(raw[pr]) / len(raw[pr]))):>12}")
        if since is not None and raw["before"] and raw["after"]:
            # standardizzazione diretta: eq/turno del dopo, ripesato sul mix
            # di profondità del prima (bande senza dati nel dopo: escluse dal peso)
            w = {b: len(by.get(("before", b), [])) for b in BANDS}
            num = den = 0
            for b in BANDS:
                a = by.get(("after", b), [])
                if a and w[b]:
                    num += w[b] * (sum(a) / len(a))
                    den += w[b]
            m_b = sum(raw["before"]) / len(raw["before"])
            m_a = sum(raw["after"]) / len(raw["after"])
            print(f"costo/turno grezzo: prima {fmt(int(m_b))}, dopo {fmt(int(m_a))} "
                  f"({(m_a / m_b - 1) * 100:+.1f}%)")
            if den:
                std = num / den
                print(f"standardizzato sul mix di profondità del prima: {fmt(int(std))} "
                      f"({(std / m_b - 1) * 100:+.1f}%) — bande con dati su entrambi i lati: "
                      f"{sum(1 for b in BANDS if by.get(('after', b)) and w[b])}/{len(BANDS)}")

    # Pre-budget: coi dati del budget file il confronto è scoped a
    # declared_at (come lo Stop hook); con --budget manuale resta sui totali.
    src = scoped if declared is not None else tot
    scope_s = " [scope: da declared_at]" if declared is not None else ""
    if declared is not None and scoped_no_ts:
        scope_s += f" ({scoped_no_ts} record senza timestamp esclusi)"
    if budget:
        actual = src["output_tokens"]
        ratio = actual / budget
        flag = "≥3× → POST-MORTEM DOVUTO" if ratio >= 3 else "sotto soglia 3×, ok"
        task_s = f" (task: {budget_task})" if budget_task else ""
        print(f"\npre-budget output{task_s}{scope_s}: {fmt(budget)}  "
              f"actual: {fmt(actual)}  ratio: {ratio:.1f}× — {flag}")
    if budget_input:
        actual_in = src["input_tokens"] + src["cache_creation_input_tokens"]
        ratio = actual_in / budget_input
        flag = "≥3× → POST-MORTEM DOVUTO" if ratio >= 3 else "sotto soglia 3×, ok"
        print(f"pre-budget input (fresh=input+cache_creation){scope_s}: "
              f"{fmt(budget_input)}  actual: {fmt(actual_in)}  ratio: {ratio:.1f}× — {flag}")


if __name__ == "__main__":
    main()
