"""
Στατικές σελίδες για το Google και για κοινοποίηση στα social.

Κάθε εκτέλεση γράφει (μέσα στο site/, χωρίς να μπαίνουν στο αποθετήριο):
  agones/<ομάδα>-<ομάδα>-<ημερομηνία>.html   μία σελίδα ανά αγώνα (επερχόμενοι και όσοι παίχτηκαν τις τελευταίες 14 μέρες)
  protathlimata/<πρωτάθλημα>.html            «Προγνωστικά <πρωτάθλημα>» με αγώνες και βαθμολογία
  protathlimata/index.html                   λίστα πρωταθλημάτων
  og/<αγώνας>.png, og.png                    εικόνες προεπισκόπησης 1200×630 (αν υπάρχει το Pillow)
  sitemap.xml, robots.txt, static.css
"""
from __future__ import annotations

import datetime as dt
import html
import json
import math
import os
import pathlib
import re
import subprocess
import sys
import unicodedata

HERE = pathlib.Path(__file__).resolve().parent
ATHENS = None
try:
    from zoneinfo import ZoneInfo
    ATHENS = ZoneInfo("Europe/Athens")
except Exception:  # pragma: no cover
    ATHENS = dt.timezone(dt.timedelta(hours=3))

E = html.escape
DAYS = ["Δευτέρα", "Τρίτη", "Τετάρτη", "Πέμπτη", "Παρασκευή", "Σάββατο", "Κυριακή"]
MONTHS = ["Ιανουαρίου", "Φεβρουαρίου", "Μαρτίου", "Απριλίου", "Μαΐου", "Ιουνίου", "Ιουλίου", "Αυγούστου",
          "Σεπτεμβρίου", "Οκτωβρίου", "Νοεμβρίου", "Δεκεμβρίου"]
GR2LAT = {"α": "a", "β": "v", "γ": "g", "δ": "d", "ε": "e", "ζ": "z", "η": "i", "θ": "th", "ι": "i", "κ": "k",
          "λ": "l", "μ": "m", "ν": "n", "ξ": "x", "ο": "o", "π": "p", "ρ": "r", "σ": "s", "ς": "s", "τ": "t",
          "υ": "y", "φ": "f", "χ": "ch", "ψ": "ps", "ω": "o"}


