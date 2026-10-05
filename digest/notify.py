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

    rows = [section("مطالب تازه", f"{n} مطلب برتر هفتهٔ اخیر که از بازبینی کیفی عبور کرد"
                    if n else "در این نوبت مطلب تازه‌ای از بازبینی کیفی عبور نکرد")]
    for i, a in enumerate(arts, 1):
        c = cats[a["category"]]
        about = short(a.get("about") or a.get("summary"), 380)
        concl = short(a.get("conclusion"), 300)
        use = (a.get("for_us") or a.get("actions") or [""])[0]
        rows.append(f"""
<tr><td style="padding:6px 0">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;border:1px solid {LINE};border-radius:12px">
<tr><td {td}padding:18px 20px">
  <div>{pill(c)} <span style="color:{MUTE};font-size:12.5px">&nbsp;{t(a['source'])}</span></div>
  <div style="margin:10px 0 6px;font-size:17px;line-height:1.75;font-weight:bold;color:{INK}">{i}. {t(a['title_fa'])}</div>
  {f'<div style="font-size:12.5px;color:{MUTE};margin-bottom:4px">بریف از منبع اصلی · پوشش از طریق {t(a["via"]["source"])}</div>' if a.get("via") else ''}
  <div style="font-size:14.5px;line-height:1.95;color:{INK}">{t(a.get('tldr'))}</div>
  {f'<div style="margin-top:10px;font-size:13.5px;line-height:1.95;color:{SOFT}"><b style="color:{INK}">درباره</b> · {t(about)}</div>' if about else ''}
  {f'<div style="margin-top:6px;font-size:13.5px;line-height:1.95;color:{SOFT}"><b style="color:{INK}">نتیجه‌گیری</b> · {t(concl)}</div>' if concl else ''}
  {f'<div style="margin-top:10px;background:#FFF2F4;border-right:3px solid {RED};border-radius:6px;padding:8px 12px;font-size:13.5px;line-height:1.9;color:{INK}"><b style="color:{RED}">کاربرد برای تیم</b> · {t(use)}</div>' if use else ''}
  {btns(f"{page}a/{a['id']}.html", a['url'])}
</td></tr></table>
</td></tr>""")

    bn = digest.get("board_new") or []
    if bn:
        rows.append(section("ورودی‌های جدید لیدربورد", f"{len(bn)} مطلب وارد برترین‌های دسته‌ها شد"))
        order = list(cats)
        for e in sorted(bn, key=lambda x: (order.index(x["category"]) if x["category"] in order else 99, x.get("rank", 99))):
            c = cats.get(e["category"], {"fa": e["category"], "color": MUTE})
            rows.append(f"""
<tr><td style="padding:4px 0">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;border:1px solid {LINE};border-radius:12px">
<tr><td width="46" valign="top" style="padding:16px 0 0 0;text-align:center">
  <div style="width:30px;height:30px;line-height:30px;border-radius:15px;background:{RED};color:#fff;font-family:{FONT};font-weight:bold;font-size:14px;margin:0 auto">{e.get('rank', '')}</div></td>
<td {td}padding:14px 16px 14px 0">
  <div>{pill(c)} <span style="color:{MUTE};font-size:12.5px">&nbsp;{t(e['source'])}</span></div>
  <div style="margin:6px 0 2px;font-size:15.5px;line-height:1.75;font-weight:bold">
    <a href="{t(page + 'a/' + e['id'] + '.html')}" style="color:{INK};text-decoration:none">{t(e['title_fa'])}</a></div>
  <div style="font-size:13.5px;line-height:1.9;color:{SOFT}">{t(e.get('tldr'))}</div>
</td></tr></table>
</td></tr>""")

    rep = digest.get("source_report")
    if rep:
        st_fa = {"active": "فعال", "probation": "آزمایشی", "paused": "متوقف", "disabled": "غیرفعال", "rejected": "ردشده", "new": "جدید"}
        tier_fa = {1: "اصلی", 2: "تحلیلی", 3: "خبری"}
        rows.append(section("گزارش هفتگی منابع", "برای مرور تیم: رده، وضعیت، کیفیت، نرخ قبولی و پیشنهاد تغییر رده"))
        if rep.get("changes"):
            lis = "".join(f'<li style="margin:2px 0">{t(c)}</li>' for c in rep["changes"][:15])
            rows.append(f'<tr><td {td}padding:4px 0 10px;font-size:13px;line-height:1.9;color:{SOFT}">'
                        f'<b style="color:{INK}">تغییرات این هفته</b><ul style="margin:4px 0;padding-right:18px">{lis}</ul></td></tr>')
        head = "".join(f'<th style="padding:6px 8px;font-size:12px;color:{MUTE};text-align:right;border-bottom:1px solid {LINE}">{h}</th>'
                       for h in ("منبع", "رده", "وضعیت", "کیفیت", "قبولی", "انتخاب", "پیشنهاد"))
        body_rows = ""
        for r in rep["rows"]:
            if r.get("kind") == "paper":
                continue
            cells = (t(r["name"]), tier_fa.get(r.get("tier", 2), ""), st_fa.get(r["status"], r["status"]),
                     r["avg"] if r["avg"] is not None else "", f'{r["acc_rate"]}%' if r.get("acc_rate") is not None else "", r["picks"], r.get("suggest", ""))
            body_rows += "<tr>" + "".join(f'<td style="padding:5px 8px;font-size:12.5px;color:{INK};border-bottom:1px solid {LINE};font-family:{FONT}">{c}</td>' for c in cells) + "</tr>"
        rows.append(f'<tr><td style="background:#fff;border:1px solid {LINE};border-radius:12px;padding:8px">'
                    f'<table role="presentation" dir="rtl" width="100%" cellpadding="0" cellspacing="0">{head}{body_rows}</table></td></tr>')

    events = digest.get("events") or []
    if events:
        lis = "".join(f'<li style="margin:2px 0">{t(e)}</li>' for e in events[:8])
        rows.append(f'<tr><td {td}padding:22px 0 0;color:{MUTE};font-size:12.5px;line-height:1.9">'
                    f'<b style="color:{SOFT}">نگهداری منابع</b><ul style="margin:4px 0;padding-right:18px">{lis}</ul></td></tr>')

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
  <div style="margin-top:12px;font-family:{FONT};font-size:12.5px"><a href="{t(page)}full/{digest['date']}.html" style="color:{MUTE}">متن کامل همهٔ بریف‌ها در یک صفحه (مناسب NotebookLM)</a></div>
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
