"""Render the static site: one page with tabs (latest / leaderboard / sources) + per-run archive pages."""
import datetime as dt
import json
import shutil
from pathlib import Path

import jdatetime
from jinja2 import Environment

J_MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
            "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]
J_DAYS = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه"]
TEHRAN = dt.timezone(dt.timedelta(hours=3, minutes=30))
LEVELS = {"intro": "مقدماتی", "practitioner": "کاربردی", "advanced": "پیشرفته"}
STATUS = {"active": "فعال", "new": "در انتظار اولین اجرا", "paused": "متوقف (کیفیت)", "disabled": "غیرفعال (خطا)"}


def jalali(iso_date: str) -> dict:
    g = dt.date.fromisoformat(iso_date[:10])
    j = jdatetime.date.fromgregorian(date=g)
    return {"long": f"{J_DAYS[g.weekday()]} {j.day} {J_MONTHS[j.month - 1]} {j.year}",
            "short": f"{j.year}/{j.month:02d}/{j.day:02d}", "greg": g.strftime("%d %b %Y")}


def font_css(root: Path, prefix: str) -> str:
    """Vazirmatn is stored once in docs/fonts and referenced, so pages stay small."""
    out = root / "docs" / "fonts"
    out.mkdir(parents=True, exist_ok=True)
    rules = []
    for weight, name in ((400, "Regular"), (700, "Bold"), (800, "ExtraBold")):
        src = root / "assets" / "fonts" / f"Vazirmatn-{name}.woff2"
        if src.exists():
            dst = out / src.name
            if not dst.exists():
                shutil.copyfile(src, dst)
            rules.append("@font-face{font-family:'Vazirmatn';font-weight:%d;font-display:swap;"
                         "src:url('%sfonts/%s') format('woff2');}" % (weight, prefix, src.name))
    return "\n".join(rules)


def normalize(a: dict) -> dict:
    """Map older schemas (summary/key_points/why_it_matters, whats_new/how/evidence/actions) onto the brief fields."""
    a = dict(a)
    a.setdefault("tldr", a.get("summary", ""))
    a.setdefault("about", a.get("whats_new", ""))
    a.setdefault("problem", "")
    a.setdefault("approach", "")
    a.setdefault("steps", a.get("how") or a.get("key_points") or [])
    a.setdefault("findings", a.get("evidence") or [])
    a.setdefault("conclusion", "")
    a.setdefault("for_us", a.get("actions") or ([a["why_it_matters"]] if a.get("why_it_matters") else []))
    a.setdefault("caveats", [])
    a.setdefault("teams", [])
    a["level_fa"] = LEVELS.get(a.get("level", ""), "")
    a["search"] = " ".join([a.get("title_fa", ""), a.get("title", ""), a.get("tldr", ""), a.get("about", ""),
                            a.get("conclusion", ""), " ".join(a["for_us"]), " ".join(a.get("tags") or []),
                            " ".join(a["teams"]), a.get("source", "")]).lower()
    return a


def cats_present(settings, articles):
    out = []
    for key, meta in settings["categories"].items():
        n = sum(1 for a in articles if a["category"] == key)
        if n:
            out.append({"key": key, "n": n, **meta})
    return out


