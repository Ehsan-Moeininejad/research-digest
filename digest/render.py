"""Render the static site: index (latest N), archive/<date>, leaderboard, sources."""
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


def jalali(iso_date: str) -> dict:
    g = dt.date.fromisoformat(iso_date[:10])
    j = jdatetime.date.fromgregorian(date=g)
    return {
        "long": f"{J_DAYS[g.weekday()]} {j.day} {J_MONTHS[j.month - 1]} {j.year}",
        "short": f"{j.year}/{j.month:02d}/{j.day:02d}",
        "greg": g.strftime("%d %b %Y"),
    }


def font_css(root: Path, prefix: str) -> str:
    """Copy Vazirmatn into docs/fonts once and reference it, so daily pages stay small."""
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


def _cats_present(settings, articles):
    out = []
    for key, meta in settings["categories"].items():
        n = sum(1 for a in articles if a["category"] == key)
        if n:
            out.append({"key": key, "n": n, **meta})
    return out


def render_site(root: Path, settings: dict, board: dict | None = None, source_rows: list | None = None):
    """Old archive pages are never rewritten (their menu reads archive.json), keeping git history small."""
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
    updated = dt.datetime.now(TEHRAN).strftime("%H:%M")
    base = dict(settings=settings, cats=settings["categories"], updated=updated)

    digests = {}

    def load(f):
        if f.stem not in digests:
            digests[f.stem] = json.loads(f.read_text(encoding="utf-8"))
        return digests[f.stem]

    # daily archive pages (only today's and any missing ones)
    for i, f in enumerate(files):
        if i and (arch_dir / f"{f.stem}.html").exists():
            continue
        dg = load(f)
        (arch_dir / f"{f.stem}.html").write_text(tpl.render(
            **base, view="day", prefix="../", fonts=font_css(root, "../"), page_title=jalali(dg["date"])["short"],
            digest=dg, d=jalali(dg["date"]), current=dg["date"], shown=dg["articles"],
            total=len(dg["articles"]), cats_present=_cats_present(settings, dg["articles"])), encoding="utf-8")

    # index: always the latest N articles across days
    n = settings["selection"].get("latest_size", 10)
    latest = []
    for f in files:
        for a in load(f)["articles"]:
            if len(latest) < n:
                latest.append(dict(a, day=f.stem, day_short=jalali(f.stem)["short"]))
        if len(latest) >= n:
            break
    today = load(files[0])
    (docs / "index.html").write_text(tpl.render(
        **base, view="today", prefix="", fonts=font_css(root, ""), page_title=jalali(today["date"])["short"],
        digest=today, d=jalali(today["date"]), current=today["date"], shown=latest, total=len(latest),
        cats_present=_cats_present(settings, latest)), encoding="utf-8")

    # leaderboard
    board = board or {}
    flat = []
    for key in settings["categories"]:
        for e in board.get(key, []):
            e["added_short"] = jalali(e.get("added", today["date"]))["short"]
            flat.append(e)
    (docs / "leaderboard.html").write_text(tpl.render(
        **base, view="board", prefix="", fonts=font_css(root, ""), page_title="لیدربورد",
        board=board, total=len(flat), cats_present=_cats_present(settings, flat), current=None), encoding="utf-8")

    # sources
    rows = source_rows or []
    for r in rows:
        r["last_item_short"] = jalali(r["last_item"])["short"] if r.get("last_item") else ""
    (docs / "sources.html").write_text(tpl.render(
        **base, view="sources", prefix="", fonts=font_css(root, ""), page_title="منابع",
        rows=rows, total=0, cats_present=[], current=None), encoding="utf-8")
    (docs / ".nojekyll").write_text("")