# ------------------------------------------------------------------ βοηθητικά
def slug(text: str) -> str:
    s = unicodedata.normalize("NFD", text.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = "".join(GR2LAT.get(c, c) for c in s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "x"


def local(iso: str) -> dt.datetime:
    return dt.datetime.fromisoformat(iso).astimezone(ATHENS)


def when_text(iso: str) -> str:
    d = local(iso)
    return f"{DAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]}, {d:%H:%M}"


def match_slug(m: dict) -> str:
    return f"{slug(m['home'])}-{slug(m['away'])}-{local(m['kickoff']):%Y-%m-%d}"


def league_slug(name: str) -> str:
    return slug(name)


def pct(p: float) -> str:
    return f"{round(p * 100)}%"


def pz(p: float) -> str:
    return "–" if p < 0.005 else ">99%" if p > 0.995 else pct(p)


def fair(p: float) -> str:
    return f"{1 / p:.2f}".replace(".", ",") if p > 0.0005 else "–"


def dec(x: float, n: int = 1) -> str:
    return f"{x:.{n}f}".replace(".", ",")


def base_url(site: pathlib.Path) -> str:
    if os.environ.get("SITE_URL"):
        return os.environ["SITE_URL"].rstrip("/") + "/"
    cname = site / "CNAME"
    if cname.exists() and cname.read_text().strip():
        return f"https://{cname.read_text().strip()}/"
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repo:
        owner, name = repo.split("/", 1)
        if name.lower() == f"{owner.lower()}.github.io":
            return f"https://{name.lower()}/"
        return f"https://{owner.lower()}.github.io/{name}/"
    return ""


# ------------------------------------------------------------------ πιθανότητες (ίδιες με τη σελίδα)
RHO = -0.10


def football_probs(lh: float, la: float) -> dict:
    pois = lambda l, k: math.exp(-l) * l ** k / math.factorial(k)
    M, tot = {}, 0.0
    for i in range(11):
        for j in range(11):
            tau = 1.0
            if i == 0 and j == 0: tau = 1 - lh * la * RHO
            elif i == 0 and j == 1: tau = 1 + lh * RHO
            elif i == 1 and j == 0: tau = 1 + la * RHO
            elif i == 1 and j == 1: tau = 1 - RHO
            v = pois(lh, i) * pois(la, j) * tau
            M[i, j] = v; tot += v
    M = {k: v / tot for k, v in M.items()}
    H = sum(v for (i, j), v in M.items() if i > j)
    D = sum(v for (i, j), v in M.items() if i == j)
    top = max(M.items(), key=lambda kv: kv[1])
    return dict(H=H, D=D, A=1 - H - D, O25=sum(v for (i, j), v in M.items() if i + j > 2.5),
                BT=sum(v for (i, j), v in M.items() if i > 0 and j > 0), top=(top[0][0], top[0][1], top[1]))


def basketball_probs(m: dict) -> dict:
    sd, mg = m.get("sd", 12), m["expHome"] - m["expAway"]
    cdf = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
    h, a = 1 - cdf((0.5 - mg) / sd), cdf((-0.5 - mg) / sd)
    return dict(H=h / (h + a), D=0.0, A=a / (h + a), mg=mg, total=m["expHome"] + m["expAway"])


def probs_of(m: dict) -> dict:
    return football_probs(m["lambdaHome"], m["lambdaAway"]) if m["sport"] == "football" else basketball_probs(m)


def best(m: dict, p: dict):
    opts = [("1", p["H"], m["home"]), ("Χ", p["D"], None), ("2", p["A"], m["away"])]
    if m["sport"] != "football":
        opts = [opts[0], opts[2]]
    return max(opts, key=lambda o: o[1])


def why(m: dict, p: dict):
    I, out = m.get("info") or {}, []
    if m["sport"] == "football":
        out.append(f"Το μοντέλο περιμένει {dec(m['lambdaHome'])} γκολ για {m['home']} και {dec(m['lambdaAway'])} για {m['away']}.")
        ph, pa = (I.get("pos") or [None, None])
        lh, la = (I.get("lvl") or [None, None])
        if ph and pa and ph["lg"] == pa["lg"]:
            out.append(f"Βαθμολογία {ph['lg']}: {m['home']} {ph['rank']}η με {ph['pts']} βαθμούς, {m['away']} {pa['rank']}η με {pa['pts']}.")
        else:
            for n, q in ((m["home"], ph), (m["away"], pa)):
                if q:
                    out.append(f"{n}: {q['rank']}η θέση στη βαθμολογία της {q['lg']} ({q['pts']} βαθμοί σε {q['p']} αγώνες).")
            if lh and la and lh[1] is not None and la[1] is not None and lh[0] != la[0] and abs(lh[1] - la[1]) >= 0.15:
                s, w = (lh[0], la[0]) if lh[1] > la[1] else (la[0], lh[0])
                out.append(f"Το μοντέλο υπολογίζει ότι η {s} είναι αρκετά πιο δυνατό πρωτάθλημα από τη {w}, "
                           "γι' αυτό οι επιδόσεις στο εσωτερικό δεν μετράνε το ίδιο.")
    else:
        fav = m["home"] if p["mg"] >= 0 else m["away"]
        out.append(f"Αναμενόμενο σκορ {round(m['expHome'])}-{round(m['expAway'])}: διαφορά {dec(abs(p['mg']))} πόντων υπέρ {fav}.")
    fh, fa = (I.get("form") or [[], []])
    if fh and fa:
        c = lambda f: {k: sum(1 for x in f if x["r"] == k) for k in "WDL"}
        a, b = c(fh), c(fa)
        fmt = (lambda x: f"{x['W']}Ν-{x['D']}Ι-{x['L']}Η") if m["sport"] == "football" else (lambda x: f"{x['W']}Ν-{x['L']}Η")
        out.append(f"Τελευταίοι {min(len(fh), len(fa))} αγώνες σε όλες τις διοργανώσεις: {m['home']} {fmt(a)}, {m['away']} {fmt(b)}.")
    out.append(f"Πλεονέκτημα έδρας: {m['home']}.")
    k, pr, team = best(m, p)
    if k == "Χ":
        v = f"Πιθανότερη η ισοπαλία ({pct(pr)}), χωρίς καθαρό φαβορί."
    elif pr >= 0.65:
        v = f"Καθαρό φαβορί: {team} ({pct(pr)})."
    elif pr >= 0.45:
        v = f"Προβάδισμα για {team} ({pct(pr)}), όχι όμως καθαρό."
    else:
        v = "Ανοιχτό παιχνίδι: κανένα σημείο δεν ξεπερνά το 45%."
    return out, v


# ------------------------------------------------------------------ HTML
CSS = """
:root{--bg:#0A1714;--surface:#0F211C;--surface-2:#15302A;--line:#20413A;--ink:#EAF2EE;--muted:#8FA89E;--amber:#FFB627;
--amber-ink:#FFC756;--draw:#58736A;--sky:#5AB4FF;--pos:#4FD18B;--neg:#FF7A6B;--on-amber:#1A1200;
--display:"Fira Sans Extra Condensed","Arial Narrow","Roboto Condensed",sans-serif;--body:"Commissioner","Segoe UI",system-ui,sans-serif;color-scheme:dark}
@media (prefers-color-scheme:light){:root{--bg:#EEF3EF;--surface:#fff;--surface-2:#E4ECE6;--line:#CFDCD3;--ink:#0E1F1A;--muted:#567066;
--amber:#E89A00;--amber-ink:#8A5A00;--draw:#A7B8AF;--sky:#1F7AD1;--pos:#17803F;--neg:#C2412E;color-scheme:light}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 var(--body)}
a{color:var(--amber-ink)}.gut{padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
.wrap{max-width:980px;margin:0 auto}
.top{background:#06100D;border-bottom:1px solid #1B302A}.top .wrap{display:flex;align-items:center;gap:20px;min-height:58px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:10px;color:#F3F7F5;text-decoration:none}.brand svg{width:30px;height:30px}
.brand b{font:italic 900 25px/1 var(--display)}.brand b span{color:#FFB627;font-weight:700;margin-left:3px}
.top nav{display:flex;gap:16px;flex-wrap:wrap}.top nav a{color:#9DB3AA;text-decoration:none;font:600 16px/1 var(--display)}.top nav a:hover{color:#fff}
main{padding-block:20px 40px;display:grid;gap:16px}
.crumbs{font-size:13px;color:var(--muted)}.crumbs a{color:var(--muted)}
.hero{border-radius:18px;padding:22px 24px;color:#F3F7F5;background:radial-gradient(120% 90% at 50% -20%,rgba(255,182,39,.28),transparent 60%),repeating-linear-gradient(90deg,#0F2A22 0 64px,#12301F 64px 128px),#0E241D;display:grid;gap:14px}
.hero.bb{background:radial-gradient(120% 90% at 50% -20%,rgba(255,182,39,.26),transparent 60%),linear-gradient(160deg,#3A2414,#22160E 60%,#18110B)}
.meta{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;font:600 15px/1.2 var(--display)}
.when{background:#FFB627;color:#1A1200;border-radius:8px;padding:5px 10px}
h1{margin:0;font:italic 900 clamp(30px,6vw,52px)/1 var(--display);text-wrap:balance}
.sub{margin:-6px 0 0;color:rgba(255,255,255,.75)}
.pbar{display:flex;gap:2px;height:12px;border-radius:4px;overflow:hidden}.pbar i{display:block}
.pbar .h{background:#FFB627}.pbar .d{background:#58736A}.pbar .a{background:#5AB4FF}
.odds{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;max-width:560px}.odds.two{grid-template-columns:repeat(2,minmax(0,1fr));max-width:380px}
.odd{background:rgba(0,0,0,.42);border:1px solid rgba(255,255,255,.16);border-radius:10px;padding:9px 12px;display:grid;grid-template-columns:auto 1fr;column-gap:8px;align-items:baseline}
.odd .k{grid-row:1/3;align-self:center;font:700 14px/1 var(--display);color:rgba(255,255,255,.66)}
.odd .v{font:800 24px/1 var(--display);text-align:right}.odd .p{font:600 12px/1.2 var(--display);text-align:right;color:rgba(255,255,255,.66)}
.odd.best{background:#FFB627;border-color:#FFB627;color:#1A1200}.odd.best .k,.odd.best .p{color:#4A3300}
.facts{display:flex;flex-wrap:wrap;gap:6px 20px;font-size:14px;color:rgba(255,255,255,.8)}.facts b{font:700 17px/1 var(--display);color:#fff;margin-left:4px}
.result{border-radius:14px;padding:14px 16px;background:var(--surface-2);font:600 17px/1.3 var(--display)}.result .ok{color:var(--pos)}.result .no{color:var(--neg)}
.card{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:16px 18px;display:grid;gap:10px}
.card h2{margin:0;font:italic 800 23px/1.15 var(--display)}
.card ul{margin:0;padding-left:18px;display:grid;gap:6px}.card li::marker{color:var(--amber)}
.verdict{margin:0;font:italic 800 21px/1.2 var(--display);color:var(--amber-ink)}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;align-items:start}
table{width:100%;border-collapse:collapse;font-size:14px}td,th{padding:7px 6px;border-top:1px solid var(--line);text-align:left}
th{font:600 13px/1.2 var(--display);color:var(--muted);border-top:0}td.n{text-align:right;font:700 15px/1 var(--display);white-space:nowrap}
td small{display:block;color:var(--muted);font-size:12px}
.pill{display:inline-grid;place-items:center;width:22px;height:22px;border-radius:6px;font:800 12px/1 var(--display);color:#fff;margin-right:3px}
.W{background:#1F9D57}.D{background:#6E8079}.L{background:#C94A3B}
.cta{display:block;text-align:center;background:#FFB627;color:#1A1200;text-decoration:none;border-radius:12px;padding:14px 16px;font:800 18px/1.2 var(--display)}
.wide{overflow-x:auto}
footer{border-top:1px solid var(--line);padding-block:20px 34px;color:var(--muted);font-size:13px}footer a{color:var(--ink)}
footer p{margin:0 0 6px;max-width:80ch}.links{display:flex;flex-wrap:wrap;gap:6px 14px;margin:10px 0}
@media (max-width:680px){.grid{grid-template-columns:minmax(0,1fr)}.hero{padding:18px 16px}}
"""

LOGO = ('<svg viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="8" fill="#FFB627"/>'
        '<path d="M10.6 20.2 Q19.2 13.4 26.4 5.4 Q22.9 16 13.8 23.3 Z" fill="#1A1200"/>'
        '<ellipse cx="11" cy="21.9" rx="3.6" ry="1.35" transform="rotate(-42 11 21.9)" fill="#1A1200"/>'
        '<path d="M9.6 23.4 6.2 26.8" stroke="#1A1200" stroke-width="2.8" stroke-linecap="round"/></svg>')


def page(title, desc, canonical, body, root, og_image="", jsonld=None, links_html=""):
    og = ""
    if canonical:
        og += f'<link rel="canonical" href="{E(canonical)}">\n<meta property="og:url" content="{E(canonical)}">\n'
    if og_image:
        og += f'<meta property="og:image" content="{E(og_image)}">\n<meta property="og:image:width" content="1200">\n<meta property="og:image:height" content="630">\n'
    ld = f'<script type="application/ld+json">{json.dumps(jsonld, ensure_ascii=False)}</script>\n' if jsonld else ""
    return f"""<!doctype html>
<html lang="el">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{E(title)}</title>
<meta name="description" content="{E(desc)}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="SamurAI bet">
<meta property="og:locale" content="el_GR">
<meta property="og:title" content="{E(title)}">
<meta property="og:description" content="{E(desc)}">
<meta name="twitter:card" content="summary_large_image">
{og}<meta name="theme-color" content="#0A1714">
<link rel="icon" href="{root}og-icon.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Commissioner:wght@400;600;700&family=Fira+Sans+Extra+Condensed:ital,wght@0,600;0,700;1,800;1,900&display=swap">
<link rel="stylesheet" href="{root}static.css">
{ld}<script data-goatcounter="https://samuraibet.goatcounter.com/count" async src="https://gc.zgo.at/count.js"></script>
</head>
<body>
<header class="top gut"><div class="wrap">
<a class="brand" href="{root}">{LOGO}<b>SamurAI<span>bet</span></b></a>
<nav><a href="{root}#football">Ποδόσφαιρο</a><a href="{root}#basketball">Μπάσκετ</a><a href="{root}protathlimata/">Πρωταθλήματα</a><a href="{root}#record">Ιστορικό</a></nav>
</div></header>
<main class="gut"><div class="wrap" style="display:grid;gap:16px">
{body}
</div></main>
<footer class="gut"><div class="wrap">
<p><b>18+ · Παίξε υπεύθυνα.</b> Το SamurAI bet δείχνει στατιστικές εκτιμήσεις και δεν είναι συμβουλή στοιχηματισμού. Δεν συνεργάζεται με στοιχηματικές εταιρείες.
Αν ο τζόγος σού δημιουργεί πρόβλημα: <a href="https://findahelpline.com/el-GR/countries/gr/topics/gambling" rel="noopener">γραμμές υποστήριξης στην Ελλάδα</a>.</p>
<div class="links">{links_html}</div>
</div></footer>
</body>
</html>
"""


def odds_html(m, p):
    items = [("1", p["H"]), ("Χ", p["D"]), ("2", p["A"])] if m["sport"] == "football" else [("1", p["H"]), ("2", p["A"])]
    top = max(x[1] for x in items)
    cells = "".join(f'<div class="odd{" best" if v == top else ""}"><span class="k">{k}</span><span class="v">{fair(v)}</span>'
                    f'<span class="p">{pct(v)}</span></div>' for k, v in items)
    return f'<div class="odds{"" if m["sport"] == "football" else " two"}">{cells}</div>'


def pbar_html(p):
    return ('<div class="pbar" role="img" aria-label="Πιθανότητες">' +
            "".join(f'<i class="{c}" style="flex:{v:.4f} 1 0"></i>' for c, v in (("h", p["H"]), ("d", p["D"]), ("a", p["A"])) if v > 0.001) +
            "</div>")


def form_table(f):
    rows = "".join(f'<tr><td>{dt.date.fromisoformat(x["d"]):%d/%m}</td><td>{"εντός" if x["h"] else "εκτός"} με {E(x["o"])}'
                   f'<small>{E(x["lg"])}</small></td><td class="n">{x["gf"]}-{x["ga"]}</td>'
                   f'<td class="n"><span class="pill {x["r"]}">{ {"W": "Ν", "D": "Ι", "L": "Η"}[x["r"]] }</span></td></tr>' for x in f)
    return f"<table><tbody>{rows}</tbody></table>"


# ------------------------------------------------------------------ εικόνες προεπισκόπησης
def pillow():
    try:
        from PIL import Image, ImageDraw, ImageFont  # noqa
        return True
    except ImportError:
        pass
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "pillow"],
                       check=True, timeout=180, capture_output=True)
        from PIL import Image, ImageDraw, ImageFont  # noqa
        return True
    except Exception as e:
        print(f"  ! εικόνες προεπισκόπησης: χωρίς Pillow ({e})")
        return False


