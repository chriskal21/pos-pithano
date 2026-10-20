"""Αξιολόγηση του μοντέλου ποδοσφαίρου σε όλη τη διαθέσιμη περίοδο (walk-forward ανά εβδομάδα).

Για κάθε εβδομάδα το μοντέλο εκπαιδεύεται μόνο με όσα είχαν παιχτεί πριν και προβλέπει τους αγώνες
της εβδομάδας. Μετρά:
  log-loss  : -ln(πιθανότητα που έδωσε στο αποτέλεσμα που βγήκε). Χαμηλότερο = καλύτερο.
              Τιμωρεί πολύ την υπερβολική σιγουριά.
  RPS       : ranked probability score για 1-Χ-2 (το συνηθισμένο μέτρο στη βιβλιογραφία). Χαμηλότερο = καλύτερο.
  hit       : ποσοστό αγώνων όπου βγήκε το πιθανότερο σημείο.
  ou        : log-loss για Over/Under 2,5.

Χρήση:
  python3 scripts/evaluate.py                       # τρέχουσες ρυθμίσεις
  python3 scripts/evaluate.py HALF_LIFE=120 RHO=-0.05 SHOT_BETA=0.3
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import pathlib
import sys
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import update as U  # noqa: E402

PARAMS = ("HALF_LIFE", "FB_PRIOR", "RHO", "HOME_PRIOR", "ELO_SCALE", "TOT_PRIOR", "MU_HALF", "SHOT_BETA")


def set_params(**kw):
    for k, v in kw.items():
        if k not in PARAMS:
            raise SystemExit(f"άγνωστη παράμετρος {k}")
        setattr(U, k, v)
    U.LEAGUE_LEVEL = {k: round(U.ELO_SCALE * (v - 1720), 3) for k, v in U.LEAGUE_ELO.items()}
    U.DEFAULT_LEVEL = round(U.ELO_SCALE * (1400 - 1720), 3)


def load_games(cache: dict):
    games, count = [], defaultdict(lambda: defaultdict(int))
    for lg in U.FOOTBALL:
        for e in cache["leagues"].get(lg["slug"], {}).get("events", {}).values():
            if e["done"] and e["hs"] is not None:
                games.append(e)
                if not lg.get("cup"):
                    count[e["home"]][lg["slug"]] += 1; count[e["away"]][lg["slug"]] += 1
    games.sort(key=lambda g: g["when"])
    return games, {t: max(c, key=c.get) for t, c in count.items()}


def walk_forward(games, team_league, start: str | None = None, min_train: int = 1500, iters: int = 60):
    """Επιστρέφει λίστα (αγώνας, πιθανότητες) για όλους τους αγώνες των εμφανιζόμενων διοργανώσεων."""
    shown = {l["slug"] for l in U.FOOTBALL if l.get("show", True)}
    first = dt.datetime.fromisoformat(start or games[0]["when"])
    last = dt.datetime.fromisoformat(games[-1]["when"])
    ws = first - dt.timedelta(days=first.weekday(), hours=first.hour, minutes=first.minute,
                              seconds=first.second, microseconds=first.microsecond)
    out = []
    while ws <= last:
        we = ws + dt.timedelta(days=7)
        a, b = ws.isoformat(), we.isoformat()
        train = [g for g in games if g["when"] < a]
        test = [g for g in games if a <= g["when"] < b and g["league"] in shown]
        if len(train) >= min_train and test:
            wf = lambda w, ws=ws: 0.5 ** (max((ws - dt.datetime.fromisoformat(w)).total_seconds() / 86400, 0) / U.HALF_LIFE)
            model = U.fit_football(train, team_league, weight_fn=wf, iters=iters)
            for g in test:
                lh, la = U.fb_lambda(model, g["home"], g["away"], g["league"], g["neutral"])
                out.append((g, U.fb_probs(lh, la)))
        ws = we
    return out


def score(preds):
    n = len(preds)
    if not n:
        return {}
    ll = rps = br = hit = ou = 0.0
    for g, p in preds:
        r = "H" if g["hs"] > g["as_"] else "D" if g["hs"] == g["as_"] else "A"
        q = [max(p["H"], 1e-6), max(p["D"], 1e-6), max(p["A"], 1e-6)]
        o = [r == "H", r == "D", r == "A"]
        ll -= math.log(q["HDA".index(r)])
        c1, c2 = q[0] - o[0], q[0] + q[1] - o[0] - o[1]
        rps += (c1 * c1 + c2 * c2) / 2
        br += sum((qi - oi) ** 2 for qi, oi in zip(q, o))
        hit += max(range(3), key=lambda i: q[i]) == "HDA".index(r)
        over = g["hs"] + g["as_"] > 2.5
        po = min(max(p["O25"], 1e-6), 1 - 1e-6)
        ou -= math.log(po if over else 1 - po)
    return dict(n=n, logloss=round(ll / n, 4), rps=round(rps / n, 4), brier=round(br / n, 4),
                hit=round(hit / n, 4), ou=round(ou / n, 4))


def baseline(preds):
    """Χωρίς μοντέλο: ίδιες πιθανότητες για όλους (οι συχνότητες 1-Χ-2 και Over της περιόδου)."""
    n = len(preds)
    H = sum(g["hs"] > g["as_"] for g, _ in preds) / n
    D = sum(g["hs"] == g["as_"] for g, _ in preds) / n
    O = sum(g["hs"] + g["as_"] > 2.5 for g, _ in preds) / n
    return score([(g, dict(H=H, D=D, A=1 - H - D, O25=O)) for g, _ in preds])


def main():
    kw = {}
    for a in sys.argv[1:]:
        k, v = a.split("=")
        kw[k] = float(v)
    set_params(**kw)
    path = pathlib.Path(os.environ.get("CACHE", U.DATA / "cache.json"))
    games, tl = load_games(json.loads(path.read_text()))
    preds = walk_forward(games, tl, start=os.environ.get("EVAL_START"))
    print(json.dumps(dict(params={k: getattr(U, k, None) for k in PARAMS}, model=score(preds),
                          baseline=baseline(preds)), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