def render_site(root: Path, settings: dict, board: dict | None = None, source_rows: list | None = None):
    docs = root / "docs"
    files = sorted((docs / "data").glob("2*.json"), reverse=True)
    if not files:
        return
    arch_dir = docs / "archive"
    arch_dir.mkdir(parents=True, exist_ok=True)
    (docs / "archive.json").write_text(
        json.dumps([{"date": f.stem, "short": jalali(f.stem)["short"]} for f in files], ensure_ascii=False),
        encoding="utf-8")
    tpl = Environment(autoescape=True).from_string(TEMPLATE)
    base = dict(settings=settings, cats=settings["categories"], updated=dt.datetime.now(TEHRAN).strftime("%H:%M"))
    cache = {}

    def load(f):
        if f.stem not in cache:
            cache[f.stem] = json.loads(f.read_text(encoding="utf-8"))
        return cache[f.stem]

    # archive pages: one per run, never rewritten later
    for i, f in enumerate(files):
        if i and (arch_dir / f"{f.stem}.html").exists():
            continue
        dg = load(f)
        arts = [normalize(dict(a, day=f.stem, day_short=jalali(f.stem)["short"])) for a in dg["articles"]]
        (arch_dir / f"{f.stem}.html").write_text(tpl.render(
            **base, archive_view=True, prefix="../", fonts=font_css(root, "../"), digest=dg, d=jalali(dg["date"]),
            current=dg["date"], latest=arts, latest_cats=cats_present(settings, arts),
            board={}, board_n=0, board_cats=[], rows=[]), encoding="utf-8")

    # main page: latest N + leaderboard + sources as tabs
    n = settings["selection"].get("latest_size", 10)
    latest = []
    for f in files:
        for a in load(f)["articles"]:
            if len(latest) < n:
                latest.append(normalize(dict(a, day=f.stem, day_short=jalali(f.stem)["short"])))
        if len(latest) >= n:
            break
    today = load(files[0])
    board = {k: [normalize(dict(e, added_short=jalali(e.get("added", today["date"]))["short"])) for e in v]
             for k, v in (board or {}).items()}
    flat = [e for k in settings["categories"] for e in board.get(k, [])]
    rows = source_rows or []
    for r in rows:
        r["last_item_short"] = jalali(r["last_item"])["short"] if r.get("last_item") else ""
        r["status_fa"] = STATUS.get(r["status"], r["status"])
    (docs / "index.html").write_text(tpl.render(
        **base, archive_view=False, prefix="", fonts=font_css(root, ""), digest=today, d=jalali(today["date"]),
        current=today["date"], latest=latest, latest_cats=cats_present(settings, latest),
        board=board, board_n=len(flat), board_cats=cats_present(settings, flat), rows=rows), encoding="utf-8")
    # one page per article: the full brief
    adir = docs / "a"
    adir.mkdir(parents=True, exist_ok=True)
    btpl = Environment(autoescape=True).from_string(BRIEF)
    pool = {a["id"]: a for a in latest}
    for e in flat:
        pool.setdefault(e["id"], e)
    for a in [normalize(x) for x in today["articles"]]:
        pool.setdefault(a["id"], a)
    for aid, a in pool.items():
        (adir / f"{aid}.html").write_text(btpl.render(
            a=a, c=settings["categories"][a["category"]], settings=settings, fonts=font_css(root, "../"),
            published=jalali(a["published"])["short"] if a.get("published") else ""), encoding="utf-8")
    for old in ("leaderboard.html", "sources.html"):
        (docs / old).unlink(missing_ok=True)
    (docs / ".nojekyll").write_text("")