def og_image(path, league, when, home, away, items, footer="samurai bet · πιθανότητες από στατιστικό μοντέλο"):
    from PIL import Image, ImageDraw, ImageFont
    W, H = 1200, 630
    img = Image.new("RGB", (W, H), (10, 23, 20))
    d = ImageDraw.Draw(img)
    for x in range(0, W, 128):                                           # λωρίδες γηπέδου
        d.rectangle([x + 64, 0, x + 127, H], fill=(14, 36, 29))
    d.rectangle([0, 0, W, 10], fill=(255, 182, 39))
    bold = str(HERE / "fonts" / "DejaVuSans-Bold.ttf"); reg = str(HERE / "fonts" / "DejaVuSans.ttf")
    F = lambda s, b=True: ImageFont.truetype(bold if b else reg, s)

    def fit(text, size, maxw):
        while size > 26 and d.textlength(text, font=F(size)) > maxw:
            size -= 2
        return F(size)
    d.text((60, 46), league, font=F(34), fill=(255, 199, 86))
    d.text((W - 60, 46), when, font=F(30, False), fill=(234, 242, 238), anchor="ra")
    d.text((60, 130), home, font=fit(home, 76, W - 120), fill=(243, 247, 245))
    d.text((60, 232), "–  " + away, font=fit("–  " + away, 76, W - 120), fill=(243, 247, 245))
    x, top = 60, max(v for _, v in items)
    bw = (W - 120 - 24 * (len(items) - 1)) // len(items)
    for k, v in items:
        best = v == top
        d.rounded_rectangle([x, 370, x + bw, 500], radius=18, fill=(255, 182, 39) if best else (0, 0, 0),
                            outline=None if best else (60, 90, 80), width=2)
        col = (26, 18, 0) if best else (243, 247, 245)
        d.text((x + 26, 435), k, font=F(40), fill=col, anchor="lm")
        d.text((x + bw - 26, 418), pct(v), font=F(62), fill=col, anchor="rm")
        d.text((x + bw - 26, 470), "δίκαιη " + fair(v), font=F(26, False), fill=col, anchor="rm")
        x += bw + 24
    d.text((60, 560), "SamurAI", font=F(40), fill=(243, 247, 245))
    d.text((60 + d.textlength("SamurAI ", font=F(40)), 560), "bet", font=F(40), fill=(255, 182, 39))
    d.text((W - 60, 572), "18+ · πιθανότητες από στατιστικό μοντέλο", font=F(24, False), fill=(143, 168, 158), anchor="ra")
    img.convert("P", palette=Image.ADAPTIVE, colors=96).save(path, "PNG", optimize=True)


