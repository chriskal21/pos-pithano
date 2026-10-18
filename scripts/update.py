#!/usr/bin/env python3
"""
Ενημέρωση δεδομένων για το «Πόσο Πιθανό» — μόνο με δωρεάν πηγές, χωρίς κλειδιά.

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
BACKFILL_DAYS = 460       # πόσο πίσω κοιτάει την πρώτη φορά
HALF_LIFE = 180           # σε μέρες: ένας αγώνας πριν από 6 μήνες μετράει τα μισά
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
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (pos-pithano; +github pages)",
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
ESPN = "https://site.api.espn.com/apis/site/v2/sports/{sport}/{slug}/scoreboard?dates={a}-{b}&limit=1000"


def espn_events(sport: str, slug: str, start: dt.date, end: dt.date) -> list[dict]:
    url = ESPN.format(sport=sport, slug=slug, a=start.strftime("%Y%m%d"), b=end.strftime("%Y%m%d"))
    out = []
    for ev in get_json(url).get("events") or []:
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


def months(start: dt.date, end: dt.date):
    d = start.replace(day=1)
    while d <= end:
        nxt = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        yield d, min(nxt - dt.timedelta(days=1), end)
        d = nxt


def refresh_espn(cache: dict, sport: str, slug: str) -> None:
    """Γεμίζει/ανανεώνει cache['leagues'][slug] με αγώνες ESPN."""
    lc = cache["leagues"].setdefault(slug, {"months": [], "events": {}})
    today = NOW.date()
    jobs = []
    for a, b in months(today - dt.timedelta(days=BACKFILL_DAYS), today - dt.timedelta(days=15)):
        key = a.strftime("%Y-%m")
        if key not in lc["months"]:
            jobs.append((a, b, key))
    jobs.append((today - dt.timedelta(days=14), today + dt.timedelta(days=FALLBACK_DAYS), None))
    for a, b, key in jobs:
        evs = espn_events(sport, slug, a, b)
        for e in evs:
            lc["events"][e["id"]] = e
        if key and b < today - dt.timedelta(days=15):
            lc["months"].append(key)
    # καθάρισε ό,τι είναι πολύ παλιό
    cutoff = (NOW - dt.timedelta(days=BACKFILL_DAYS + 30)).isoformat()
    lc["events"] = {k: v for k, v in lc["events"].items() if v["when"] >= cutoff}
    lc["months"] = [m for m in lc["months"] if m >= cutoff[:7]]


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


def fit_football(games: list[dict], team_league: dict):
    """Poisson με επίθεση/άμυνα ανά ομάδα, κοινό για όλες τις διοργανώσεις."""
    teams = {g["home"] for g in games} | {g["away"] for g in games}
    att = {t: 1.0 for t in teams}; dfn = {t: 1.0 for t in teams}
    prior_a = {t: 1.0 for t in teams}; prior_d = {t: 1.0 for t in teams}
    mu, home = 1.3, 1.2
    W = [(g, weight(g["when"])) for g in games]
    for it in range(80):
        if it == 25:   # αφετηρία κάθε ομάδας = μέσος όρος του πρωταθλήματός της
            by = defaultdict(list)
            for t in teams:
                by[team_league.get(t)].append(t)
            for lg, ts in by.items():
                if lg is None:
                    continue
                ma = math.exp(sum(math.log(att[t]) for t in ts) / len(ts))
                md = math.exp(sum(math.log(dfn[t]) for t in ts) / len(ts))
                for t in ts:
                    prior_a[t], prior_d[t] = ma * 0.97, md * 1.03
        sg = defaultdict(float); se = defaultdict(float); cg = defaultdict(float); ce = defaultdict(float)
        for g, w in W:
            hf = 1.0 if g["neutral"] else home
            eh = mu * hf * att[g["home"]] * dfn[g["away"]]
            ea = mu * att[g["away"]] * dfn[g["home"]]
            sg[g["home"]] += w * g["hs"]; se[g["home"]] += w * eh / att[g["home"]]
            sg[g["away"]] += w * g["as_"]; se[g["away"]] += w * ea / att[g["away"]]
            cg[g["away"]] += w * g["hs"]; ce[g["away"]] += w * eh / dfn[g["away"]]
            cg[g["home"]] += w * g["as_"]; ce[g["home"]] += w * ea / dfn[g["home"]]
        for t in teams:
            att[t] = (sg[t] + FB_PRIOR * prior_a[t]) / (se[t] + FB_PRIOR)
            dfn[t] = (cg[t] + FB_PRIOR * prior_d[t]) / (ce[t] + FB_PRIOR)
        # κανονικοποίηση και κοινές παράμετροι
        la = sum(math.log(v) for v in att.values()) / len(att)
        ld = sum(math.log(v) for v in dfn.values()) / len(dfn)
        for t in teams:
            att[t] /= math.exp(la); dfn[t] /= math.exp(ld)
        num_h = den_h = num = den = 0.0
        for g, w in W:
            if not g["neutral"]:
                num_h += w * g["hs"]; den_h += w * mu * att[g["home"]] * dfn[g["away"]]
        home = num_h / den_h if den_h else home
        for g, w in W:
            hf = 1.0 if g["neutral"] else home
            num += w * (g["hs"] + g["as_"])
            den += w * (hf * att[g["home"]] * dfn[g["away"]] + att[g["away"]] * dfn[g["home"]])
        mu = num / den if den else mu
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
                                     headers={"User-Agent": "Mozilla/5.0 (pos-pithano)"})
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


def main() -> int:
    SITE.mkdir(exist_ok=True); DATA.mkdir(exist_ok=True)
    cache_p, data_p, hist_p = DATA / "cache.json", SITE / "data.json", SITE / "history.json"
    cache = json.loads(cache_p.read_text()) if cache_p.exists() else {"leagues": {}}
    old = json.loads(data_p.read_text()) if data_p.exists() else {}
    history = json.loads(hist_p.read_text()) if hist_p.exists() else {"events": {}}
    hist = history.setdefault("events", {})
    problems = []

    # 1. λήψη
    for lg in FOOTBALL:
        try:
            refresh_espn(cache, "soccer", lg["slug"])
        except FetchError as e:
            print(f"  ! {lg['slug']}: {e}")
            if lg.get("show", True): problems.append(lg["name"])
    for lg in BASKETBALL:
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

    if fb_games:
        mu, home, att, dfn = fit_football(fb_games, team_league)
        print(f"• ποδόσφαιρο: {len(fb_games)} αγώνες στο μοντέλο, μ={mu:.2f}, έδρα×{home:.2f}")
        for lg in FOOTBALL:
            if not lg.get("show", True):
                continue
            events = list(cache["leagues"].get(lg["slug"], {}).get("events", {}).values())
            ups = upcoming_of(events)
            if not ups:
                continue
            for e in ups:
                hf = 1.0 if e["neutral"] else home
                ah, dh = att.get(e["home"], 0.85), dfn.get(e["home"], 1.15)
                aa, da = att.get(e["away"], 0.85), dfn.get(e["away"], 1.15)
                lh, la = round(mu * hf * ah * da, 3), round(mu * aa * dh, 3)
                m = dict(id=e["id"], sport="football", league=lg["name"], country=lg["cc"], kickoff=e["when"],
                         home=fb_name(e["home"]), away=fb_name(e["away"]), lambdaHome=lh, lambdaAway=la)
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
        for e in ups:
            eh = round(L + off.get(e["home"], -3) + dfn.get(e["away"], 3) + hca / 2, 1)
            ea = round(L + off.get(e["away"], -3) + dfn.get(e["home"], 3) - hca / 2, 1)
            hn, an = greek(names.get(e["home"], e["home"])), greek(names.get(e["away"], e["away"]))
            m = dict(id=e["id"], sport="basketball", league=lg["name"], country=lg["cc"], kickoff=e["when"],
                     home=hn, away=an, expHome=eh, expAway=ea, sd=lg["sd"])
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
    done = sum("result" in v for v in history["events"].values())
    print(f"✓ {len(matches)} αγώνες σε {len(meta)} διοργανώσεις · ιστορικό {len(history['events'])} ({done} με αποτέλεσμα)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