TEMPLATE = r"""<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{{ settings.site_title }} | {{ d.short }}</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📚</text></svg>">
<style>
{{ fonts | safe }}
:root{--ink:#121829;--sheet:#182036;--sheet-2:#1E2741;--rule:#2B3554;--text:#ECE9E2;--soft:#BCC2D0;--mute:#8189A0;--focus:#F2D27A;
  box-sizing:border-box;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}
*,*::before,*::after{box-sizing:inherit}
html{scroll-padding-top:calc(env(safe-area-inset-top,0px) + 120px)}
body{margin:0;background:var(--ink);color:var(--text);font-family:'Vazirmatn',Tahoma,sans-serif;font-size:16.5px;line-height:1.95;
  -webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
a{color:inherit}
:focus-visible{outline:2px solid var(--focus);outline-offset:3px;border-radius:4px}
.wrap{max-width:820px;margin:0 auto;padding:0 20px}
.ltr{direction:ltr;unicode-bidi:isolate}

.mast{padding:46px 0 22px}
.brand{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap;color:var(--mute);font-size:14px}
.brand b{color:var(--soft)}
.date{margin:18px 0 2px;font-size:clamp(30px,6.5vw,46px);font-weight:800;line-height:1.3}
.greg{color:var(--mute);font-size:14.5px}
.note{margin:20px 0 0;font-size:18px;line-height:2;color:var(--soft);max-width:68ch}
.stats{display:flex;gap:26px;margin-top:18px;color:var(--mute);font-size:13.5px;flex-wrap:wrap}
.stats strong{display:block;color:var(--text);font-size:21px;font-weight:700;line-height:1.4}

.tabs{position:sticky;top:env(safe-area-inset-top,0px);z-index:6;background:rgba(18,24,41,.96);backdrop-filter:blur(8px);border-bottom:1px solid var(--rule)}
.tabbar{display:flex;gap:4px;padding-top:10px}
.tab{border:0;background:transparent;color:var(--mute);font:inherit;font-size:15.5px;font-weight:700;padding:8px 16px 10px;cursor:pointer;
  border-bottom:2.5px solid transparent;text-decoration:none}
.tab[aria-selected="true"]{color:var(--text);border-bottom-color:var(--focus)}
.tab span{font-weight:400;opacity:.65;margin-inline-start:4px}
.tools{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:10px 0 12px}
.chips{display:flex;gap:6px;overflow-x:auto;flex:1 1 100%;scrollbar-width:none}
.chips::-webkit-scrollbar{display:none}
.chip{flex:none;border:1px solid var(--rule);background:transparent;color:var(--soft);font:inherit;font-size:13.5px;padding:3px 12px;border-radius:999px;cursor:pointer;white-space:nowrap}
.chip[aria-pressed="true"]{background:var(--c,#ECE9E2);border-color:var(--c,#ECE9E2);color:#141a2b;font-weight:700}
.chip span{opacity:.7;margin-inline-start:4px}
.search{flex:1 1 220px;min-width:0;background:var(--sheet);border:1px solid var(--rule);color:var(--text);font:inherit;font-size:14.5px;padding:6px 14px;border-radius:10px}
.search::placeholder{color:var(--mute)}
select{background:var(--sheet);border:1px solid var(--rule);color:var(--soft);font:inherit;font-size:14px;padding:6px 10px;border-radius:10px}

.panel{padding:6px 0 60px}
.panel[hidden]{display:none}
.group{margin-top:34px}
.group h2{display:flex;align-items:center;gap:10px;font-size:15px;font-weight:700;color:var(--c);margin:0 0 4px}
.group h2::before{content:"";width:10px;height:10px;border-radius:3px;background:var(--c)}
.group h2 small{color:var(--mute);font-weight:400}

article{position:relative;margin:14px 0;background:var(--sheet);border-radius:4px 14px 14px 4px;border-right:4px solid var(--c);padding:20px 22px 18px}
.meta{display:flex;flex-wrap:wrap;gap:4px 12px;align-items:center;color:var(--mute);font-size:13px}
.meta .src{color:var(--soft);font-weight:700}
.badge{border:1px solid currentColor;border-radius:6px;padding:0 7px;font-size:12px;color:var(--c)}
.badge.hot{background:var(--focus);border-color:var(--focus);color:#141a2b;font-weight:700}
.badge.day{color:var(--mute)}
.meta .score{margin-inline-start:auto}
article.ranked .meta,article.ranked h3{padding-left:46px}
.rank{position:absolute;top:18px;left:18px;width:34px;height:34px;border-radius:50%;display:grid;place-items:center;font-weight:800;font-size:15px;background:var(--c);color:#141a2b}
article h3{margin:8px 0 0;font-size:20.5px;line-height:1.65;font-weight:800}
.orig{color:var(--mute);font-size:13.5px;line-height:1.6;margin:2px 0 0;text-align:left;font-family:system-ui,-apple-system,sans-serif}
.tldr{margin:14px 0 0;font-size:17.5px;line-height:1.95;font-weight:700;color:var(--text)}
.new{margin:8px 0 0;color:var(--soft)}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:14px}
@media (max-width:640px){.cols{grid-template-columns:1fr}}
.box{background:var(--sheet-2);border-radius:10px;padding:10px 14px}
.box h4{margin:0 0 4px;font-size:13.5px;color:var(--c);font-weight:700}
.box ul,.act ul{margin:0;padding-inline-start:18px}
.box li,.act li{margin:3px 0;font-size:15px;line-height:1.85}
.act{margin-top:12px;border:1px solid var(--focus);border-radius:10px;padding:10px 14px;background:rgba(242,210,122,.06)}
.act h4{margin:0 0 4px;font-size:13.5px;color:var(--focus);font-weight:800}
.cav{margin-top:10px;color:var(--mute);font-size:14px}
.cav b{color:var(--soft);font-weight:700}
.foot{display:flex;flex-wrap:wrap;gap:8px;align-items:center;justify-content:space-between;margin-top:14px;padding-top:12px;border-top:1px dashed var(--rule)}
.teams{display:flex;gap:6px;flex-wrap:wrap}
.teams span,.tags span{font-size:12px;color:var(--soft);background:var(--ink);padding:1px 9px;border-radius:6px}
.tags span{color:var(--mute)}
.go{display:inline-flex;align-items:center;gap:6px;text-decoration:none;font-weight:700;font-size:14.5px;color:#141a2b;background:var(--focus);padding:6px 16px;border-radius:9px}
.go:hover{filter:brightness(1.08)}
.btns{display:flex;gap:8px;flex-wrap:wrap}
.go.ghost{background:transparent;color:var(--soft);border:1px solid var(--rule)}
.url{display:block;color:var(--mute);font-size:12.5px;margin-top:6px;text-align:left;overflow-wrap:anywhere;text-decoration:none}
.url:hover{color:var(--soft)}
details.more{margin-top:10px}
details.more summary{cursor:pointer;color:var(--soft);font-size:14px;font-weight:700;list-style:none}
details.more summary::-webkit-details-marker{display:none}
details.more summary::before{content:"+";display:inline-block;width:18px;color:var(--c);font-weight:800}
details.more[open] summary::before{content:"−"}

.lead{color:var(--soft);margin:18px 0 0;max-width:70ch}
.empty{display:none;text-align:center;color:var(--mute);padding:40px 0}
.tw{overflow-x:auto;margin-top:20px}
table.src{width:100%;border-collapse:collapse;font-size:14.5px;min-width:620px}
table.src th{white-space:nowrap;text-align:right;color:var(--mute);font-weight:700;padding:8px 10px;border-bottom:1px solid var(--rule)}
table.src td{padding:9px 10px;border-bottom:1px solid var(--rule);vertical-align:top}
.st{font-size:12.5px;padding:1px 8px;border-radius:6px;white-space:nowrap}
.st-active{background:#1f3b34;color:#7fd6bf}.st-new{background:#2a3150;color:#b7bdcc}.st-paused{background:#43361d;color:#f0c674}.st-disabled{background:#47232c;color:#f29aa9}
footer{border-top:1px solid var(--rule);color:var(--mute);font-size:13.5px;padding:22px 0 48px}
@media (max-width:560px){body{font-size:16px}article{padding:18px 16px 14px}article h3{font-size:19px}.tabs{position:static}.rank{display:none}article.ranked .meta,article.ranked h3{padding-left:0}}
</style>
</head>
<body>
{% macro card(a, rank=none) -%}
<article{% if rank %} class="ranked"{% endif %} data-cat="{{ a.category }}" data-text="{{ a.search }}" style="--c:{{ cats[a.category].color }}">
  {% if rank %}<span class="rank">{{ rank }}</span>{% endif %}
  <div class="meta">
    <span class="src">{{ a.source }}</span>
    {% if a.kind == 'paper' %}<span class="badge">مقالهٔ علمی</span>{% elif a.kind == 'tutorial' %}<span class="badge">آموزش</span>{% else %}<span class="badge">تحلیل</span>{% endif %}
    {% if a.level_fa %}<span>{{ a.level_fa }}</span>{% endif %}
    <span>{{ a.read_minutes }} دقیقه</span>
    {% if a.board and not rank %}<span class="badge hot">ورود به لیدربورد</span>{% endif %}
    {% if rank and a.added_short %}<span>ورود: {{ a.added_short }}</span>{% elif a.day and a.day != current %}<span class="badge day">{{ a.day_short }}</span>{% endif %}
    <span class="score" title="امتیاز نهایی بازبینی از 10">{{ a.score }}/10</span>
  </div>
  <h3>{{ a.title_fa }}</h3>
  <p class="orig ltr">{{ a.title }}</p>
  {% if a.tldr %}<p class="tldr">{{ a.tldr }}</p>{% endif %}
  {% if a.about %}<p class="new">{{ a.about }}</p>{% endif %}
  {% if a.for_us %}<div class="act"><h4>کاربرد برای تیم</h4><ul>{% for x in a.for_us[:2] %}<li>{{ x }}</li>{% endfor %}</ul></div>{% endif %}
  <div class="foot">
    <div class="teams">{% for t in a.teams %}<span class="ltr">{{ t }}</span>{% endfor %}</div>
    <span class="btns"><a class="go" href="{{ prefix }}a/{{ a.id }}.html">خواندن بریف کامل</a><a class="go ghost" href="{{ a.url }}" target="_blank" rel="noopener">منبع اصلی</a></span>
  </div>
  <a class="url ltr" href="{{ a.url }}" target="_blank" rel="noopener">{{ a.url }}</a>
</article>
{%- endmacro %}

<header class="mast">
  <div class="wrap">
    <div class="brand"><b>{{ settings.site_title }}</b><span class="ltr">{{ settings.site_subtitle }}</span></div>
    <div class="date">{{ d.long }}</div>
    <div class="greg ltr" style="text-align:right">{{ d.greg }}</div>
    {% if digest.note %}<p class="note">{{ digest.note }}</p>{% endif %}
    <div class="stats">
      <div><strong>{{ digest.articles|length }}</strong>مطلب تازهٔ این نوبت</div>
      <div><strong>{{ digest.reviewed or 0 }}</strong>متن کامل بازبینی‌شده</div>
      <div><strong>{{ digest.candidates }}</strong>مطلب غربال‌شده</div>
      {% if not archive_view %}<div><strong>{{ board_n }}</strong>مطلب در لیدربورد</div>{% endif %}
    </div>
  </div>
</header>

<nav class="tabs" aria-label="بخش‌ها">
  <div class="wrap">
    <div class="tabbar" role="tablist">
      <button class="tab" role="tab" data-tab="latest" aria-selected="true">{% if archive_view %}مطالب این نوبت{% else %}مطالب تازه{% endif %}<span>{{ latest|length }}</span></button>
      {% if archive_view %}
      <a class="tab" href="{{ prefix }}index.html">بازگشت به آخرین نسخه</a>
      {% else %}
      <button class="tab" role="tab" data-tab="board" aria-selected="false">لیدربورد<span>{{ board_n }}</span></button>
      <button class="tab" role="tab" data-tab="sources" aria-selected="false">منابع<span>{{ rows|length }}</span></button>
      {% endif %}
    </div>
    <div class="tools">
      <div class="chips" role="group" aria-label="دسته‌ها"></div>
      <input class="search" type="search" placeholder="جست‌وجو: عنوان، اقدام، تیم (eCRM، Ads ...)" aria-label="جست‌وجو">
      <select class="arch" aria-label="آرشیو نوبت‌ها" onchange="if(this.value)location.href=this.value">
        <option value="{{ prefix }}archive/{{ current }}.html" selected>{{ d.short }}</option>
      </select>
    </div>
  </div>
</nav>

<main class="wrap">
  <section class="panel" data-panel="latest">
    {% if not archive_view and latest|length > digest.articles|length %}<p class="lead">این بخش همیشه {{ latest|length }} مطلب آخر را نشان می‌دهد؛ مطالب نوبت‌های قبل با تاریخ مشخص شده‌اند.</p>{% endif %}
    {% for c in latest_cats %}
    <div class="group" data-group="{{ c.key }}" style="--c:{{ c.color }}">
      <h2>{{ c.fa }} <small class="ltr">{{ c.en }}</small></h2>
      {% for a in latest if a.category == c.key %}{{ card(a) }}{% endfor %}
    </div>
    {% endfor %}
    {% if not latest %}<p class="lead">در این نوبت هیچ مطلبی از بازبینی کیفی عبور نکرد.</p>{% endif %}
    <p class="empty">مطلبی با این فیلتر پیدا نشد.</p>
  </section>

  {% if not archive_view %}
  <section class="panel" data-panel="board" hidden>
    <p class="lead">برترین {{ settings.leaderboard.size }} مطلب هر دسته از ابتدای راه‌اندازی. مطلب تازه با امتیاز حداقل {{ settings.leaderboard.min_score }} فقط وقتی وارد می‌شود که در مقایسهٔ مستقیم با فهرست فعلی برتر باشد.</p>
    {% for c in board_cats %}
    <div class="group" data-group="{{ c.key }}" style="--c:{{ c.color }}">
      <h2>{{ c.fa }} <small class="ltr">{{ c.en }}</small></h2>
      {% for a in board[c.key] %}{{ card(a, loop.index) }}{% endfor %}
    </div>
    {% endfor %}
    {% if not board_cats %}<p class="lead">لیدربورد با اولین مطالب بالای امتیاز {{ settings.leaderboard.min_score }} پر می‌شود.</p>{% endif %}
    <p class="empty">مطلبی با این فیلتر پیدا نشد.</p>
  </section>

  <section class="panel" data-panel="sources" hidden>
    <p class="lead">منابعی که در هر نوبت خوانده می‌شوند. فید خراب خودکار ترمیم یا غیرفعال می‌شود، منبع کم‌کیفیت 30 روز متوقف می‌شود و هر هفته منابع جدید پس از تأیید اضافه می‌شوند.</p>
    <div class="tw"><table class="src">
      <thead><tr><th>منبع</th><th>دسته</th><th>وضعیت</th><th>آخرین مطلب</th><th>میانگین کیفیت</th><th>دفعات انتخاب</th></tr></thead>
      <tbody>
      {% for r in rows %}
      <tr><td class="ltr" style="text-align:right">{{ r.name }}{% if r.origin == 'scout' %} · new{% endif %}{% if r.repaired %} · repaired{% endif %}</td>
        <td>{{ cats[r.hint].fa if r.hint in cats else '' }}</td>
        <td><span class="st st-{{ r.status }}">{{ r.status_fa }}</span></td>
        <td class="ltr" style="text-align:right">{{ r.last_item_short }}</td>
        <td class="ltr" style="text-align:right">{{ r.avg if r.avg is not none else '' }}</td>
        <td class="ltr" style="text-align:right">{{ r.picks }}</td></tr>
      {% endfor %}
      </tbody>
    </table></div>
  </section>
  {% endif %}
</main>

<footer><div class="wrap">به‌روزرسانی: {{ updated }} به وقت تهران. خلاصه‌ها با AI تولید شده‌اند؛ برای تصمیم‌گیری، منبع اصلی مرجع است.</div></footer>

<script>
(function(){
  var CATS={{ cats|tojson }};
  var tabs=[].slice.call(document.querySelectorAll('[data-tab]')), panels=[].slice.call(document.querySelectorAll('[data-panel]'));
  var chipsEl=document.querySelector('.chips'), q=document.querySelector('.search'), sel=document.querySelector('.arch');
  var state={tab:'latest',cat:'all'};
  function panel(){return document.querySelector('[data-panel="'+state.tab+'"]');}
  function buildChips(){
    var p=panel(), arts=[].slice.call(p.querySelectorAll('article')), counts={};
    arts.forEach(function(a){counts[a.dataset.cat]=(counts[a.dataset.cat]||0)+1;});
    var h='<button class="chip" data-cat="all" aria-pressed="'+(state.cat==='all')+'">همه<span>'+arts.length+'</span></button>';
    Object.keys(CATS).forEach(function(k){ if(counts[k]) h+='<button class="chip" data-cat="'+k+'" aria-pressed="'+(state.cat===k)+'" style="--c:'+CATS[k].color+'">'+CATS[k].fa+'<span>'+counts[k]+'</span></button>'; });
    chipsEl.innerHTML=h; chipsEl.style.display=(state.tab==='sources')?'none':'flex'; q.style.display=(state.tab==='sources')?'none':'';
    sel.style.display=(state.tab==='latest')?'':'none';
  }
  function apply(){
    var p=panel(), term=(q.value||'').trim().toLowerCase(), shown=0, arts=[].slice.call(p.querySelectorAll('article'));
    arts.forEach(function(a){var ok=(state.cat==='all'||a.dataset.cat===state.cat)&&(!term||a.dataset.text.indexOf(term)>-1);a.hidden=!ok;if(ok)shown++;});
    [].forEach.call(p.querySelectorAll('.group'),function(g){g.hidden=!g.querySelector('article:not([hidden])');});
    var e=p.querySelector('.empty'); if(e)e.style.display=(arts.length&&!shown)?'block':'none';
  }
  function show(name,push){
    if(!document.querySelector('[data-panel="'+name+'"]'))name='latest';
    state.tab=name; state.cat='all';
    tabs.forEach(function(t){t.setAttribute('aria-selected',t.dataset.tab===name?'true':'false');});
    panels.forEach(function(p){p.hidden=p.dataset.panel!==name;});
    buildChips(); apply();
    if(push)history.replaceState(null,'',name==='latest'?location.pathname:'#'+name);
  }
  tabs.forEach(function(t){t.addEventListener('click',function(){show(t.dataset.tab,true);window.scrollTo({top:document.querySelector('.tabs').offsetTop,behavior:'smooth'});});});
  chipsEl.addEventListener('click',function(e){var c=e.target.closest('.chip');if(!c)return;state.cat=c.dataset.cat;
    [].forEach.call(chipsEl.children,function(x){x.setAttribute('aria-pressed',x===c?'true':'false');});apply();});
  q.addEventListener('input',apply);
  var prefix={{ prefix|tojson }}, cur={{ current|tojson }};
  fetch(prefix+'archive.json').then(function(r){return r.json();}).then(function(list){
    sel.innerHTML='';
    list.forEach(function(a,i){var o=document.createElement('option');o.value=prefix+'archive/'+a.date+'.html';
      o.textContent=a.short+(i===0?' (آخرین)':'');if(a.date===cur)o.selected=true;sel.appendChild(o);});
  }).catch(function(){});
  show((location.hash||'').replace('#','')||'latest',false);
})();
</script>
</body>
</html>
"""


