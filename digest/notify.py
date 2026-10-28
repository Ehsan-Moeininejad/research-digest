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


def send_email(digest: dict, settings: dict, board: dict | None = None):
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

    RED, INK, SOFT, MUTE, LINE, BG = "#EF4056", "#232933", "#4F545C", "#81858B", "#E4E4E7", "#F0F0F1"
    td = f'dir="rtl" style="text-align:right;font-family:{FONT};'

    def tint(hexcolor):
        h = hexcolor.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        return f"rgba({r},{g},{b},0.10)"

    def pill(c):
        return (f'<span style="display:inline-block;background:{tint(c["color"])};color:{c["color"]};font-size:12px;'
                f'font-weight:bold;padding:2px 10px;border-radius:12px">{t(c["fa"])}</span>')

    def section(title, sub=""):
        return (f'<tr><td {td}padding:28px 0 8px">'
                f'<div style="border-right:4px solid {RED};padding-right:10px">'
                f'<div style="font-size:18px;font-weight:bold;color:{INK};line-height:1.6">{t(title)}</div>'
                + (f'<div style="font-size:13px;color:{MUTE};line-height:1.6">{t(sub)}</div>' if sub else "")
                + "</div></td></tr>")

    def btns(brief, src):
        return (f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin-top:14px"><tr>'
                f'<td style="background:{RED};border-radius:8px"><a href="{t(brief)}" style="display:inline-block;padding:9px 18px;'
                f'font-family:{FONT};font-size:13.5px;font-weight:bold;color:#ffffff;text-decoration:none">خواندن بریف کامل</a></td>'
                f'<td style="width:8px"></td>'
                f'<td style="border:1px solid {LINE};border-radius:8px"><a href="{t(src)}" style="display:inline-block;padding:8px 16px;'
                f'font-family:{FONT};font-size:13px;color:{SOFT};text-decoration:none">منبع اصلی</a></td>'
                f'</tr></table>')

    order = list(cats)
    rows = [section("مطالب تازه بر اساس دسته", f"{n} مطلب برتر هفتهٔ اخیر که از بازبینی کیفی عبور کرد"
                    if n else "در این نوبت مطلب تازه‌ای از بازبینی کیفی عبور نکرد")]
    by_cat = sorted(arts, key=lambda a: (order.index(a["category"]) if a["category"] in order else 99, -a["score"]))
    last_cat = None
    for a in by_cat:
        c = cats[a["category"]]
        if a["category"] != last_cat:
            last_cat = a["category"]
            rows.append(f'<tr><td {td}padding:16px 2px 4px"><span style="display:inline-block;width:9px;height:9px;'
                        f'border-radius:3px;background:{c["color"]}"></span>&nbsp;<b style="font-size:14.5px;color:{c["color"]}">'
                        f'{t(c["fa"])}</b></td></tr>')
        about = short(a.get("about") or a.get("summary"), 320)
        use = (a.get("for_us") or a.get("actions") or [""])[0]
        rows.append(f"""
<tr><td style="padding:5px 0">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;border:1px solid {LINE};border-right:3px solid {c['color']};border-radius:12px">
<tr><td {td}padding:16px 20px">
  <div style="color:{MUTE};font-size:12.5px">{t(a['source'])}{' · منبع اصلی' if a.get('tier') == 1 else ''}{' · ورود به لیدربورد' if a.get('board') else ''}</div>
  <div style="margin:6px 0 6px;font-size:16.5px;line-height:1.75;font-weight:bold;color:{INK}">{t(a['title_fa'])}</div>
  {f'<div style="font-size:12.5px;color:{MUTE};margin-bottom:4px">بریف از منبع اصلی · پوشش از طریق {t(a["via"]["source"])}</div>' if a.get("via") else ''}
  <div style="font-size:14.5px;line-height:1.95;color:{INK}">{t(a.get('tldr'))}</div>
  {f'<div style="margin-top:8px;font-size:13.5px;line-height:1.95;color:{SOFT}">{t(about)}</div>' if about else ''}
  {f'<div style="margin-top:10px;background:#FFF2F4;border-right:3px solid {RED};border-radius:6px;padding:8px 12px;font-size:13.5px;line-height:1.9;color:{INK}"><b style="color:{RED}">کاربرد برای تیم</b> · {t(use)}</div>' if use else ''}
  {btns(f"{page}a/{a['id']}.html", a['url'])}
</td></tr></table>
</td></tr>""")

    # leaderboard summary: top 3 of every category, new entries marked
    bn = digest.get("board_new") or []
    new_ids = {e["id"] for e in bn}
    board = board or {}
    if any(board.get(k) for k in order):
        rows.append(section("خلاصهٔ لیدربورد", "3 مطلب برتر هر دسته" + (f" · {len(bn)} ورود تازه در این نوبت" if bn else "")))
        for k in order:
            top = (board.get(k) or [])[:3]
            if not top:
                continue
            c = cats[k]
            items = ""
            for r, e in enumerate(top, 1):
                tag = (f' <span style="background:{RED};color:#fff;font-size:11px;font-weight:bold;padding:1px 7px;'
                       f'border-radius:8px">تازه</span>') if e["id"] in new_ids else ""
                items += (f'<tr><td width="28" valign="top" style="padding:5px 0;font-family:{FONT};font-weight:bold;'
                          f'color:{c["color"]};font-size:14px;text-align:center">{r}</td>'
                          f'<td {td}padding:5px 6px;font-size:14px;line-height:1.8">'
                          f'<a href="{t(page + "a/" + e["id"] + ".html")}" style="color:{INK};text-decoration:none">{t(e.get("title_fa"))}</a>{tag}'
                          f'<div style="font-size:12px;color:{MUTE}">{t(e.get("source"))} · {e.get("score", "")}/10</div></td></tr>')
            rows.append(f"""
<tr><td style="padding:5px 0">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;border:1px solid {LINE};border-radius:12px">
<tr><td {td}padding:12px 16px 4px">{pill(c)}</td></tr>
<tr><td style="padding:0 12px 10px"><table role="presentation" dir="rtl" width="100%" cellpadding="0" cellspacing="0">{items}</table></td></tr>
</table></td></tr>""")
        rows.append(f'<tr><td {td}padding:8px 2px 0;font-size:13px"><a href="{t(page)}index.html#board" style="color:{RED}">مشاهدهٔ کامل لیدربورد</a></td></tr>')

    nbn = len(bn)
    body = f"""<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light only"><meta name="supported-color-schemes" content="light">
<link href="https://fonts.googleapis.com/css2?family=Vazirmatn:wght@400;700;800&display=swap" rel="stylesheet"></head>
<body style="margin:0;padding:0;background:{BG}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{BG}"><tr><td align="center" style="padding:20px 10px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px">

<tr><td style="background:#ffffff;border-radius:12px 12px 0 0;border-bottom:3px solid {RED}">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
    <td {td}padding:16px 22px;font-size:19px;font-weight:800;color:{RED}">{t(settings['site_title'])}</td>
    <td dir="ltr" style="padding:16px 22px;text-align:left;font-family:{FONT};font-size:12.5px;color:{MUTE}">Research &amp; Industry Digest</td>
  </tr></table>
</td></tr>

<tr><td {td}background:#ffffff;padding:20px 22px 22px;border-radius:0 0 12px 12px">
  <div style="font-size:24px;font-weight:800;color:{INK};line-height:1.5">{t(d['long'])}</div>
  {f'<div style="margin-top:10px;font-size:14.5px;line-height:2;color:{SOFT}">{t(digest.get("note"))}</div>' if digest.get("note") else ''}
  <table role="presentation" cellpadding="0" cellspacing="0" style="margin-top:14px"><tr>
    <td style="background:#FFF2F4;border-radius:8px;padding:8px 14px;font-family:{FONT};font-size:13px;color:{RED};font-weight:bold">{n} مطلب تازه</td>
    <td style="width:8px"></td>
    <td style="background:{BG};border-radius:8px;padding:8px 14px;font-family:{FONT};font-size:13px;color:{SOFT}">{digest['candidates']} مطلب بررسی‌شده</td>
    {f'<td style="width:8px"></td><td style="background:{BG};border-radius:8px;padding:8px 14px;font-family:{FONT};font-size:13px;color:{SOFT}">{nbn} ورود به لیدربورد</td>' if nbn else ''}
  </tr></table>
</td></tr>

<tr><td><table role="presentation" width="100%" cellpadding="0" cellspacing="0">{''.join(rows)}</table></td></tr>

<tr><td align="center" style="padding:26px 0 8px">
  <table role="presentation" cellpadding="0" cellspacing="0"><tr><td style="background:{RED};border-radius:10px">
    <a href="{t(page)}" style="display:inline-block;padding:12px 28px;font-family:{FONT};font-size:15px;font-weight:bold;color:#ffffff;text-decoration:none">مشاهدهٔ دایجست و لیدربورد</a>
  </td></tr></table>
  <div style="margin-top:12px;font-family:{FONT};font-size:12.5px"><a href="{t(page)}index.html#guide" style="color:{MUTE}">راهنما و نحوهٔ امتیازدهی</a> &nbsp;·&nbsp; <a href="{t(page)}full/{digest['date']}.html" style="color:{MUTE}">متن کامل همهٔ بریف‌ها در یک صفحه (مناسب NotebookLM)</a></div>
</td></tr>
<tr><td {td}padding:14px 0 6px;text-align:center;font-size:11.5px;color:{MUTE}">خلاصه‌ها با AI از منبع اصلی تهیه شده‌اند؛ برای تصمیم‌گیری، منبع اصلی مرجع است.</td></tr>

</table></td></tr></table></body></html>"""

    msg = MIMEMultipart("alternative")
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