def site_og(path):
    from PIL import Image, ImageDraw, ImageFont
    W, H = 1200, 630
    img = Image.new("RGB", (W, H), (10, 23, 20)); d = ImageDraw.Draw(img)
    for x in range(0, W, 128):
        d.rectangle([x + 64, 0, x + 127, H], fill=(14, 36, 29))
    d.rectangle([0, 0, W, 10], fill=(255, 182, 39))
    b = ImageFont.truetype(str(HERE / "fonts" / "DejaVuSans-Bold.ttf"), 110)
    r = ImageFont.truetype(str(HERE / "fonts" / "DejaVuSans.ttf"), 38)
    d.text((80, 200), "SamurAI", font=b, fill=(243, 247, 245))
    d.text((80 + d.textlength("SamurAI ", font=b), 200), "bet", font=b, fill=(255, 182, 39))
    d.text((84, 360), "Πιθανότητες για ποδόσφαιρο και μπάσκετ", font=r, fill=(234, 242, 238))
    d.text((84, 412), "από στατιστικό μοντέλο, με ανοιχτό ιστορικό επιτυχίας", font=r, fill=(143, 168, 158))
    img.convert("P", palette=Image.ADAPTIVE, colors=96).save(path, "PNG", optimize=True)


# ------------------------------------------------------------------ κατασκευή
def build(site: pathlib.Path, matches: list, history: dict, tables: list, now: dt.datetime):
    base = base_url(site)
    for sub in ("agones", "protathlimata", "og"):
        p = site / sub
        p.mkdir(exist_ok=True)
        for f in p.iterdir():                     # σβήσε τα παλιά της προηγούμενης εκτέλεσης
            if f.is_file():
                f.unlink()
    (site / "static.css").write_text(CSS.strip())
    (site / "og-icon.svg").write_text(LOGO.replace('aria-hidden="true"', 'xmlns="http://www.w3.org/2000/svg"'))
    has_pil = pillow()
    if has_pil:
        site_og(site / "og.png")

    leagues = {}
    for m in matches:
        leagues.setdefault(m["league"], []).append(m)
    lg_links = "".join(f'<a href="{{root}}protathlimata/{league_slug(n)}.html">Προγνωστικά {E(n)}</a>' for n in leagues)
    urls = [(base, "daily"), (base + "protathlimata/", "daily")]

    # --- σελίδες αγώνων: επερχόμενοι
    for m in matches:
        p = probs_of(m)
        s = m.get("page") or f"agones/{match_slug(m)}.html"
        k, pr, team = best(m, p)
        lines, verdict = why(m, p)
        when = when_text(m["kickoff"])
        if m["sport"] == "football":
            t = p["top"]
            facts = (f'<span>Πιθανότερο σκορ<b>{t[0]}-{t[1]}</b></span><span>Over 2,5<b>{pct(p["O25"])}</b></span>'
                     f'<span>Γκολ-Γκολ<b>{pct(p["BT"])}</b></span><span>Αναμενόμενα γκολ<b>{dec(m["lambdaHome"], 2)}–{dec(m["lambdaAway"], 2)}</b></span>')
            desc = (f"{m['league']}, {when}. Πιθανότητες: 1 {pct(p['H'])}, Χ {pct(p['D'])}, 2 {pct(p['A'])}. "
                    f"Πιθανότερο σκορ {t[0]}-{t[1]}, Over 2,5 {pct(p['O25'])}. Φόρμα, βαθμολογία και δίκαιες αποδόσεις.")
        else:
            facts = (f'<span>Αναμενόμενο σκορ<b>{round(m["expHome"])}-{round(m["expAway"])}</b></span>'
                     f'<span>Χάντικαπ<b>{dec(-p["mg"])}</b></span><span>Σύνολο<b>{dec(p["total"])}</b></span>')
            desc = (f"{m['league']}, {when}. Πιθανότητες νίκης: {m['home']} {pct(p['H'])}, {m['away']} {pct(p['A'])}. "
                    f"Αναμενόμενο σκορ {round(m['expHome'])}-{round(m['expAway'])}, φόρμα και δίκαιες αποδόσεις.")
        I = m.get("info") or {}
        blocks = []
        pos = I.get("pos") or [None, None]
        if any(pos):
            cells = "".join(f'<tr><td>{E(n)}</td><td>{E(q["lg"]) if q else "–"}</td><td class="n">{(str(q["rank"]) + "η") if q else "–"}</td>'
                            f'<td class="n">{q["pts"] if q else "–"}</td></tr>' for n, q in ((m["home"], pos[0]), (m["away"], pos[1])))
            blocks.append(f'<section class="card"><h2>Θέση στη βαθμολογία</h2><table><thead><tr><th>Ομάδα</th><th>Πρωτάθλημα</th>'
                          f'<th style="text-align:right">Θέση</th><th style="text-align:right">Βαθμοί</th></tr></thead><tbody>{cells}</tbody></table></section>')
        for n, f in zip((m["home"], m["away"]), I.get("form") or [[], []]):
            if f:
                blocks.append(f'<section class="card"><h2>Φόρμα: {E(n)}</h2>{form_table(f)}</section>')
        h2h = I.get("h2h") or []
        h2h_html = ("<table><tbody>" + "".join(f'<tr><td>{dt.date.fromisoformat(g["d"]):%d/%m/%y}</td><td>{E(g["h"])} – {E(g["a"])}'
                                              f'<small>{E(g["lg"])}</small></td><td class="n">{g["hs"]}-{g["as_"]}</td></tr>' for g in h2h)
                    + "</tbody></table>") if h2h else "<p>Δεν έχουν παίξει μεταξύ τους τους τελευταίους μήνες.</p>"
        others = [x for x in leagues[m["league"]] if x is not m][:8]
        other_html = "".join(f'<li><a href="../{x.get("page")}">{E(x["home"])} – {E(x["away"])}</a> <small>{when_text(x["kickoff"])}</small></li>'
                             for x in others if x.get("page"))
        body = f"""<nav class="crumbs"><a href="../">Αρχική</a> › <a href="../protathlimata/{league_slug(m['league'])}.html">{E(m['league'])}</a> › {E(m['home'])} – {E(m['away'])}</nav>
<article class="hero{'' if m['sport'] == 'football' else ' bb'}">
<div class="meta"><span>{E(m['league'])}</span><span class="when">{E(when)}</span></div>
<h1>{E(m['home'])} – {E(m['away'])}</h1>
<p class="sub">Πρόγνωση, πιθανότητες και δίκαιες αποδόσεις</p>
{pbar_html(p)}
{odds_html(m, p)}
<div class="facts">{facts}</div>
</article>
<section class="card"><h2>Γιατί αυτή η πρόβλεψη</h2><ul>{''.join(f'<li>{E(x)}</li>' for x in lines)}</ul><p class="verdict">{E(verdict)}</p></section>
<div class="grid">{''.join(blocks)}<section class="card" style="grid-column:1/-1"><h2>Προηγούμενες αναμετρήσεις</h2>{h2h_html}</section></div>
<a class="cta" href="../#m/{E(m['id'])}">Πλήρης ανάλυση: πίνακας σκορ, αγορές και σύγκριση με την απόδοση που βλέπεις</a>
{f'<section class="card"><h2>Άλλοι αγώνες {E(m["league"])}</h2><ul>{other_html}</ul></section>' if other_html else ''}"""
        jsonld = {"@context": "https://schema.org", "@type": "SportsEvent", "name": f"{m['home']} – {m['away']}",
                  "startDate": m["kickoff"], "eventStatus": "https://schema.org/EventScheduled",
                  "sport": "Soccer" if m["sport"] == "football" else "Basketball",
                  "homeTeam": {"@type": "SportsTeam", "name": m["home"]}, "awayTeam": {"@type": "SportsTeam", "name": m["away"]},
                  "superEvent": {"@type": "SportsEvent", "name": m["league"]}, "description": desc}
        img = ""
        if has_pil:
            name = s.split("/")[-1].replace(".html", ".png")
            items = [("1", p["H"]), ("Χ", p["D"]), ("2", p["A"])] if m["sport"] == "football" else [("1", p["H"]), ("2", p["A"])]
            try:
                og_image(site / "og" / name, m["league"], when, m["home"], m["away"], items)
                img = f"{base}og/{name}" if base else f"../og/{name}"
            except Exception as e:
                print(f"  ! εικόνα {name}: {e}")
        title = f"{m['home']} – {m['away']}: πρόγνωση και πιθανότητες | SamurAI bet"
        (site / s).write_text(page(title, desc, base + s if base else "", body, "../", img, jsonld, lg_links.replace("{root}", "../")))
        urls.append((base + s, "daily"))

    # --- σελίδες αγώνων που παίχτηκαν πρόσφατα (με αποτέλεσμα: διαφάνεια)
    cutoff = (now - dt.timedelta(days=14)).isoformat()
    for e in (history.get("events") or {}).values():
        if not e.get("result") or e["kickoff"] < cutoff:
            continue
        m = dict(e, page=None)
        s = f"agones/{match_slug(m)}.html"
        P, r = e["p"], e["result"]
        if e["sport"] == "football":
            real = "1" if r["home"] > r["away"] else "Χ" if r["home"] == r["away"] else "2"
            items = [("1", P["H"]), ("Χ", P["D"]), ("2", P["A"])]
        else:
            real = "1" if r["home"] > r["away"] else "2"
            items = [("1", P["H"]), ("2", P["A"])]
        pick = max(items, key=lambda x: x[1])
        ok = pick[0] == real
        pp = dict(H=P["H"], D=P.get("D", 0.0), A=P["A"])
        desc = (f"{e['league']}: {e['home']} – {e['away']} {r['home']}-{r['away']}. Η πρόβλεψη πριν από τον αγώνα: "
                f"{pick[0]} με {pct(pick[1])}.")
        body = f"""<nav class="crumbs"><a href="../">Αρχική</a> › <a href="../protathlimata/{league_slug(e['league'])}.html">{E(e['league'])}</a> › {E(e['home'])} – {E(e['away'])}</nav>
<article class="hero{'' if e['sport'] == 'football' else ' bb'}">
<div class="meta"><span>{E(e['league'])}</span><span class="when">{E(when_text(e['kickoff']))}</span></div>
<h1>{E(e['home'])} – {E(e['away'])} {r['home']}-{r['away']}</h1>
<p class="sub">Η πρόβλεψη όπως δημοσιεύτηκε πριν από τον αγώνα</p>
{pbar_html(pp)}
{odds_html(dict(sport=e['sport']), pp)}
</article>
<p class="result">Τελικό {r['home']}-{r['away']}. Το μοντέλο έδινε πιο πιθανό το «{pick[0]}» ({pct(pick[1])}):
<span class="{'ok' if ok else 'no'}">{'βγήκε' if ok else 'δεν βγήκε'}</span>.</p>
<a class="cta" href="../#record">Όλο το ιστορικό των προβλέψεων</a>"""
        (site / s).write_text(page(f"{e['home']} – {e['away']} {r['home']}-{r['away']}: η πρόβλεψη | SamurAI bet", desc,
                                   base + s if base else "", body, "../", "", None, lg_links.replace("{root}", "../")))
        urls.append((base + s, "weekly"))

    # --- σελίδες πρωταθλημάτων
    tmap = {t["name"]: t for t in tables or []}
    for name, ms in leagues.items():
        ls = league_slug(name)
        rows = []
        for m in sorted(ms, key=lambda x: x["kickoff"]):
            p = probs_of(m)
            cells = (f'<td class="n">{pct(p["H"])}<small>{fair(p["H"])}</small></td>' +
                     (f'<td class="n">{pct(p["D"])}<small>{fair(p["D"])}</small></td>' if m["sport"] == "football" else "") +
                     f'<td class="n">{pct(p["A"])}<small>{fair(p["A"])}</small></td>')
            rows.append(f'<tr><td>{when_text(m["kickoff"])}</td><td><a href="../{m.get("page")}">{E(m["home"])} – {E(m["away"])}</a></td>{cells}</tr>')
        fb = ms[0]["sport"] == "football"
        head = "<th style='text-align:right'>1</th>" + ("<th style='text-align:right'>Χ</th>" if fb else "") + "<th style='text-align:right'>2</th>"
        table_html = ""
        T = tmap.get(name)
        if T:
            sim = T.get("sim")
            th = "<tr><th>#</th><th>Ομάδα</th><th style='text-align:right'>Αγ</th><th style='text-align:right'>Β</th>" + \
                 ("<th style='text-align:right'>Αναμ. βαθμοί</th><th style='text-align:right'>1η</th><th style='text-align:right'>Top 4</th>"
                  "<th style='text-align:right'>Τελευταίοι 3</th>" if sim else "") + "</tr>"
            tr = "".join(f'<tr><td>{i}</td><td>{E(r["t"])}</td><td class="n">{r["p"]}</td><td class="n">{r["pts"]}</td>' +
                         (f'<td class="n">{round(r["xp"])}</td><td class="n">{pz(r["p1"])}</td><td class="n">{pz(r["p4"])}</td>'
                          f'<td class="n">{pz(r["pb"])}</td>' if sim else "") + "</tr>" for i, r in enumerate(T["rows"], 1))
            table_html = (f'<section class="card"><h2>Βαθμολογία{" και πρόβλεψη τελικής κατάταξης" if sim else ""}</h2>'
                          f'<div class="wide"><table><thead>{th}</thead><tbody>{tr}</tbody></table></div>'
                          f'<p style="margin:0;color:var(--muted);font-size:13px">{E(T.get("note", ""))}</p></section>')
        desc = (f"Προγνωστικά {name} από στατιστικό μοντέλο: πιθανότητες {'1Χ2' if fb else 'νίκης'} και δίκαιες αποδόσεις για "
                f"{len(ms)} αγώνες των επόμενων ημερών" + (", και πρόβλεψη τελικής βαθμολογίας." if T and T.get("sim") else "."))
        body = f"""<nav class="crumbs"><a href="../">Αρχική</a> › <a href="./">Πρωταθλήματα</a> › {E(name)}</nav>
<h1 style="color:var(--ink)">Προγνωστικά {E(name)}</h1>
<p style="margin:0;color:var(--muted)">Πιθανότητες για κάθε αγώνα από στατιστικό μοντέλο, με τις δίκαιες αποδόσεις που τους αντιστοιχούν. Ενημέρωση {when_text(now.isoformat())}.</p>
<section class="card"><h2>Επόμενοι αγώνες</h2><div class="wide"><table><thead><tr><th>Ημέρα</th><th>Αγώνας</th>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>
{table_html}
<a class="cta" href="../">Όλοι οι αγώνες και η διαδραστική ανάλυση</a>"""
        (site / "protathlimata" / f"{ls}.html").write_text(
            page(f"Προγνωστικά {name}: πιθανότητες για κάθε αγώνα | SamurAI bet", desc,
                 f"{base}protathlimata/{ls}.html" if base else "", body, "../", f"{base}og.png" if base and has_pil else "",
                 None, lg_links.replace("{root}", "../")))
        urls.append((f"{base}protathlimata/{ls}.html", "daily"))

    idx = "".join(f'<li><a href="{league_slug(n)}.html">Προγνωστικά {E(n)}</a> <small>({len(ms)} αγώνες)</small></li>' for n, ms in leagues.items())
    (site / "protathlimata" / "index.html").write_text(page(
        "Προγνωστικά ανά πρωτάθλημα | SamurAI bet", "Προγνωστικά από στατιστικό μοντέλο για Super League, Premier League, "
        "La Liga, Serie A, Bundesliga, Champions League, EuroLeague, NBA και άλλα πρωταθλήματα.",
        f"{base}protathlimata/" if base else "", f'<h1 style="color:var(--ink)">Προγνωστικά ανά πρωτάθλημα</h1><section class="card"><ul>{idx}</ul></section>',
        "../", f"{base}og.png" if base and has_pil else "", None, lg_links.replace("{root}", "../")))

    # --- αρχική: απόλυτες διευθύνσεις για εικόνα και canonical
    home = site / "index.html"
    if base and home.exists():
        h = home.read_text()
        h = re.sub(r'<meta property="og:image" content="[^"]*">', f'<meta property="og:image" content="{base}og.png">', h)
        h = re.sub(r'<link rel="canonical" href="[^"]*">', f'<link rel="canonical" href="{base}">', h)
        h = re.sub(r'<meta property="og:url" content="[^"]*">', f'<meta property="og:url" content="{base}">', h)
        home.write_text(h)

    # --- sitemap και robots
    if base:
        day = now.date().isoformat()
        sm = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
        sm += [f"<url><loc>{E(u)}</loc><lastmod>{day}</lastmod><changefreq>{f}</changefreq></url>" for u, f in urls]
        sm.append("</urlset>")
        (site / "sitemap.xml").write_text("\n".join(sm))
        (site / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {base}sitemap.xml\n")
    print(f"• στατικές σελίδες: {len(urls)} διευθύνσεις{', με εικόνες' if has_pil else ''}{'' if base else ' (χωρίς διεύθυνση site: χωρίς sitemap)'}")
