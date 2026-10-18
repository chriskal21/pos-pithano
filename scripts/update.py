#!/usr/bin/env python3
"""
Ενημέρωση δεδομένων για το SamurAI bet — μόνο με δωρεάν πηγές, χωρίς κλειδιά.

Πηγές
  • ESPN (δημόσια JSON, ανεπίσημα): ποδόσφαιρο (πρωταθλήματα + Champions/Europa/Conference League) και NBA
  • EuroLeague API (επίσημο): EuroLeague και EuroCup
  • Tennis Abstract: βαθμολογίες Elo τένις

Ροή
  1. Ενημερώνει την προσωρινή μνήμη αγώνων data/cache.json (την πρώτη φορά κατεβάζει ~15 μήνες,
     μετά μόνο τις τελευταίες και τις επόμενες 2 εβδομάδες).
  2. Υπολογίζει δυνάμεις ομάδων από όλους τους αγώνες, με μεγαλύτερο βάρος στους πρόσφατους.
     Στο ποδόσφαιρο όλα τα πρωταθλήματα και τα κύπελλα λύνονται μαζί, ώστε οι ευρωπαϊκοί αγώνες
     να «γεφυρώνουν» τα πρωταθλήματα.
  3. Γράφει site/data.json (επόμενοι αγώνες) και site/history.json (προβλέψεις και αποτελέσματα).

Μεταβλητές περιβάλλοντος (μόνο για δοκιμές)
  OFFLINE_DIR   φάκελος με αποθηκευμένες απαντήσεις JSON αντί για internet
  NOW           ημερομηνία-ώρα ISO που παίζει τον ρόλο του «τώρα»
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from html.parser import HTMLParser

ROOT = pathlib.Path(__file__).resolve().parents[1]
SITE, DATA = ROOT / "site", ROOT / "data"
OFFLINE = os.environ.get("OFFLINE_DIR")
NOW = (dt.datetime.fromisoformat(os.environ["NOW"].replace("Z", "+00:00"))
       if os.environ.get("NOW") else dt.datetime.now(dt.timezone.utc))
UTC = dt.timezone.utc
START = time.monotonic()
FETCH_BUDGET = 75 * 60    # δευτερόλεπτα: μετά από τόσο, τα παλιά (ιστορικά) δεδομένα συνεχίζουν στην επόμενη εκτέλεση

# ------------------------------------------------------------------ τι εμφανίζεται
# show=False: ο αγώνας χρησιμοποιείται μόνο για τις δυνάμεις (π.χ. ομάδες που παίζουν στα κύπελλα)
FOOTBALL = [
    dict(slug="gre.1", name="Super League", cc="GR"),
    dict(slug="eng.1", name="Premier League", cc="EN"),
    dict(slug="esp.1", name="La Liga", cc="ES"),
    dict(slug="ita.1", name="Serie A", cc="IT"),
    dict(slug="ger.1", name="Bundesliga", cc="DE"),
    dict(slug="fra.1", name="Ligue 1", cc="FR"),
    dict(slug="ned.1", name="Eredivisie", cc="NL"),
    dict(slug="por.1", name="Primeira Liga", cc="PT"),
    dict(slug="tur.1", name="Süper Lig", cc="TR"),
    dict(slug="bel.1", name="Pro League", cc="BE"),
    dict(slug="sco.1", name="Premiership", cc="SC"),
    dict(slug="uefa.champions", name="Champions League", cc="UCL", cup=True),
    dict(slug="uefa.europa", name="Europa League", cc="UEL", cup=True),
    dict(slug="uefa.europa.conf", name="Conference League", cc="UECL", cup=True),
    # μόνο για δυνάμεις
    *[dict(slug=s, name=s, cc="", show=False, cup=True) for s in
      ["uefa.champions_qual", "uefa.europa_qual", "uefa.europa.conf_qual"]],
    *[dict(slug=s, name=s, cc="", show=False) for s in
      ["eng.2", "aut.1", "sui.1", "den.1", "nor.1", "swe.1", "pol.1", "cze.1", "cro.1", "srb.1",
       "cyp.1", "isr.1", "rou.1", "ukr.1", "hun.1", "svk.1", "svn.1", "bul.1"]],
]
BASKETBALL = [
    dict(kind="euroleague", code="E", name="EuroLeague", cc="EL", sd=11.5),
    dict(kind="euroleague", code="U", name="EuroCup", cc="EC", sd=11.5),
    dict(kind="espn", slug="nba", name="NBA", cc="US", sd=12.5),
]
ORDER = [l["name"] for l in FOOTBALL if l.get("show", True)] + [l["name"] for l in BASKETBALL]

HORIZON_DAYS = 8          # πόσες μέρες μπροστά δείχνει η σελίδα
FALLBACK_DAYS = 21        # αν δεν υπάρχει αγώνας στις 8 μέρες, ψάχνει ως εδώ (π.χ. διακοπή εθνικών ομάδων)
BACKFILL_DAYS = 330       # πόσο πίσω κοιτάει την πρώτη φορά (η πρώτη εκτέλεση κρατά ~20–30 λεπτά)
HALF_LIFE = 150           # σε μέρες: ένας αγώνας πριν από 5 μήνες μετράει τα μισά
RHO = -0.10               # διόρθωση Dixon–Coles
FB_PRIOR = 4.0            # βάρος (σε αγώνες) της αφετηρίας κάθε ομάδας
BB_PRIOR = 6.0

GREEK_NAMES = {
    "olympiacos": "Ολυμπιακός", "olympiakos": "Ολυμπιακός", "panathinaikos": "Παναθηναϊκός",
    "aek athens": "ΑΕΚ", "aek": "ΑΕΚ", "paok": "ΠΑΟΚ", "paok salonika": "ΠΑΟΚ", "aris": "Άρης",
    "aris thessaloniki": "Άρης", "ofi crete": "ΟΦΗ", "ofi": "ΟΦΗ", "volos nfc": "Βόλος", "volos": "Βόλος",
    "levadiakos": "Λεβαδειακός", "asteras tripoli": "Αστέρας Τρίπολης", "asteras tripolis": "Αστέρας Τρίπολης",
    "atromitos": "Ατρόμητος", "panetolikos": "Παναιτωλικός", "panaitolikos": "Παναιτωλικός",
    "kifisia": "Κηφισιά", "ae kifisia": "Κηφισιά", "kalamata": "Καλαμάτα", "iraklis": "Ηρακλής",
    "lamia": "Λαμία", "panserraikos": "Πανσερραϊκός", "athens kallithea": "Καλλιθέα", "larissa": "ΑΕΛ",
    "ael larissa": "ΑΕΛ", "pas giannina": "ΠΑΣ Γιάννινα",
    "olympiacos piraeus": "Ολυμπιακός", "panathinaikos aktor athens": "Παναθηναϊκός",
    "aek betsson bc": "ΑΕΚ", "aris midea thessaloniki": "Άρης", "paok mateco thessaloniki": "ΠΑΟΚ",
}


def greek(name: str) -> str:
    k = re.sub(r"\b(fc|bc|b\.c\.)\b", "", name.lower()).strip()
    return GREEK_NAMES.get(k) or GREEK_NAMES.get(re.sub(r"\s+", " ", k)) or name


# ------------------------------------------------------------------ λήψη
class FetchError(Exception):
    pass


def get_json(url: str, pause: float = 0.25):
    if OFFLINE:
        key = re.sub(r"[^A-Za-z0-9]+", "_", url.split("://", 1)[1])[:180] + ".json"
        p = pathlib.Path(OFFLINE) / key
        if not p.exists():
            raise FetchError(f"offline: λείπει {key}")
        return json.loads(p.read_text())
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (samurai-bet; +github pages)",
                                                       "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=40) as r:
                body = r.read().decode("utf-8")
            time.sleep(pause)
            return json.loads(body)
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (400, 404):
                break
            time.sleep(10 * (attempt + 1))
        except Exception as e:
            last = e
            time.sleep(5 * (attempt + 1))
    raise FetchError(f"{url}: {last}")


def parse_dt(s: str) -> dt.datetime | None:
    if not s:
        return None
    s = s.strip().replace(" ", "T").replace("Z", "+00:00")
    if re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d", s):
        s += ":00+00:00"
    if not re.search(r"[+-]\d\d:\d\d$", s):
        s += "+00:00"
    try:
        return dt.datetime.fromisoformat(s)
    except ValueError:
        return None


# ---------------------------------------------------- ESPN
# Το ESPN απαντά αξιόπιστα μόνο σε ερωτήματα για ΜΙΑ μέρα (τα εύρη ημερομηνιών απορρίπτονται).
# Γι' αυτό: 1) διαβάζουμε το «ημερολόγιο» της σεζόν (ποιες μέρες έχουν αγώνες),
#           2) ρωτάμε μόνο αυτές τις μέρες, και 3) όσες έχουν κλείσει δεν τις ξαναρωτάμε ποτέ.
ESPN = "https://site.api.espn.com/apis/site/v2/sports/{sport}/{slug}/scoreboard"


def espn_board(sport: str, slug: str, day: dt.date | None = None) -> dict:
    url = ESPN.format(sport=sport, slug=slug) + (f"?dates={day:%Y%m%d}" if day else "")
    return get_json(url, pause=0.15)


def parse_events(board: dict, sport: str, slug: str) -> list[dict]:
    out = []
    for ev in board.get("events") or []:
        try:
            comp = ev["competitions"][0]
            teams = {c["homeAway"]: c for c in comp["competitors"]}
            h, a = teams["home"], teams["away"]
            st = (comp.get("status") or ev.get("status") or {}).get("type") or {}
            name = (st.get("name") or "").upper()
            when = parse_dt(comp.get("date") or ev.get("date"))
            if not when:
                continue
            season_type = (ev.get("season") or {}).get("type")
            out.append(dict(
                id=f"{slug}:{ev['id']}", league=slug, when=when.isoformat(),
                home=str(h["team"]["id"]), away=str(a["team"]["id"]),
                hname=h["team"].get("displayName") or h["team"].get("name"),
                aname=a["team"].get("displayName") or a["team"].get("name"),
                hs=int(h["score"]) if st.get("completed") and str(h.get("score", "")).isdigit() else None,
                as_=int(a["score"]) if st.get("completed") and str(a.get("score", "")).isdigit() else None,
                done=bool(st.get("completed")),
                off=any(x in name for x in ("POSTPONED", "CANCELED", "CANCELLED", "ABANDONED", "SUSPENDED")),
                neutral=bool(comp.get("neutralSite")),
                pre=(sport == "basketball" and season_type == 1),     # φιλικά προετοιμασίας NBA
            ))
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return out


def calendar_days(board: dict, cup: bool = False) -> set[str]:
    """Οι μέρες (YYYY-MM-DD) με αγώνες, από το πεδίο calendar της απάντησης."""
    days = set()

    def add(s):
        d = parse_dt(s) if isinstance(s, str) else None
        if d:
            days.add(d.date().isoformat())

    for lg in board.get("leagues") or []:
        for c in lg.get("calendar") or []:
            if isinstance(c, str):
                add(c)
            elif isinstance(c, dict):
                items = c.get("entries") or [c]
                for it in items:
                    a, b = parse_dt(it.get("startDate") or ""), parse_dt(it.get("endDate") or "")
                    if a and b and (b - a).days <= 370:
                        long_range = (b - a).days > 21
                        d = a.date()
                        while d <= b.date():
                            # Μεγάλη περίοδος χωρίς λεπτομέρειες (π.χ. «League Phase Σεπ–Ιαν» ή «Regular Season»):
                            # ρωτάμε μόνο τις μέρες που παίζονται συνήθως αγώνες.
                            if not long_range or (d.weekday() in (1, 2, 3) if cup else d.weekday() != 3):
                                days.add(d.isoformat())
                            d += dt.timedelta(days=1)
                    elif a:
                        days.add(a.date().isoformat())
    return days


def refresh_espn(cache: dict, sport: str, slug: str, show: bool = True) -> None:
    """Γεμίζει/ανανεώνει cache['leagues'][slug]. Ρίχνει FetchError μόνο αν δεν απαντά καθόλου η διοργάνωση."""
    lc = cache["leagues"].setdefault(slug, {})
    lc.setdefault("events", {}); lc.setdefault("final", []); lc.setdefault("prevcal", None)
    lc.pop("months", None)
    today = NOW.date()

    board = espn_board(sport, slug)                       # τρέχουσα μέρα + ημερολόγιο τρέχουσας σεζόν
    for e in parse_events(board, sport, slug):
        lc["events"][e["id"]] = e
    cup = slug.startswith("uefa")
    days = calendar_days(board, cup)
    if lc.get("calver") != 2:                            # νέος τρόπος ανάγνωσης ημερολογίου: ξαναδιάβασέ το
        lc["prevcal"], lc["calver"] = None, 2
    if lc["prevcal"] is None:                            # ημερολόγιο περσινής σεζόν, μία φορά
        try:
            lc["prevcal"] = sorted(calendar_days(espn_board(sport, slug, today - dt.timedelta(days=300)), cup))
        except FetchError:
            lc["prevcal"] = []
    days |= set(lc["prevcal"])
    if not days:                                          # χωρίς ημερολόγιο: κοίτα απλώς γύρω από σήμερα
        days = {(today + dt.timedelta(days=k)).isoformat() for k in range(-3, 11)}

    lo = (today - dt.timedelta(days=BACKFILL_DAYS + (100 if slug.startswith("uefa") else 0))).isoformat()
    hi = (today + dt.timedelta(days=FALLBACK_DAYS if show else 0)).isoformat()
    final = set(lc["final"])
    todo = sorted((d for d in days if lo <= d <= hi and d not in final), reverse=True)   # πρώτα τα πρόσφατα
    fails = 0
    for d in todo:
        day = dt.date.fromisoformat(d)
        if day < today - dt.timedelta(days=3) and time.monotonic() - START > FETCH_BUDGET:
            break                                         # τα υπόλοιπα ιστορικά στην επόμενη εκτέλεση
        try:
            evs = parse_events(espn_board(sport, slug, day), sport, slug)
        except FetchError as e:
            fails += 1
            if fails == 1:
                print(f"    ({slug} {d}: {e})")
            if fails >= 8:
                print(f"    {slug}: πολλές αποτυχίες, συνεχίζω στην επόμενη εκτέλεση")
                break
            continue
        fails = 0
        for e in evs:
            lc["events"][e["id"]] = e
        if day < today - dt.timedelta(days=2):          # τα αποτελέσματα δεν αλλάζουν πια
            final.add(d)

    cutoff = (NOW - dt.timedelta(days=BACKFILL_DAYS + 130)).isoformat()
    lc["events"] = {k: v for k, v in lc["events"].items() if v["when"] >= cutoff}
    lc["final"] = sorted(x for x in final if x >= cutoff[:10])


# ---------------------------------------------------- EuroLeague API
EL = "https://api-live.euroleague.net/v2/competitions/{c}/seasons/{c}{y}/games"


def euroleague_events(code: str, year: int) -> list[dict]:
    data = get_json(EL.format(c=code, y=year))
    games = data.get("data") if isinstance(data, dict) else data
    out = []
    for g in games or []:
        try:
            lo, ro = g["local"], g["road"]
            when = parse_dt(g.get("utcDate") or g.get("date"))
            played = bool(g.get("played"))
            out.append(dict(
                id=f"{code}{year}:{g.get('gameCode') or g.get('identifier') or g.get('id')}", league=code,
                when=when.isoformat(), home=lo["club"]["code"], away=ro["club"]["code"],
                hname=lo["club"].get("name") or lo["club"]["code"], aname=ro["club"].get("name") or ro["club"]["code"],
                hs=int(lo["score"]) if played else None, as_=int(ro["score"]) if played else None,
                done=played, off=False, neutral=False, pre=False))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return out


def refresh_euroleague(cache: dict, code: str) -> None:
    lc = cache["leagues"].setdefault(code, {"months": [], "events": {}})
    y = NOW.year if NOW.month >= 8 else NOW.year - 1
    seasons = [y] + ([y - 1] if f"{code}{y - 1}" not in lc["months"] else [])
    for yy in seasons:
        evs = euroleague_events(code, yy)
        for e in evs:
            lc["events"][e["id"]] = e
        if yy < y and evs:
            lc["months"].append(f"{code}{yy}")   # η περσινή σεζόν δεν αλλάζει, φτάνει μία λήψη


# ------------------------------------------------------------------ μοντέλα
def weight(when: str) -> float:
    days = (NOW - dt.datetime.fromisoformat(when)).total_seconds() / 86400
    return 0.5 ** (max(days, 0) / HALF_LIFE)


# Επίπεδο κάθε πρωταθλήματος, από τον μέσο όρο βαθμολογίας Elo των ομάδων του (περίπου, όπως τον δίνουν
# οι γνωστές κατατάξεις συλλόγων), με κλίμακα 0,0038 ανά βαθμό Elo. Την κλίμακα την επιβεβαιώνουν τα δεδομένα:
# οι ομάδες που ανεβοκατεβαίνουν ανάμεσα σε Premier League και Championship δίνουν διαφορά ~0,8, όσο και ο πίνακας.
# Είναι μόνο η αφετηρία: όπου υπάρχουν ευρωπαϊκά παιχνίδια, τα αποτελέσματα τη διορθώνουν. Χωρίς αυτήν κάθε
# πρωτάθλημα θα ήταν «νησί» και η πρώτη της Super League θα έβγαινε ισάξια με την πρώτη της Premier League.
LEAGUE_ELO = {
    "eng.1": 1800, "esp.1": 1745, "ita.1": 1730, "ger.1": 1720, "fra.1": 1665,
    "eng.2": 1600, "ned.1": 1585, "por.1": 1580, "bel.1": 1565, "tur.1": 1540,
    "cze.1": 1500, "aut.1": 1500, "den.1": 1500, "sui.1": 1490, "gre.1": 1470,
    "cro.1": 1450, "pol.1": 1450, "ukr.1": 1450, "sco.1": 1440, "nor.1": 1440,
    "swe.1": 1430, "srb.1": 1400, "isr.1": 1400, "rou.1": 1400, "hun.1": 1400,
    "cyp.1": 1390, "svk.1": 1370, "bul.1": 1360, "svn.1": 1340,
}
ELO_SCALE = 0.0038
LEAGUE_LEVEL = {k: round(ELO_SCALE * (v - 1720), 3) for k, v in LEAGUE_ELO.items()}
DEFAULT_LEVEL = round(ELO_SCALE * (1400 - 1720), 3)   # ομάδα που τη βλέπουμε μόνο σε κύπελλο
HOME_PRIOR = 60.0          # πόσο «τραβιέται» η έδρα κάθε διοργάνωσης προς τη συνολική (σε γκολ)


def fit_football(games: list[dict], team_league: dict, weight_fn=None, iters: int = 120):
    """Dixon–Coles/Poisson με επίθεση και άμυνα ανά ομάδα, πλεονέκτημα έδρας ανά διοργάνωση και
    αφετηρία ανά πρωτάθλημα. Όλες οι διοργανώσεις λύνονται μαζί."""
    weight_fn = weight_fn or weight
    teams = {g["home"] for g in games} | {g["away"] for g in games}
    lvl = {t: LEAGUE_LEVEL.get(team_league.get(t), DEFAULT_LEVEL) for t in teams}
    prior_a = {t: math.exp(lvl[t] / 2) for t in teams}
    prior_d = {t: math.exp(-lvl[t] / 2) for t in teams}
    att, dfn = dict(prior_a), dict(prior_d)
    mu, hg = 1.3, 1.25
    home = {}
    W = [(g, weight_fn(g["when"])) for g in games]
    for _ in range(iters):
        sg = defaultdict(float); se = defaultdict(float); cg = defaultdict(float); ce = defaultdict(float)
        for g, w in W:
            h, a = g["home"], g["away"]
            hf = 1.0 if g["neutral"] else home.get(g["league"], hg)
            eh = mu * hf * att[h] * dfn[a]
            ea = mu * att[a] * dfn[h]
            sg[h] += w * g["hs"]; se[h] += w * eh / att[h]
            sg[a] += w * g["as_"]; se[a] += w * ea / att[a]
            cg[a] += w * g["hs"]; ce[a] += w * eh / dfn[a]
            cg[h] += w * g["as_"]; ce[h] += w * ea / dfn[h]
        for t in teams:
            att[t] = (sg[t] + FB_PRIOR * prior_a[t]) / (se[t] + FB_PRIOR)
            dfn[t] = (cg[t] + FB_PRIOR * prior_d[t]) / (ce[t] + FB_PRIOR)
        nh = defaultdict(float); dh = defaultdict(float)
        for g, w in W:
            if not g["neutral"]:
                nh[g["league"]] += w * g["hs"]; dh[g["league"]] += w * mu * att[g["home"]] * dfn[g["away"]]
        hg = sum(nh.values()) / sum(dh.values()) if dh else hg
        home = {L: (nh[L] + HOME_PRIOR * hg) / (dh[L] + HOME_PRIOR) for L in dh}
        num = den = 0.0
        for g, w in W:
            hf = 1.0 if g["neutral"] else home.get(g["league"], hg)
            num += w * (g["hs"] + g["as_"])
            den += w * (hf * att[g["home"]] * dfn[g["away"]] + att[g["away"]] * dfn[g["home"]])
        mu = num / den if den else mu
    home["_all"] = hg
    return mu, home, att, dfn


def fit_basketball(games: list[dict]):
    """Πόντοι = μέσος όρος + επίθεση + άμυνα αντιπάλου ± έδρα/2 (με χρονική απόσβεση)."""
    teams = {g["home"] for g in games} | {g["away"] for g in games}
    off = {t: 0.0 for t in teams}; dfn = {t: 0.0 for t in teams}
    W = [(g, weight(g["when"])) for g in games]
    tw = sum(w for _, w in W) or 1
    L = sum(w * (g["hs"] + g["as_"]) / 2 for g, w in W) / tw
    hca = sum(w * (g["hs"] - g["as_"]) for g, w in W) / tw
    hca = min(4.5, max(1.5, hca))
    for _ in range(60):
        so = defaultdict(float); sd_ = defaultdict(float); n = defaultdict(float)
        for g, w in W:
            h, a = g["home"], g["away"]
            so[h] += w * (g["hs"] - L - hca / 2 - dfn[a]); so[a] += w * (g["as_"] - L + hca / 2 - dfn[h])
            sd_[a] += w * (g["hs"] - L - hca / 2 - off[h]); sd_[h] += w * (g["as_"] - L + hca / 2 - off[a])
            n[h] += w; n[a] += w
        for t in teams:
            off[t] = so[t] / (n[t] + BB_PRIOR); dfn[t] = sd_[t] / (n[t] + BB_PRIOR)
    return L, hca, off, dfn


def poisson(l, k):
    return math.exp(-l) * l ** k / math.factorial(k)


def fb_probs(lh, la):
    M, tot = {}, 0.0
    for i in range(11):
        for j in range(11):
            tau = 1.0
            if i == 0 and j == 0: tau = 1 - lh * la * RHO
            elif i == 0 and j == 1: tau = 1 + lh * RHO
            elif i == 1 and j == 0: tau = 1 + la * RHO
            elif i == 1 and j == 1: tau = 1 - RHO
            v = poisson(lh, i) * poisson(la, j) * tau; M[i, j] = v; tot += v
    H = sum(v for (i, j), v in M.items() if i > j) / tot
    D = sum(v for (i, j), v in M.items() if i == j) / tot
    O = sum(v for (i, j), v in M.items() if i + j > 2.5) / tot
    return dict(H=round(H, 4), D=round(D, 4), A=round(1 - H - D, 4), O25=round(O, 4))


def bb_probs(eh, ea, sd):
    m = eh - ea
    ph = 1 - 0.5 * (1 + math.erf((0.5 - m) / sd / math.sqrt(2)))
    pa = 0.5 * (1 + math.erf((-0.5 - m) / sd / math.sqrt(2)))
    return dict(H=round(ph / (ph + pa), 4), A=round(pa / (ph + pa), 4))


# ------------------------------------------------------------------ τένις
class _Table(HTMLParser):
    def __init__(self):
        super().__init__(); self.rows, self._r, self._c = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "tr": self._r = []
        elif tag in ("td", "th") and self._r is not None: self._c = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._r is not None and self._c is not None:
            self._r.append(" ".join("".join(self._c).split())); self._c = None
        elif tag == "tr" and self._r is not None:
            if self._r: self.rows.append(self._r)
            self._r = None

    def handle_data(self, d):
        if self._c is not None: self._c.append(d)


def update_elo(previous):
    if OFFLINE:
        return previous
    try:
        req = urllib.request.Request("https://tennisabstract.com/reports/atp_elo_ratings.html",
                                     headers={"User-Agent": "Mozilla/5.0 (samurai-bet)"})
        html = urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "replace")
        p = _Table(); p.feed(html)
        head = next(r for r in p.rows if "Player" in r and "Elo" in r)
        ix = {k: head.index(k) for k in ("Player", "Elo", "hElo", "cElo", "gElo")}
        players = []
        for r in p.rows[p.rows.index(head) + 1:]:
            try:
                players.append(dict(n=r[ix["Player"]].replace("\xa0", " "), e=float(r[ix["Elo"]]), h=float(r[ix["hElo"]]),
                                    c=float(r[ix["cElo"]]), g=float(r[ix["gElo"]])))
            except (ValueError, IndexError):
                continue
            if len(players) == 60:
                break
        if len(players) < 20:
            raise ValueError("λίγοι παίκτες")
        m = re.search(r"Last update:\s*(\d{4}-\d\d-\d\d)", html)
        return dict(tour="ATP", asOf=m[1] if m else NOW.date().isoformat(), source="Tennis Abstract", players=players)
    except Exception as e:
        print(f"  ! Elo τένις: κράτησα τα προηγούμενα ({e})")
        return previous


# ------------------------------------------------------------------ κύρια ροή
def upcoming_of(events):
    soon = [e for e in events if not e["done"] and not e["off"] and not e.get("pre")
            and NOW - dt.timedelta(hours=2) < dt.datetime.fromisoformat(e["when"]) < NOW + dt.timedelta(days=HORIZON_DAYS)]
    if soon:
        return sorted(soon, key=lambda e: e["when"])
    later = [e for e in events if not e["done"] and not e["off"] and not e.get("pre")
             and NOW < dt.datetime.fromisoformat(e["when"]) < NOW + dt.timedelta(days=FALLBACK_DAYS)]
    return sorted(later, key=lambda e: e["when"])[:12]


def played_count(events, teams):
    """Αγώνες ανά ομάδα στη φετινή σεζόν (από το τελευταίο κενό > 35 ημερών, δηλαδή τη θερινή διακοπή)."""
    done = sorted(e["when"] for e in events if e["done"])
    start = done[0] if done else NOW.isoformat()
    for a, b in zip(done, done[1:]):
        if (dt.datetime.fromisoformat(b) - dt.datetime.fromisoformat(a)).days > 35:
            start = b
    c = defaultdict(int)
    for e in events:
        if e["done"] and e["when"] >= start:
            c[e["home"]] += 1; c[e["away"]] += 1
    return max((c[t] for t in teams), default=0)



# ------------------------------------------------------------------ φόρμα, βαθμολογίες, προσομοίωση
EXTRA_NAMES = {
    "uefa.champions_qual": "Προκριματικά Champions League", "uefa.europa_qual": "Προκριματικά Europa League",
    "uefa.europa.conf_qual": "Προκριματικά Conference League", "eng.2": "Championship", "aut.1": "Αυστρία",
    "sui.1": "Ελβετία", "den.1": "Δανία", "nor.1": "Νορβηγία", "swe.1": "Σουηδία", "pol.1": "Πολωνία",
    "cze.1": "Τσεχία", "cro.1": "Κροατία", "srb.1": "Σερβία", "cyp.1": "Κύπρος", "isr.1": "Ισραήλ",
    "rou.1": "Ρουμανία", "ukr.1": "Ουκρανία", "hun.1": "Ουγγαρία", "svk.1": "Σλοβακία", "svn.1": "Σλοβενία",
    "bul.1": "Βουλγαρία", "E": "EuroLeague", "U": "EuroCup", "nba": "NBA",
}
# πρωταθλήματα με καθαρό διπλό γύρο (όλοι με όλους εντός-εκτός) στην κανονική διάρκεια: εκεί η προσομοίωση
# ξέρει ακριβώς ποιοι αγώνες απομένουν
SIM_LEAGUES = {"eng.1", "esp.1", "ita.1", "ger.1", "fra.1", "ned.1", "por.1", "tur.1", "gre.1", "bel.1"}
REGULAR_ONLY = {"gre.1": "Μόνο η κανονική διάρκεια: τα πλέι οφ δεν προσομοιώνονται.",
                "bel.1": "Μόνο η κανονική διάρκεια: τα πλέι οφ δεν προσομοιώνονται."}
SIMS = 5000


def league_label(slug):
    for l in FOOTBALL:
        if l["slug"] == slug and l.get("show", True):
            return l["name"]
    return EXTRA_NAMES.get(slug, slug)


def season_start(events):
    done = sorted(e["when"] for e in events if e["done"])
    start = done[0] if done else NOW.isoformat()
    for a, b in zip(done, done[1:]):
        if (dt.datetime.fromisoformat(b) - dt.datetime.fromisoformat(a)).days > 35:
            start = b
    return start


def by_team(games):
    idx = defaultdict(list)
    for g in sorted(games, key=lambda g: g["when"], reverse=True):
        idx[g["home"]].append(g); idx[g["away"]].append(g)
    return idx


def form_of(idx, t, name, n=5):
    out = []
    for g in idx.get(t, [])[:n]:
        home = g["home"] == t
        gf, ga = (g["hs"], g["as_"]) if home else (g["as_"], g["hs"])
        out.append(dict(d=g["when"][:10], o=name(g["away"] if home else g["home"]), h=home, gf=gf, ga=ga,
                        r="W" if gf > ga else "D" if gf == ga else "L", lg=league_label(g["league"])))
    return out


def stats_of(idx, t, n=10):
    gs = idx.get(t, [])[:n]
    if not gs:
        return None
    gf = sum(g["hs"] if g["home"] == t else g["as_"] for g in gs)
    ga = sum(g["as_"] if g["home"] == t else g["hs"] for g in gs)
    return dict(n=len(gs), gf=round(gf / len(gs), 2), ga=round(ga / len(gs), 2),
                o25=round(sum(g["hs"] + g["as_"] > 2.5 for g in gs) / len(gs), 2),
                btts=round(sum(g["hs"] > 0 and g["as_"] > 0 for g in gs) / len(gs), 2))


def h2h_of(games, a, b, name, n=6):
    pair = {a, b}
    gs = sorted((g for g in games if {g["home"], g["away"]} == pair), key=lambda g: g["when"], reverse=True)[:n]
    return [dict(d=g["when"][:10], h=name(g["home"]), a=name(g["away"]), hs=g["hs"], as_=g["as_"],
                 lg=league_label(g["league"])) for g in gs]


def standings(games):
    s = defaultdict(lambda: [0, 0, 0, 0, 0, 0, 0])   # Αγ, Ν, Ι, Η, ΓΥ, ΓΚ, Β
    for g in games:
        for t, gf, ga in ((g["home"], g["hs"], g["as_"]), (g["away"], g["as_"], g["hs"])):
            r = s[t]; r[0] += 1; r[4] += gf; r[5] += ga
            if gf > ga: r[1] += 1; r[6] += 3
            elif gf == ga: r[2] += 1; r[6] += 1
            else: r[3] += 1
    return s


def rank_order(s):
    return sorted(s, key=lambda t: (s[t][6], s[t][4] - s[t][5], s[t][4]), reverse=True)


def simulate(teams, season_games, lam, n=SIMS):
    """Monte Carlo της υπόλοιπης κανονικής διάρκειας. lam(h, a) -> (λ γηπεδούχου, λ φιλοξενούμενου)."""
    import random
    rnd = random.Random(20261019)
    teams = sorted(teams)
    ix = {t: i for i, t in enumerate(teams)}
    played = {(g["home"], g["away"]) for g in season_games}
    st = standings(season_games)
    base = [st[t][6] if t in st else 0 for t in teams]
    gd = [float(st[t][4] - st[t][5]) if t in st else 0.0 for t in teams]
    rem = []
    for h in teams:
        for a in teams:
            if h != a and (h, a) not in played:
                lh, la = lam(h, a)
                p = fb_probs(lh, la)
                rem.append((ix[h], ix[a], p["H"], p["H"] + p["D"]))
                gd[ix[h]] += lh - la; gd[ix[a]] += la - lh
    T = len(teams)
    top1 = [0] * T; top4 = [0] * T; bot3 = [0] * T; pts_sum = [0.0] * T
    for _ in range(n):
        pts = base[:]
        for i, j, ph, phd in rem:
            r = rnd.random()
            if r < ph: pts[i] += 3
            elif r < phd: pts[i] += 1; pts[j] += 1
            else: pts[j] += 3
        order = sorted(range(T), key=lambda k: (pts[k], gd[k] + rnd.random() * 2), reverse=True)
        top1[order[0]] += 1
        for k in order[:4]: top4[k] += 1
        for k in order[-3:]: bot3[k] += 1
        for k in range(T): pts_sum[k] += pts[k]
    return {t: dict(xp=round(pts_sum[i] / n, 1), p1=round(top1[i] / n, 4), p4=round(top4[i] / n, 4),
                    pb=round(bot3[i] / n, 4)) for t, i in ix.items()}, len(rem)


def backtest(fb_games, team_league, shown, weeks=10):
    """Αναδρομικός έλεγχος: για κάθε μία από τις τελευταίες εβδομάδες, το μοντέλο βλέπει μόνο τους
    προηγούμενους αγώνες και προβλέπει τους αγώνες της εβδομάδας."""
    items = []
    end = NOW - dt.timedelta(days=NOW.weekday(), hours=NOW.hour, minutes=NOW.minute)
    for k in range(weeks, 0, -1):
        ws, we = end - dt.timedelta(days=7 * k), end - dt.timedelta(days=7 * (k - 1))
        train = [g for g in fb_games if g["when"] < ws.isoformat()]
        test = [g for g in fb_games if ws.isoformat() <= g["when"] < we.isoformat() and g["league"] in shown]
        if len(train) < 500 or not test:
            continue
        wf = lambda w, ws=ws: 0.5 ** (max((ws - dt.datetime.fromisoformat(w)).total_seconds() / 86400, 0) / HALF_LIFE)
        mu, home, att, dfn = fit_football(train, team_league, weight_fn=wf, iters=60)
        d = (math.exp(DEFAULT_LEVEL / 2), math.exp(-DEFAULT_LEVEL / 2))
        for g in test:
            hf = 1.0 if g["neutral"] else home.get(g["league"], home["_all"])
            lh = mu * hf * att.get(g["home"], d[0]) * dfn.get(g["away"], d[1])
            la = mu * att.get(g["away"], d[0]) * dfn.get(g["home"], d[1])
            items.append(dict(kickoff=g["when"], league=league_label(g["league"]), sport="football",
                              hid=g["home"], aid=g["away"], p=fb_probs(lh, la), result=dict(home=g["hs"], away=g["as_"])))
    return items


def main() -> int:
    SITE.mkdir(exist_ok=True); DATA.mkdir(exist_ok=True)
    cache_p, data_p, hist_p = DATA / "cache.json", SITE / "data.json", SITE / "history.json"
    tables_p = SITE / "tables.json"
    cache = json.loads(cache_p.read_text()) if cache_p.exists() else {"leagues": {}}
    old = json.loads(data_p.read_text()) if data_p.exists() else {}
    history = json.loads(hist_p.read_text()) if hist_p.exists() else {"events": {}}
    hist = history.setdefault("events", {})
    problems = []

    # 1. λήψη (SKIP_FETCH=1: μόνο υπολογισμός με ό,τι υπάρχει ήδη, για δοκιμές)
    for lg in ([] if os.environ.get("SKIP_FETCH") else FOOTBALL):
        print(f"↓ {lg['slug']}", flush=True)
        try:
            refresh_espn(cache, "soccer", lg["slug"], show=lg.get("show", True))
        except FetchError as e:
            print(f"  ! {lg['slug']}: {e}")
            if lg.get("show", True): problems.append(lg["name"])
        cache_p.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")))   # πρόοδος
    for lg in ([] if os.environ.get("SKIP_FETCH") else BASKETBALL):
        try:
            if lg["kind"] == "espn":
                refresh_espn(cache, "basketball", lg["slug"])
            else:
                refresh_euroleague(cache, lg["code"])
        except FetchError as e:
            print(f"  ! {lg['name']}: {e}"); problems.append(lg["name"])
    cache_p.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")))

    names, matches, meta = {}, [], []
    for lc in cache["leagues"].values():
        for e in lc["events"].values():
            names[e["home"]] = e["hname"]; names[e["away"]] = e["aname"]

    # αποτελέσματα στο ιστορικό
    for lc in cache["leagues"].values():
        for e in lc["events"].values():
            h = hist.get(e["id"])
            if h and e["done"] and e["hs"] is not None and "result" not in h:
                h["result"] = dict(home=e["hs"], away=e["as_"])

    # 2. ποδόσφαιρο: ένα κοινό μοντέλο
    fb_games, team_league, greek_teams = [], {}, set()
    league_count = defaultdict(lambda: defaultdict(int))
    for lg in FOOTBALL:
        for e in cache["leagues"].get(lg["slug"], {}).get("events", {}).values():
            if e["done"] and e["hs"] is not None:
                fb_games.append(e)
                if not lg.get("cup"):
                    league_count[e["home"]][lg["slug"]] += 1; league_count[e["away"]][lg["slug"]] += 1
    for t, c in league_count.items():
        team_league[t] = max(c, key=c.get)
        if team_league[t] == "gre.1":
            greek_teams.add(t)
    fb_name = lambda t: greek(names.get(t, t)) if t in greek_teams else names.get(t, t)

    tables = []
    if fb_games:
        mu, home, att, dfn = fit_football(fb_games, team_league)
        print(f"• ποδόσφαιρο: {len(fb_games)} αγώνες στο μοντέλο, μ={mu:.2f}, έδρα×{home['_all']:.2f}")
        dflt = (math.exp(DEFAULT_LEVEL / 2), math.exp(-DEFAULT_LEVEL / 2))
        lam = lambda h, a, slug, neutral=False: (
            mu * (1.0 if neutral else home.get(slug, home["_all"])) * att.get(h, dflt[0]) * dfn.get(a, dflt[1]),
            mu * att.get(a, dflt[0]) * dfn.get(h, dflt[1]))
        fidx = by_team(fb_games)
        # βαθμολογίες και προσομοίωση
        pos = {}
        for lg in FOOTBALL:
            if not lg.get("show", True) or lg.get("cup"):
                continue
            evs = list(cache["leagues"].get(lg["slug"], {}).get("events", {}).values())
            start = season_start(evs)
            sg = [e for e in evs if e["done"] and e["hs"] is not None and e["when"] >= start]
            teams = {e["home"] for e in evs if e["when"] >= start} | {e["away"] for e in evs if e["when"] >= start}
            if not sg or len(teams) < 6:
                continue
            st = standings(sg)
            order = rank_order({t: st[t] for t in teams} if all(t in st for t in teams) else
                               {t: st.get(t, [0] * 7) for t in teams})
            sim, left = ({}, 0)
            if lg["slug"] in SIM_LEAGUES:
                sim, left = simulate(teams, sg, lambda h, a, s=lg["slug"]: lam(h, a, s))
            lidx = by_team(sg)
            rows = []
            for r, tm in enumerate(order, 1):
                v = st.get(tm, [0] * 7)
                rows.append(dict(t=fb_name(tm), p=v[0], w=v[1], d=v[2], l=v[3], gf=v[4], ga=v[5], pts=v[6],
                                 form="".join(x["r"] for x in form_of(lidx, tm, fb_name)), **sim.get(tm, {})))
                pos[tm] = dict(lg=lg["name"], rank=r, of=len(order), pts=v[6], p=v[0])
            note = REGULAR_ONLY.get(lg["slug"], "")
            if sim:
                note = (f"Από {SIMS:,} προσομοιώσεις των {left} αγώνων που απομένουν. ".replace(",", ".") + note).strip()
            tables.append(dict(name=lg["name"], cc=lg["cc"], sim=bool(sim), note=note, rows=rows))
        shown_slugs = {l["slug"] for l in FOOTBALL if l.get("show", True)}
        week = NOW.strftime("%G-W%V")
        if history.get("bt", {}).get("week") != week:
            print("• αναδρομικός έλεγχος…", flush=True)
            items = backtest(fb_games, team_league, shown_slugs)
            for it in items:
                it["home"], it["away"] = fb_name(it.pop("hid")), fb_name(it.pop("aid"))
            history["bt"] = dict(week=week, items=items)
            print(f"  {len(items)} αγώνες")
        for lg in FOOTBALL:
            if not lg.get("show", True):
                continue
            events = list(cache["leagues"].get(lg["slug"], {}).get("events", {}).values())
            ups = upcoming_of(events)
            if not ups:
                continue
            for e in ups:
                hf = 1.0 if e["neutral"] else home.get(lg["slug"], home["_all"])
                ah, dh = att.get(e["home"], dflt[0]), dfn.get(e["home"], dflt[1])
                aa, da = att.get(e["away"], dflt[0]), dfn.get(e["away"], dflt[1])
                lh, la = round(mu * hf * ah * da, 3), round(mu * aa * dh, 3)
                m = dict(id=e["id"], sport="football", league=lg["name"], country=lg["cc"], kickoff=e["when"],
                         home=fb_name(e["home"]), away=fb_name(e["away"]), lambdaHome=lh, lambdaAway=la,
                         info=dict(form=[form_of(fidx, e["home"], fb_name), form_of(fidx, e["away"], fb_name)],
                                   stats=[stats_of(fidx, e["home"]), stats_of(fidx, e["away"])],
                                   h2h=h2h_of(fb_games, e["home"], e["away"], fb_name),
                                   pos=[pos.get(e["home"]), pos.get(e["away"])],
                                   lvl=[[league_label(team_league[x]), LEAGUE_LEVEL.get(team_league[x])] if x in team_league else None
                                        for x in (e["home"], e["away"])]))
                matches.append(m)
                if dt.datetime.fromisoformat(e["when"]) > NOW:
                    hist[e["id"]] = dict(sport="football", league=lg["name"], kickoff=e["when"], home=m["home"],
                                         away=m["away"], p=fb_probs(lh, la))
            teams = {e["home"] for e in events} | {e["away"] for e in events}
            n = played_count(events, teams)
            note = ("Φάση ομίλων/πρωταθλήματος. Οι δυνάμεις βγαίνουν από τα πρωταθλήματα και τα ευρωπαϊκά παιχνίδια όλων των ομάδων."
                    if lg.get("cup") else (f"Μετά από {n} αγωνιστικές, μαζί με την περσινή σεζόν." if n else
                                           "Πριν από την πρεμιέρα: βάση η περσινή σεζόν."))
            meta.append(dict(name=lg["name"], sport="football", cc=lg["cc"], note=note))
            print(f"  {lg['name']}: {len(ups)} αγώνες")

    # 3. μπάσκετ: ένα μοντέλο ανά διοργάνωση
    for lg in BASKETBALL:
        key = lg.get("slug") or lg["code"]
        events = [e for e in cache["leagues"].get(key, {}).get("events", {}).values() if not e.get("pre")]
        games = [e for e in events if e["done"] and e["hs"] is not None]
        ups = upcoming_of(events)
        if not games or not ups:
            continue
        L, hca, off, dfn = fit_basketball(games)
        bidx = by_team(games)
        bname = lambda t: greek(names.get(t, t))
        for e in ups:
            eh = round(L + off.get(e["home"], -3) + dfn.get(e["away"], 3) + hca / 2, 1)
            ea = round(L + off.get(e["away"], -3) + dfn.get(e["home"], 3) - hca / 2, 1)
            hn, an = greek(names.get(e["home"], e["home"])), greek(names.get(e["away"], e["away"]))
            m = dict(id=e["id"], sport="basketball", league=lg["name"], country=lg["cc"], kickoff=e["when"],
                     home=hn, away=an, expHome=eh, expAway=ea, sd=lg["sd"],
                     info=dict(form=[form_of(bidx, e["home"], bname), form_of(bidx, e["away"], bname)],
                               stats=[stats_of(bidx, e["home"]), stats_of(bidx, e["away"])],
                               h2h=h2h_of(games, e["home"], e["away"], bname)))
            matches.append(m)
            if dt.datetime.fromisoformat(e["when"]) > NOW:
                hist[e["id"]] = dict(sport="basketball", league=lg["name"], kickoff=e["when"], home=hn, away=an,
                                     p=bb_probs(eh, ea, lg["sd"]))
        teams = {e["home"] for e in events} | {e["away"] for e in events}
        n = played_count(events, teams)
        meta.append(dict(name=lg["name"], sport="basketball", cc=lg["cc"],
                         note=(f"Μετά από {n} αγώνες ανά ομάδα, μαζί με την περσινή σεζόν." if n else
                               "Πριν από την πρεμιέρα: βάση η περσινή σεζόν.")))
        print(f"• {lg['name']}: {len(games)} αγώνες στο μοντέλο, {len(ups)} έρχονται")

    meta.sort(key=lambda l: ORDER.index(l["name"]))
    cutoff = (NOW - dt.timedelta(days=150)).isoformat()
    history["events"] = {k: v for k, v in hist.items() if v["kickoff"] > cutoff}
    history["updated"] = NOW.isoformat()
    hist_p.write_text(json.dumps(history, ensure_ascii=False, separators=(",", ":")))

    shown = len([l for l in FOOTBALL if l.get("show", True)]) + len(BASKETBALL)
    if len(problems) >= shown:
        print("! Απέτυχε η λήψη για όλες τις διοργανώσεις. Η σελίδα μένει όπως ήταν.")
        return 1
    data = dict(updated=NOW.isoformat(), matches=matches, leagues=meta, problems=problems,
                elo=update_elo(old.get("elo")))
    data_p.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    if tables:
        tables_p.write_text(json.dumps(dict(updated=NOW.isoformat(), leagues=tables), ensure_ascii=False, separators=(",", ":")))
    done = sum("result" in v for v in history["events"].values())
    print(f"✓ {len(matches)} αγώνες σε {len(meta)} διοργανώσεις · ιστορικό {len(history['events'])} ({done} με αποτέλεσμα)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