TEMPLATE = r"""<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{{ settings.site_title }} | {{ page_title }}</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📚</text></svg>">
<style>
{{ fonts | safe }}
:root{
  --ink:#121829; --sheet:#182036; --sheet-2:#1E2741; --rule:#2B3554;
  --text:#ECE9E2; --soft:#B7BDCC; --mute:#8189A0; --focus:#F2D27A;
  box-sizing:border-box;
  padding-top:env(safe-area-inset-top,0px); padding-bottom:env(safe-area-inset-bottom,0px);
}
*,*::before,*::after{box-sizing:inherit}
html{scroll-padding-top:calc(env(safe-area-inset-top,0px) + 72px)}
body{margin:0;background:var(--ink);color:var(--text);
  font-family:'Vazirmatn',Tahoma,'Segoe UI',sans-serif;font-size:17px;line-height:1.9;
  -webkit-font-smoothing:antialiased}
a{color:inherit}
:focus-visible{outline:2px solid var(--focus);outline-offset:3px;border-radius:4px}
.wrap{max-width:780px;margin:0 auto;padding:0 20px}

/* masthead */
.mast{padding:56px 0 28px;border-bottom:1px solid var(--rule)}
.brand{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap;color:var(--mute);font-size:14px}
.brand b{color:var(--soft);font-weight:700}
.brand .en{direction:ltr;letter-spacing:.02em}
.date{margin:22px 0 4px;font-size:clamp(34px,7vw,52px);font-weight:800;line-height:1.25;letter-spacing:-.01em}
.greg{color:var(--mute);font-size:15px;direction:ltr;text-align:right}
.note{margin:26px 0 0;font-size:19px;line-height:2;color:var(--soft);max-width:66ch}
.stats{display:flex;gap:28px;margin-top:22px;color:var(--mute);font-size:14px}
.stats strong{display:block;color:var(--text);font-size:22px;font-weight:700;line-height:1.4}

/* toolbar */
.bar{position:sticky;top:env(safe-area-inset-top,0px);z-index:5;background:rgba(18,24,41,.94);
  backdrop-filter:blur(8px);border-bottom:1px solid var(--rule)}
.bar .wrap{display:flex;gap:10px;align-items:center;padding-top:12px;padding-bottom:12px;flex-wrap:wrap}
.chips{display:flex;gap:6px;overflow-x:auto;flex:1 1 100%;scrollbar-width:none;padding-bottom:2px}
.chips::-webkit-scrollbar{display:none}
.chip{flex:none;border:1px solid var(--rule);background:transparent;color:var(--soft);font:inherit;font-size:14px;
  padding:4px 12px;border-radius:999px;cursor:pointer;white-space:nowrap}
.chip[aria-pressed="true"]{background:var(--c,#ECE9E2);border-color:var(--c,#ECE9E2);color:#141a2b;font-weight:700}
.chip span{opacity:.7;margin-inline-start:4px}
.search{flex:1 1 220px;min-width:0;background:var(--sheet);border:1px solid var(--rule);color:var(--text);
  font:inherit;font-size:15px;padding:7px 14px;border-radius:10px}
.search::placeholder{color:var(--mute)}
select{background:var(--sheet);border:1px solid var(--rule);color:var(--soft);font:inherit;font-size:14px;
  padding:7px 10px;border-radius:10px;max-width:46%}

/* articles */
main{padding:10px 0 60px}
.group{margin-top:40px}
.group h2{display:flex;align-items:center;gap:10px;font-size:15px;font-weight:700;color:var(--c);margin:0 0 6px}
.group h2::before{content:"";width:10px;height:10px;border-radius:3px;background:var(--c)}
.group h2 small{color:var(--mute);font-weight:400;direction:ltr}
article{position:relative;padding:22px 22px 18px 20px;margin:14px 0;background:var(--sheet);
  border-radius:4px 14px 14px 4px;border-right:4px solid var(--c)}
.meta{display:flex;flex-wrap:wrap;gap:6px 14px;color:var(--mute);font-size:13.5px}
.meta .src{color:var(--soft);font-weight:700}
.meta .paper{color:var(--c);border:1px solid currentColor;border-radius:6px;padding:0 7px;font-size:12px}
.meta .score{margin-inline-start:auto;direction:ltr}
article h3{margin:8px 0 2px;font-size:21px;line-height:1.6;font-weight:700}
article h3 a{text-decoration:none}
article h3 a:hover{text-decoration:underline;text-underline-offset:5px;text-decoration-color:var(--c)}
.orig{direction:ltr;text-align:left;color:var(--mute);font-size:14px;line-height:1.6;margin:0 0 10px;font-family:system-ui,sans-serif}
.sum{margin:10px 0;color:var(--text)}
details{margin-top:8px;border-top:1px dashed var(--rule);padding-top:8px}
summary{cursor:pointer;color:var(--soft);font-size:14.5px;font-weight:700;list-style:none}
summary::-webkit-details-marker{display:none}
summary::before{content:"+";display:inline-block;width:18px;color:var(--c);font-weight:800}
details[open] summary::before{content:"−"}
details ul{margin:8px 0 4px;padding-inline-start:22px}
details li{margin:4px 0}
.why{margin:12px 0 2px;padding:12px 14px;background:var(--sheet-2);border-radius:10px;font-size:15.5px}
.why b{color:var(--c)}
.tags{display:flex;gap:6px;flex-wrap:wrap;margin-top:12px;direction:ltr;justify-content:flex-end}
.tags span{font-size:12px;color:var(--mute);background:var(--ink);padding:1px 8px;border-radius:6px;font-family:system-ui,sans-serif}
.empty{display:none;text-align:center;color:var(--mute);padding:40px 0}
footer{border-top:1px solid var(--rule);color:var(--mute);font-size:13.5px;padding:24px 0 48px}
@media (max-width:560px){
  body{font-size:16px} .mast{padding-top:36px} article{padding:18px 16px 14px}
  article h3{font-size:19px}
  .bar{position:static} .search{flex:1 1 55%} select{flex:1 1 35%;max-width:45%}
}
@media (prefers-reduced-motion:no-preference){
  .mast .date,.mast .note{animation:rise .6s cubic-bezier(.2,.7,.2,1) both}
  .mast .note{animation-delay:.12s}
  @keyframes rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
}

/* nav */
.nav{display:flex;gap:4px;margin-top:18px;flex-wrap:wrap}
.nav a{text-decoration:none;color:var(--soft);font-size:15px;padding:5px 14px;border-radius:999px;border:1px solid var(--rule)}
.nav a[aria-current="page"]{background:var(--text);color:#141a2b;border-color:var(--text);font-weight:700}
.lead{margin:14px 0 0;color:var(--soft);max-width:66ch}
.day{color:var(--mute);font-size:13px;border:1px solid var(--rule);border-radius:6px;padding:0 7px}
.board{color:#141a2b;background:var(--focus);border-radius:6px;padding:0 7px;font-size:12px;font-weight:700}
/* leaderboard */
ol.rank{list-style:none;margin:0;padding:0;counter-reset:r}
ol.rank>li{counter-increment:r;position:relative;padding:16px 58px 14px 18px;margin:10px 0;background:var(--sheet);border-radius:12px}
ol.rank>li::before{content:counter(r);position:absolute;right:14px;top:14px;width:32px;height:32px;border-radius:50%;
  display:grid;place-items:center;font-weight:800;font-size:15px;color:#141a2b;background:var(--c)}
ol.rank>li:nth-child(n+4)::before{background:transparent;color:var(--c);border:1.5px solid var(--c)}
ol.rank h3{font-size:18px;margin:4px 0 2px}
ol.rank h3 a{text-decoration:none}
ol.rank h3 a:hover{text-decoration:underline;text-underline-offset:5px;text-decoration-color:var(--c)}
ol.rank .sum{font-size:15.5px;color:var(--soft);margin:8px 0 0}
.empty-cat{color:var(--mute);font-size:15px;padding:8px 0}
/* sources */
.tablewrap{overflow-x:auto;margin-top:24px}
table.src{width:100%;border-collapse:collapse;font-size:14.5px;min-width:620px}
table.src th{white-space:nowrap;text-align:right;color:var(--mute);font-weight:700;padding:8px 10px;border-bottom:1px solid var(--rule)}
table.src td{padding:9px 10px;border-bottom:1px solid var(--rule);vertical-align:top}
table.src td.ltr{direction:ltr;text-align:right}
.st{font-size:12.5px;padding:1px 8px;border-radius:6px;white-space:nowrap}
.st-active{background:#1f3b34;color:#7fd6bf}.st-new{background:#2a3150;color:#b7bdcc}
.st-paused{background:#43361d;color:#f0c674}.st-disabled{background:#47232c;color:#f29aa9}
.legend{color:var(--mute);font-size:14px;margin-top:14px;max-width:70ch}
</style>
</head>
<body>
{% macro card(a, show_day=false) -%}
    <article data-cat="{{ a.category }}" data-text="{{ (a.title_fa ~ ' ' ~ a.title ~ ' ' ~ a.summary ~ ' ' ~ (a.tags or [])|join(' ') ~ ' ' ~ a.source)|lower }}">
      <div class="meta">
        <span class="src">{{ a.source }}</span>
        {% if a.kind == 'paper' %}<span class="paper">مقالهٔ علمی</span>{% elif a.kind == 'tutorial' %}<span class="paper">آموزش</span>{% endif %}
        {% if a.board %}<span class="board">ورود به لیدربورد</span>{% endif %}
        {% if show_day and a.day != current %}<span class="day">{{ a.day_short }}</span>{% endif %}
        <span>{{ a.read_minutes }} دقیقه مطالعه</span>
        <span class="score" title="امتیاز نهایی بازبینی از 10">{{ a.score }}/10</span>
      </div>
      <h3><a href="{{ a.url }}" target="_blank" rel="noopener">{{ a.title_fa }}</a></h3>
      <p class="orig">{{ a.title }}</p>
      <p class="sum">{{ a.summary }}</p>
      {% if a.why_it_matters %}<div class="why"><b>کاربرد برای تیم:</b> {{ a.why_it_matters }}</div>{% endif %}
      {% if a.key_points %}
      <details><summary>نکات کلیدی</summary>
        <ul>{% for k in a.key_points %}<li>{{ k }}</li>{% endfor %}</ul>
      </details>
      {% endif %}
      {% if a.tags %}<div class="tags">{% for t in a.tags %}<span>{{ t }}</span>{% endfor %}</div>{% endif %}
    </article>
{%- endmacro %}

<header class="mast">
  <div class="wrap">
    <div class="brand"><b>{{ settings.site_title }}</b><span class="en">{{ settings.site_subtitle }}</span></div>
    <nav class="nav" aria-label="بخش‌ها">
      <a href="{{ prefix }}index.html" {% if view in ('today','day') %}aria-current="page"{% endif %}>مطالب تازه</a>
      <a href="{{ prefix }}leaderboard.html" {% if view == 'board' %}aria-current="page"{% endif %}>لیدربورد</a>
      <a href="{{ prefix }}sources.html" {% if view == 'sources' %}aria-current="page"{% endif %}>منابع</a>
    </nav>
    {% if view in ('today','day') %}
    <div class="date">{{ d.long }}</div>
    <div class="greg">{{ d.greg }}</div>
    {% if digest.note %}<p class="note">{{ digest.note }}</p>{% endif %}
    <div class="stats">
      <div><strong>{{ digest.articles|length }}</strong>مطلب تازهٔ این نوبت</div>
      <div><strong>{{ digest.reviewed or digest.candidates }}</strong>متن کامل بازبینی‌شده</div>
      <div><strong>{{ digest.candidates }}</strong>مطلب غربال‌شده</div>
      <div><strong>{{ digest.articles|selectattr('board')|list|length }}</strong>ورود به لیدربورد</div>
    </div>
    {% if view == 'today' and shown|length > digest.articles|length %}<p class="lead">صفحه همیشه {{ shown|length }} مطلب آخر را نشان می‌دهد؛ مطالب نوبت‌های قبل با تاریخ مشخص شده‌اند.</p>{% endif %}
    {% elif view == 'board' %}
    <div class="date">لیدربورد</div>
    <p class="note">برترین {{ settings.leaderboard.size }} مطلب هر دسته از ابتدای راه‌اندازی. مطلب تازه فقط وقتی وارد می‌شود که در مقایسهٔ مستقیم با فهرست فعلی برتر باشد و امتیاز نهایی آن حداقل {{ settings.leaderboard.min_score }} باشد.</p>
    {% else %}
    <div class="date">منابع</div>
    <p class="note">فهرست زندهٔ منابعی که در هر نوبت (شنبه و سه‌شنبه) خوانده می‌شوند. فید خراب خودکار ترمیم یا غیرفعال می‌شود، منبع کم‌کیفیت متوقف می‌شود و هر هفته منابع جدید پس از تأیید زنده بودن اضافه می‌شوند.</p>
    {% endif %}
  </div>
</header>

{% if view != 'sources' %}
<nav class="bar" aria-label="فیلتر">
  <div class="wrap">
    <div class="chips" role="group" aria-label="دسته‌ها">
      <button class="chip" data-cat="all" aria-pressed="true">همه<span>{{ total }}</span></button>
      {% for c in cats_present %}
      <button class="chip" data-cat="{{ c.key }}" aria-pressed="false" style="--c:{{ c.color }}">{{ c.fa }}<span>{{ c.n }}</span></button>
      {% endfor %}
    </div>
    <input class="search" type="search" placeholder="جست‌وجو در عنوان، خلاصه و تگ‌ها" aria-label="جست‌وجو">
    {% if view in ('today','day') %}
    <select class="arch" aria-label="آرشیو روزها" onchange="if(this.value)location.href=this.value">
      <option value="{{ prefix }}archive/{{ current }}.html" selected>{{ d.short }}</option>
    </select>
    {% endif %}
  </div>
</nav>
{% endif %}

<main class="wrap">
{% if view in ('today','day') %}
  {% for c in cats_present %}
  <section class="group" data-group="{{ c.key }}" style="--c:{{ c.color }}">
    <h2>{{ c.fa }} <small>{{ c.en }}</small></h2>
    {% for a in shown if a.category == c.key %}{{ card(a, view == 'today') }}{% endfor %}
  </section>
  {% endfor %}
  {% if not shown %}<p class="empty-cat">در این نوبت هیچ مطلبی از بازبینی کیفی عبور نکرد. مطالب نوبت‌های قبل از منوی آرشیو در دسترس است.</p>{% endif %}
{% elif view == 'board' %}
  {% for c in cats_present %}
  <section class="group" data-group="{{ c.key }}" style="--c:{{ c.color }}">
    <h2>{{ c.fa }} <small>{{ c.en }}</small></h2>
    <ol class="rank">
    {% for a in board[c.key] %}
      <li data-cat="{{ c.key }}" data-text="{{ (a.title_fa ~ ' ' ~ a.title ~ ' ' ~ a.summary ~ ' ' ~ (a.tags or [])|join(' ') ~ ' ' ~ a.source)|lower }}">
        <div class="meta">
          <span class="src">{{ a.source }}</span>
          {% if a.kind == 'paper' %}<span class="paper">مقالهٔ علمی</span>{% elif a.kind == 'tutorial' %}<span class="paper">آموزش</span>{% endif %}
          <span>ورود: {{ a.added_short }}</span>
          <span class="score">{{ a.score }}/10</span>
        </div>
        <h3><a href="{{ a.url }}" target="_blank" rel="noopener">{{ a.title_fa }}</a></h3>
        <p class="orig">{{ a.title }}</p>
        <details><summary>خلاصه و کاربرد</summary>
          <p class="sum">{{ a.summary }}</p>
          {% if a.why_it_matters %}<div class="why"><b>کاربرد برای تیم:</b> {{ a.why_it_matters }}</div>{% endif %}
          {% if a.key_points %}<ul>{% for k in a.key_points %}<li>{{ k }}</li>{% endfor %}</ul>{% endif %}
        </details>
      </li>
    {% endfor %}
    </ol>
  </section>
  {% endfor %}
  {% if not cats_present %}<p class="empty-cat">لیدربورد با اولین مطالب بالای امتیاز {{ settings.leaderboard.min_score }} پر می‌شود.</p>{% endif %}
{% else %}
  <div class="tablewrap">
  <table class="src">
    <thead><tr><th>منبع</th><th>دسته</th><th>وضعیت</th><th>آخرین مطلب</th><th>میانگین کیفیت</th><th>دفعات انتخاب</th></tr></thead>
    <tbody>
    {% for r in rows %}
      <tr>
        <td class="ltr">{{ r.name }}{% if r.origin == 'scout' %} <span class="day">جدید</span>{% endif %}{% if r.repaired %} <span class="day">ترمیم‌شده</span>{% endif %}</td>
        <td>{{ cats[r.hint].fa if r.hint in cats else '' }}</td>
        <td><span class="st st-{{ r.status }}">{{ {'active':'فعال','new':'در انتظار اولین اجرا','paused':'متوقف (کیفیت)','disabled':'غیرفعال (خطا)'}[r.status] }}</span></td>
        <td class="ltr">{{ r.last_item_short }}</td>
        <td class="ltr">{{ r.avg if r.avg is not none else '' }}</td>
        <td class="ltr">{{ r.picks }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
  <p class="legend">منابع متوقف‌شده بعد از 30 روز و منابع غیرفعال هفته‌ای یک بار دوباره امتحان می‌شوند. افزودن یا حذف دستی منبع از فایل config/sources.yaml انجام می‌شود.</p>
{% endif %}
  <p class="empty">مطلبی با این فیلتر پیدا نشد. فیلتر «همه» را انتخاب کنید یا عبارت دیگری جست‌وجو کنید.</p>
</main>

<footer>
  <div class="wrap">به‌روزرسانی: {{ updated }} به وقت تهران. خلاصه‌ها با AI تولید شده‌اند؛ برای تصمیم‌گیری، اصل مقاله مرجع است.</div>
</footer>

<script>
(function(){
  var chips=[].slice.call(document.querySelectorAll('.chip'));
  var arts=[].slice.call(document.querySelectorAll('[data-text]'));
  var groups=[].slice.call(document.querySelectorAll('.group'));
  var q=document.querySelector('.search'), empty=document.querySelector('.empty'), cat='all';
  function apply(){
    var term=(q&&q.value||'').trim().toLowerCase(), shown=0;
    arts.forEach(function(a){
      var ok=(cat==='all'||a.dataset.cat===cat)&&(!term||a.dataset.text.indexOf(term)>-1);
      a.hidden=!ok; if(ok)shown++;
    });
    groups.forEach(function(g){g.hidden=!g.querySelector('[data-text]:not([hidden])');});
    empty.style.display=(shown||!arts.length)?'none':'block';
  }
  chips.forEach(function(c){c.addEventListener('click',function(){
    cat=c.dataset.cat; chips.forEach(function(x){x.setAttribute('aria-pressed',x===c?'true':'false');}); apply();
  });});
  if(q)q.addEventListener('input',apply);
  var sel=document.querySelector('.arch'), prefix={{ prefix|tojson }}, cur={{ (current or '')|tojson }};
  if(sel)fetch(prefix+'archive.json').then(function(r){return r.json();}).then(function(list){
    sel.innerHTML='';
    list.forEach(function(a,i){
      var o=document.createElement('option'); o.value=prefix+'archive/'+a.date+'.html';
      o.textContent=a.short+(i===0?' (آخرین)':''); if(a.date===cur)o.selected=true; sel.appendChild(o);
    });
  }).catch(function(){});
})();
</script>
</body>
</html>
"""