BRIEF = r"""<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{{ a.title_fa }} | {{ settings.site_title }}</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📄</text></svg>">
<style>
{{ fonts | safe }}
:root{--ink:#121829;--sheet:#182036;--sheet-2:#1E2741;--rule:#2B3554;--text:#ECE9E2;--soft:#BCC2D0;--mute:#8189A0;--focus:#F2D27A;--c:{{ c.color }};
  box-sizing:border-box;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}
*,*::before,*::after{box-sizing:inherit}
body{margin:0;background:var(--ink);color:var(--text);font-family:'Vazirmatn',Tahoma,sans-serif;font-size:17px;line-height:2.05;-webkit-font-smoothing:antialiased}
a{color:inherit}
:focus-visible{outline:2px solid var(--focus);outline-offset:3px;border-radius:4px}
.wrap{max-width:740px;margin:0 auto;padding:28px 22px 70px}
.ltr{direction:ltr;unicode-bidi:isolate}
.back{display:inline-block;color:var(--mute);text-decoration:none;font-size:14.5px;margin-bottom:22px}
.back:hover{color:var(--text)}
.cat{color:var(--c);font-weight:700;font-size:14.5px}
.meta{display:flex;flex-wrap:wrap;gap:4px 14px;color:var(--mute);font-size:14px;margin-top:6px}
.meta b{color:var(--soft)}
h1{font-size:clamp(25px,5vw,33px);line-height:1.55;font-weight:800;margin:10px 0 4px}
.orig{color:var(--mute);font-size:14.5px;line-height:1.6;margin:0;text-align:left;font-family:system-ui,-apple-system,sans-serif}
.tldr{margin:26px 0 8px;padding:16px 20px;border-right:4px solid var(--c);background:var(--sheet);border-radius:4px 12px 12px 4px;font-size:18.5px;font-weight:700;line-height:2}
h2{font-size:16px;font-weight:800;color:var(--c);margin:34px 0 6px}
p{margin:0 0 8px}
ul{margin:4px 0 0;padding-inline-start:22px}
li{margin:6px 0}
.us{margin-top:34px;border:1px solid var(--focus);border-radius:12px;padding:14px 20px;background:rgba(242,210,122,.06)}
.us h2{color:var(--focus);margin-top:0}
.cav{color:var(--soft);font-size:15.5px}
.tags{display:flex;gap:6px;flex-wrap:wrap;margin-top:28px}
.tags span{font-size:12.5px;color:var(--soft);background:var(--sheet);padding:2px 10px;border-radius:6px}
.source{margin-top:30px;padding:16px 20px;background:var(--sheet);border-radius:12px}
.go{display:inline-block;text-decoration:none;font-weight:700;font-size:15px;color:#141a2b;background:var(--focus);padding:8px 18px;border-radius:9px}
.url{display:block;margin-top:10px;color:var(--mute);font-size:13px;text-align:left;overflow-wrap:anywhere;text-decoration:none}
footer{color:var(--mute);font-size:13.5px;margin-top:30px}
</style>
</head>
<body>
<main class="wrap">
  <a class="back" href="../index.html">بازگشت به دایجست</a>
  <div class="cat">{{ c.fa }} <span class="ltr" style="color:var(--mute);font-weight:400">{{ c.en }}</span></div>
  <h1>{{ a.title_fa }}</h1>
  <p class="orig ltr">{{ a.title }}</p>
  <div class="meta">
    <span><b>{{ a.source }}</b></span>
    <span>{% if a.kind == 'paper' %}مقالهٔ علمی{% elif a.kind == 'tutorial' %}آموزش{% else %}تحلیل{% endif %}</span>
    {% if a.level_fa %}<span>سطح: {{ a.level_fa }}</span>{% endif %}
    {% if published %}<span>انتشار: {{ published }}</span>{% endif %}
    <span>متن اصلی: {{ a.read_minutes }} دقیقه</span>
    <span>امتیاز: {{ a.score }}/10</span>
  </div>
  {% if a.tldr %}<div class="tldr">{{ a.tldr }}</div>{% endif %}

  {% if a.about %}<h2>درباره چیست</h2><p>{{ a.about }}</p>{% endif %}
  {% if a.problem %}<h2>مسئله و اهمیت</h2><p>{{ a.problem }}</p>{% endif %}
  {% if a.approach %}<h2>روی چه چیزی کار کرده‌اند</h2><p>{{ a.approach }}</p>{% endif %}
  {% if a.steps %}<h2>{% if a.kind == 'tutorial' %}مراحل{% else %}روش و اجزا{% endif %}</h2><ul>{% for x in a.steps %}<li>{{ x }}</li>{% endfor %}</ul>{% endif %}
  {% if a.findings %}<h2>یافته‌ها و اعداد</h2><ul>{% for x in a.findings %}<li>{{ x }}</li>{% endfor %}</ul>{% endif %}
  {% if a.conclusion %}<h2>نتیجه‌گیری</h2><p>{{ a.conclusion }}</p>{% endif %}
  {% if a.for_us %}<div class="us"><h2>کاربرد برای تیم</h2><ul>{% for x in a.for_us %}<li>{{ x }}</li>{% endfor %}</ul></div>{% endif %}
  {% if a.caveats %}<h2>محدودیت‌ها</h2><ul class="cav">{% for x in a.caveats %}<li>{{ x }}</li>{% endfor %}</ul>{% endif %}
  {% if a.teams or a.tags %}<div class="tags">{% for t in a.teams %}<span class="ltr">{{ t }}</span>{% endfor %}{% for t in a.tags %}<span class="ltr">{{ t }}</span>{% endfor %}</div>{% endif %}

  <div class="source">
    <a class="go" href="{{ a.url }}" target="_blank" rel="noopener">خواندن منبع اصلی</a>
    <a class="url ltr" href="{{ a.url }}" target="_blank" rel="noopener">{{ a.url }}</a>
  </div>
  <footer>این بریف با AI از متن منبع تهیه شده است؛ برای تصمیم‌گیری، منبع اصلی مرجع است.</footer>
</main>
</body>
</html>
"""
