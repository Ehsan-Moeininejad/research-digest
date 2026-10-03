"""Send the 'digest updated' email through Gmail SMTP (App Password)."""
import html
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from render import jalali

log = logging.getLogger("digest")


def send_email(digest: dict, settings: dict):
    user, pwd = os.environ["SMTP_USER"], os.environ["SMTP_PASS"]
    to = [x.strip() for x in os.environ["MAIL_TO"].split(",") if x.strip()]
    page = os.getenv("PAGE_URL", "").rstrip("/") + "/"
    d = jalali(digest["date"])
    cats = settings["categories"]
    n = len(digest["articles"])

    nb = sum(1 for a in digest["articles"] if a.get("board"))
    board_line = f"، {nb} مطلب وارد لیدربورد شد" if nb else ""
    events = digest.get("events") or []
    events_html = ""
    if events:
        lis = "".join(f'<li style="margin:2px 0">{html.escape(e)}</li>' for e in events[:8])
        events_html = (f'<tr><td style="padding:16px 28px 0;color:#8189A0;font-size:13px">'
                       f'<b style="color:#B7BDCC">نگهداری منابع:</b><ul style="margin:6px 0;padding-right:18px">{lis}</ul></td></tr>')
    rows = []
    for a in sorted(digest["articles"], key=lambda x: -x["score"])[:5]:
        c = cats[a["category"]]
        rows.append(
            f'<tr><td style="padding:12px 0;border-bottom:1px solid #2B3554">'
            f'<div style="font-size:12px;color:{c["color"]};font-weight:bold">{html.escape(c["fa"])}{" | لیدربورد" if a.get("board") else ""}'
            f' <span style="color:#8189A0;font-weight:normal">| {html.escape(a["source"])}</span></div>'
            f'<a href="{html.escape(a["url"])}" style="color:#ECE9E2;font-size:16px;font-weight:bold;text-decoration:none">'
            f'{html.escape(a["title_fa"])}</a>'
            f'<div style="color:#B7BDCC;font-size:14px;margin-top:4px">{html.escape(a["why_it_matters"])}</div>'
            f"</td></tr>"
        )

    body = f"""<div dir="rtl" style="background:#121829;padding:28px 0;font-family:Vazirmatn,Tahoma,Arial,sans-serif">
<table role="presentation" width="100%" style="max-width:600px;margin:0 auto;background:#182036;border-radius:14px">
<tr><td style="padding:28px 28px 8px">
<div style="color:#8189A0;font-size:13px">{html.escape(settings['site_title'])}</div>
<div style="color:#ECE9E2;font-size:26px;font-weight:bold;margin:6px 0">{d['long']}</div>
<div style="color:#B7BDCC;font-size:15px;line-height:1.9">{html.escape(digest.get('note') or '')}</div>
<div style="color:#8189A0;font-size:13px;margin-top:10px">{n} مطلب از بازبینی کیفی عبور کرد ({digest['candidates']} مطلب غربال و {digest.get('reviewed', 0)} متن کامل بازبینی شد){board_line}. مطالب با بالاترین امتیاز:</div>
</td></tr>
<tr><td style="padding:0 28px"><table role="presentation" width="100%">{''.join(rows)}</table></td></tr>
{events_html}
<tr><td style="padding:24px 28px 30px" align="center">
<a href="{page}" style="background:#F2D27A;color:#141a2b;font-weight:bold;padding:12px 26px;border-radius:10px;text-decoration:none;display:inline-block">مشاهدهٔ دایجست کامل</a>
</td></tr></table></div>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"دایجست {d['short']} | {n} مطلب تازه" + (f" | {nb} ورود به لیدربورد" if nb else "")
    msg["From"] = f"Research Digest <{user}>"
    msg["To"] = ", ".join(to)
    msg.attach(MIMEText(f"دایجست این نوبت به‌روز شد: {page}", "plain", "utf-8"))
    msg.attach(MIMEText(body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
        s.login(user, pwd)
        s.sendmail(user, to, msg.as_string())
    log.info("email sent to %d recipient(s)", len(to))
