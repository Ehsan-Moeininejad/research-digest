"""Send the 'digest updated' email through Gmail SMTP (App Password)."""
import html
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from render import jalali

log = logging.getLogger("digest")
FONT = "Vazirmatn,Vazir,'Vazir Matn',Tahoma,'Segoe UI',Arial,sans-serif"
FA = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def t(x) -> str:
    return html.escape(str(x or "").translate(FA))


def send_email(digest: dict, settings: dict):
    user, pwd = os.environ["SMTP_USER"], os.environ["SMTP_PASS"]
    to = [x.strip() for x in os.environ["MAIL_TO"].split(",") if x.strip()]
    page = os.getenv("PAGE_URL", "").strip()
    if not page and os.getenv("GITHUB_REPOSITORY"):
        owner, repo = os.environ["GITHUB_REPOSITORY"].split("/", 1)
        page = f"https://{owner.lower()}.github.io/{repo}/"
    page = page.rstrip("/") + "/"
    d = jalali(digest["date"])
    cats = settings["categories"]
    arts = sorted(digest["articles"], key=lambda x: -x["score"])
    n = len(arts)
    nb = sum(1 for a in arts if a.get("board"))

    def short(x, n):
        x = str(x or "").strip()
        return x if len(x) <= n else x[:n].rsplit(" ", 1)[0] + "…"

    def section(title, sub=""):
        return (f'<tr><td dir="rtl" style="padding:26px 0 4px;text-align:right;font-family:{FONT}">'
                f'<div style="font-size:18px;font-weight:bold;color:#F2D27A">{t(title)}</div>'
                + (f'<div style="font-size:13px;color:#8189A0;margin-top:2px">{t(sub)}</div>' if sub else "")
                + "</td></tr>")

    items = [section("مطالب تازهٔ امروز",
                     f"{n} مطلب از بازبینی کیفی عبور کرد" if n else "امروز مطلب تازه‌ای از بازبینی کیفی عبور نکرد")]
    for i, a in enumerate(arts, 1):
        c = cats[a["category"]]
        brief = f"{page}a/{a['id']}.html"
        about = short(a.get("about") or a.get("summary"), 420)
        concl = short(a.get("conclusion"), 320)
        use = (a.get("for_us") or a.get("actions") or [""])[0]
        items.append(f"""
<tr><td dir="rtl" style="padding:18px 0;border-bottom:1px solid #2B3554;text-align:right;font-family:{FONT}">
  <div style="font-size:12.5px;color:{c['color']};font-weight:bold">{i}. {t(c['fa'])}
    <span style="color:#8189A0;font-weight:normal">· {t(a['source'])}</span></div>
  <div style="margin:6px 0 6px;font-size:17px;line-height:1.8;font-weight:bold;color:#ECE9E2">{t(a['title_fa'])}</div>
  <div style="font-size:15px;line-height:1.95;color:#ECE9E2;font-weight:bold">{t(a.get('tldr'))}</div>
  {f'<div style="margin-top:8px;font-size:14px;line-height:1.95;color:#BCC2D0"><b style="color:#8189A0">درباره:</b> {t(about)}</div>' if about else ''}
  {f'<div style="margin-top:6px;font-size:14px;line-height:1.95;color:#BCC2D0"><b style="color:#8189A0">نتیجه‌گیری:</b> {t(concl)}</div>' if concl else ''}
  {f'<div style="margin-top:6px;font-size:14px;line-height:1.95;color:#F2D27A"><b>کاربرد:</b> {t(use)}</div>' if use else ''}
  <div style="margin-top:12px">
    <a href="{t(brief)}" style="display:inline-block;background:#F2D27A;color:#141a2b;font-weight:bold;font-size:13.5px;padding:7px 14px;border-radius:8px;text-decoration:none">خواندن بریف کامل</a>
    &nbsp;<a href="{t(a['url'])}" style="color:#8189A0;font-size:13px">منبع اصلی</a>
  </div>
</td></tr>""")

    bn = digest.get("board_new") or []
    if bn:
        items.append(section("ورودی‌های جدید لیدربورد", f"{len(bn)} مطلب مرجع وارد برترین‌های دسته‌ها شد"))
        for e in sorted(bn, key=lambda x: (list(cats).index(x["category"]) if x["category"] in cats else 99, x.get("rank", 99))):
            c = cats.get(e["category"], {"fa": e["category"], "color": "#BCC2D0"})
            items.append(f"""
<tr><td dir="rtl" style="padding:12px 0;border-bottom:1px solid #2B3554;text-align:right;font-family:{FONT}">
  <div style="font-size:12.5px;color:{c['color']};font-weight:bold">{t(c['fa'])} · رتبهٔ {e.get('rank', '')}
    <span style="color:#8189A0;font-weight:normal">· {t(e['source'])}</span></div>
  <div style="margin:4px 0;font-size:15.5px;line-height:1.8;font-weight:bold;color:#ECE9E2">
    <a href="{t(page + 'a/' + e['id'] + '.html')}" style="color:#ECE9E2;text-decoration:none">{t(e['title_fa'])}</a></div>
  <div style="font-size:13.5px;line-height:1.9;color:#BCC2D0">{t(e.get('tldr'))}</div>
</td></tr>""")

    events = digest.get("events") or []
    ev = ""
    if events:
        lis = "".join(f'<li style="margin:2px 0">{t(e)}</li>' for e in events[:8])
        ev = (f'<tr><td dir="rtl" style="padding:18px 0 0;color:#8189A0;font-size:13px;text-align:right;font-family:{FONT}">'
              f'<b style="color:#BCC2D0">نگهداری منابع</b><ul style="margin:6px 0;padding-right:18px">{lis}</ul></td></tr>')

    body = f"""<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Vazirmatn:wght@400;700&display=swap" rel="stylesheet"></head>
<body style="margin:0;background:#121829">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#121829"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:620px;background:#182036;border-radius:14px">
<tr><td dir="rtl" style="padding:26px 26px 6px;text-align:right;font-family:{FONT}">
  <div style="color:#8189A0;font-size:13px">{t(settings['site_title'])}</div>
  <div style="color:#ECE9E2;font-size:24px;font-weight:bold;margin:4px 0 10px">{t(d['long'])}</div>
  <div style="color:#BCC2D0;font-size:15px;line-height:2">{t(digest.get('note'))}</div>
  <div style="color:#8189A0;font-size:13px;margin-top:12px">{n} مطلب تازه از {digest['candidates']} مطلب بررسی‌شده{f' · {len(digest.get("board_new") or [])} ورود به لیدربورد' if digest.get("board_new") else ''}</div>
</td></tr>
<tr><td style="padding:0 26px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">{''.join(items)}{ev}</table></td></tr>
<tr><td align="center" style="padding:22px 26px 28px;font-family:{FONT}">
  <a href="{t(page)}" style="display:inline-block;border:1px solid #F2D27A;color:#F2D27A;font-weight:bold;padding:10px 24px;border-radius:10px;text-decoration:none">مشاهدهٔ دایجست و لیدربورد</a>
  <div style="margin-top:12px;font-size:13px"><a href="{t(page)}full/{digest['date']}.html" style="color:#8189A0">متن کامل همهٔ بریف‌ها در یک صفحه (مناسب NotebookLM)</a></div>
</td></tr></table></td></tr></table></body></html>"""

    msg = MIMEMultipart("alternative")
    nbn = len(digest.get("board_new") or [])
    msg["Subject"] = f"دایجست {d['short']} | {n} مطلب تازه" + (f" | {nbn} ورود به لیدربورد" if nbn else "")
    msg["From"] = f"Research Digest <{user}>"
    msg["To"] = ", ".join(to)
    plain = "\n".join(f"- {a['title_fa']}\n  {page}a/{a['id']}.html" for a in arts)
    msg.attach(MIMEText(f"دایجست به‌روز شد: {page}\n\n{plain}".translate(FA), "plain", "utf-8"))
    msg.attach(MIMEText(body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
        s.login(user, pwd)
        s.sendmail(user, to, msg.as_string())
    log.info("email sent to %d recipient(s)", len(to))
