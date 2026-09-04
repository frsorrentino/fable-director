#!/usr/bin/env python3
"""Statusline PLAIN (default dalla 1.39): ogni segmento dice COSA FARE, non
cosa misura. Parole, non sigle; solo eccezioni; i numeri per esperti stanno
in /fable-director:status o nella modalita expert (la riga storica).

Test dei tre lettori, applicato a ogni riga: Franz, lo sviluppatore che ha
installato da GitHub senza mai parlarci, un collega junior. Se uno solo
dovrebbe cercarla nella legenda, la riga cambia o sparisce.

Input: i token gia calcolati da statusline-ctx.sh (env FD_SL_*). Output: una
riga in stato normale ("Fable 5.1 · quota 21%, resets 17:30 · context 26%"), le eccezioni in
ordine di priorita; piu di una eccezione = riga 2. Il colore non porta mai
informazione da solo: la parola c e sempre.
"""
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

GREY, YEL, RED, DIM, RST = ("\033[38;5;245m", "\033[38;5;220m",
                            "\033[38;5;196m", "\033[38;5;239m", "\033[0m")
SEP = f"{DIM} · {RST}"


def env(k):
    v = os.environ.get(k, "-")
    return "" if v in ("-", "") else v.replace(",", " ")


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def plain_len(s):
    s = re.sub(r"\x1b\][^\x07\x1b]*(\x07|\x1b\\)", "", s)
    return len(re.sub(r"\x1b\[[0-9;]*m", "", s))


def title(model):
    # "FABLE 5.1" → "Fable 5.1"; "OPUS 5" → "Opus 5"; "SONNET 4.6" → "Sonnet 4.6"
    return " ".join(w.capitalize() if w.isalpha() else w for w in model.split())


def minutes_until(hhmm):
    """Minuti al reset se rlt e un orario HH:MM di oggi/domani; None altrimenti."""
    m = re.match(r"^(\d{1,2}):(\d{2})$", hhmm or "")
    if not m:
        return None
    import time
    now = time.localtime()
    tgt = int(m.group(1)) * 60 + int(m.group(2))
    cur = now.tm_hour * 60 + now.tm_min
    d = tgt - cur
    return d + 1440 if d < 0 else d


def main():
    model = title(env("FD_SL_MODEL")) or "Claude"
    pct, rl, rlt, wk, wkt = num(env("FD_SL_PCT")), num(env("FD_SL_RL")), env("FD_SL_RLT"), num(env("FD_SL_WK")), env("FD_SL_WKT")
    bdg, eff, grind = env("FD_SL_BDG"), env("FD_SL_EFF"), num(env("FD_SL_GRIND"))
    xf, dlg, prn, pru = env("FD_SL_XF"), env("FD_SL_DLG"), env("FD_SL_PRN"), env("FD_SL_PRU")
    stuck, vrf, prio = num(env("FD_SL_STUCK")), env("FD_SL_VRF"), env("FD_SL_PRIO")
    badge = os.environ.get("FD_SL_BADGE", "")

    takeover = None          # testo rosso in testa (budget 3x, enforcement off)
    exc = []                 # (priorita, colore, testo) — piu basso = piu urgente
    normal = []              # stato tranquillo: (colore, testo), sempre in riga 1

    # --- budget --------------------------------------------------------------
    if bdg.startswith("r:") and "ENFORCEMENT OFF" in bdg:
        takeover = ("budget checks off (transcript unreadable) — update the plugin", RED)
    elif bdg.startswith("r:") and "3×" in bdg and "POST-MORTEM" in bdg:
        takeover = ("budget over 3× — post-mortem before closing", RED)
    elif bdg.startswith("r:"):
        m = re.search(r"(\d+(?:\.\d)?)×", bdg)
        takeover = (f"budget over {m.group(1) if m else '3'}× — post-mortem before closing", RED)
    elif bdg.startswith("y:"):
        m = re.search(r"(\d+(?:\.\d)?)×", bdg)
        exc.append((7, YEL, f"budget over {m.group(1) if m else '2'}× — reconsider the route"))

    # --- verify (E7) ---------------------------------------------------------
    if vrf and not vrf.startswith("0:"):
        rc, _, cmd = vrf.partition(":")
        what = "timed out (60 s)" if rc == "timeout" else f"exit {rc}"
        exc.append((2, RED, f"verification failed: {cmd.strip() or 'declared command'} ({what})"))

    # --- quota 5h: SEMPRE in riga 1 con il numero (colore per livello); da 80
    # anche l'allarme in riga 2 con il reset in minuti ------------------------
    if rl is not None:
        normal.append((RED if rl >= 80 else YEL if rl >= 60 else GREY,
                       f"quota {rl:.0f}%" + (f", resets {rlt}" if rlt else "")))
        if rl >= 80:
            mins = minutes_until(rlt)
            when = (f"resets in {mins} min" if mins is not None and mins < 120
                    else f"resets {rlt}" if rlt else "resets later")
            exc.append((3, RED, f"quota {rl:.0f}% used, {when}"))
    # --- quota settimanale ----------------------------------------------------
    if wk is not None:
        if wk >= 80:
            exc.append((8, RED, f"weekly quota {wk:.0f}% used" + (f", resets {wkt}" if wkt else "")))
        elif wk >= 60:
            exc.append((8, YEL, f"weekly quota {wk:.0f}% used" + (f", resets {wkt}" if wkt else "")))

    # --- contesto: SEMPRE in riga 1 (la percentuale e il margine), colore per
    # livello; da 80 anche la frase in riga 2. Mai in coda alle eccezioni:
    # li' era la voce meno urgente, la prima a cadere su terminale stretto.
    if pct is not None:
        normal.append((RED if pct >= 80 else YEL if pct >= 60 else GREY, f"context {pct:.0f}%"))
        if pct >= 80:
            exc.append((5, RED, f"context {pct:.0f}% full — finish the task and start a new session"))

    # --- agenti (E5) -----------------------------------------------------------
    m = re.match(r"⟲(\d+)", dlg)
    n_agents = int(m.group(1)) if m else 0
    if n_agents and stuck is not None and stuck >= 30:
        exc.append((6, YEL, f"{n_agents if n_agents > 1 else 1} agent{'s' if n_agents > 1 else ''} "
                    f"stuck for {stuck:.0f} min — check /tasks"))
    elif n_agents:
        normal.append((GREY, f"{n_agents} agent{'s' if n_agents != 1 else ''} working"))

    # --- precedenza di un altra sessione -------------------------------------
    if prio:
        exc.append((6, YEL, f"another session has priority (incident: {prio}) — new fan-outs wait"))

    # --- comandi falliti --------------------------------------------------------
    if grind is not None and grind >= 3:
        exc.append((9, RED, f"{grind:.0f} commands failed in a row — change approach"))

    # --- effort alto -------------------------------------------------------------
    if eff.startswith("y:"):
        lvl = eff.split("·")[-1]
        exc.append((10, YEL, f"effort {lvl} on"))

    # --- esterni ---------------------------------------------------------------
    if xf:
        cls, _, body = xf.partition(":")
        first = body.split()[0] if body else ""
        prov = re.sub(r"[▲×].*$", "", first)
        if "▲" in body:
            normal.append((GREY, f"{prov} working"))
        if cls in ("y", "r"):
            mm = re.search(r"(\d+)/(\d+)\s*(\S+)?", body)
            used = f" ({mm.group(1)}/{mm.group(2)})" if mm else ""
            when = f", resets {mm.group(3)}" if mm and mm.group(3) else ""
            exc.append((12, YEL if cls == "y" else RED,
                        f"{prov} free calls almost used up{used}{when}"))

    # --- PR ----------------------------------------------------------------------
    if prn:
        cls, _, txt = prn.partition(":")
        n = re.search(r"#\d+", txt)
        pr = n.group(0) if n else txt.strip()
        state = {"a": "approved", "c": ": changes requested", "p": " pending"}.get(cls, "")
        exc.append((13, YEL if cls == "c" else GREY,
                    f"PR {pr} {state}".replace("  ", " ").replace(" :", ":")))

    # --- composizione ------------------------------------------------------------
    exc.sort(key=lambda x: x[0])
    head = f"{GREY}{model}{RST}"
    line1_parts = [head] + [f"{c}{t}{RST}" for c, t in normal]
    if takeover:
        line1_parts = [f"{takeover[1]}{takeover[0]}{RST}"] + [f"{DIM}{model}{RST}"]
    line2_parts = [f"{c}{t}{RST}" for _, c, t in exc]

    W = os.environ.get("COLUMNS", "120")
    W = int(W) if W.isdigit() else 120
    # una sola eccezione: sta in riga 1 se ci entra
    if line2_parts and len(line2_parts) == 1 and not takeover:
        cand = SEP.join(line1_parts + line2_parts)
        if plain_len(cand) <= W:
            line1_parts, line2_parts = line1_parts + line2_parts, []
    line1 = SEP.join(line1_parts)
    if badge:
        cand = f"{line1}{DIM} │ {RST}{badge}"
        if plain_len(cand) <= W:
            line1 = cand
    while line2_parts and plain_len(SEP.join(line2_parts)) > W and len(line2_parts) > 1:
        line2_parts.pop()  # cade la meno urgente
    out = line1
    if line2_parts:
        out += "\n" + f"{DIM}└ {RST}" + SEP.join(line2_parts)
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.stdout.write("Claude")
        sys.exit(0)
