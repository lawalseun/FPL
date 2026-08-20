"""Weekly alerts: Telegram, email, and a calendar file of every deadline.

All credentials come from environment variables, which on GitHub Actions means
repository secrets. Nothing is committed.

  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID     - from @BotFather and @userinfobot
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, MAIL_TO
  DASHBOARD_URL                        - your GitHub Pages link
"""
from __future__ import annotations
import os, smtplib, ssl, datetime as dt
from email.message import EmailMessage
import requests

URL = os.environ.get("DASHBOARD_URL", "")


def _fmt(p: dict) -> tuple[str, str]:
    gw = p["gw"]
    dl = p.get("deadline")
    when = ""
    if dl:
        d = dt.datetime.fromisoformat(dl)
        when = d.strftime("%a %d %b %H:%M UTC")
    src = p.get("mine") or p["optimal"]
    lines = [f"FPL Gameweek {gw} — deadline {when}", ""]
    lines.append(f"Captain: {src['captain']}   (vice: {src['vice']})")
    lines.append(f"Projected: {src.get('xp', '?')} pts")
    lines.append("")
    lines.append("Starting XI")
    for r in src["xi"]:
        star = " (C)" if r["name"] == src["captain"] else ""
        lines.append(f"  {r['name']:<16} {r['team']}  {r['xp']:>5} xP{star}")
    lines.append("")
    lines.append("Bench (in order)")
    for r in src["bench"]:
        lines.append(f"  {r['name']:<16} {r['team']}  {r['xp']:>5} xP")

    if p.get("mine", {}).get("transfers"):
        lines += ["", "Transfer options"]
        for t in p["mine"]["transfers"][:4]:
            if not t["in"]:
                lines.append(f"  Roll your transfer  (net {t['net']:+})")
            else:
                lines.append(f"  {', '.join(t['out'])} -> {', '.join(t['in'])}"
                             f"  net {t['net']:+} pts"
                             + (f", -{t['hits']*4} hit" if t["hits"] else ""))
    for c in p.get("mine", {}).get("chips", []):
        if c.get("now"):
            lines += ["", f"CHIP: play {c['chip']} this week — {c['why']}"]
    if p.get("flagged"):
        lines += ["", "Injury / availability watch"]
        for f in p["flagged"][:8]:
            lines.append(f"  {f['name']} ({f['team']}) — {f['news']}")
    if URL:
        lines += ["", URL]
    return f"FPL GW{gw}: captain {src['captain']} — deadline {when}", "\n".join(lines)


def send_telegram(subject: str, body: str):
    tok, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (tok and chat):
        return "skipped (no telegram secrets)"
    r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      json={"chat_id": chat, "text": f"*{subject}*\n\n```\n{body}\n```",
                            "parse_mode": "Markdown"}, timeout=20)
    return f"telegram {r.status_code}"


def send_email(subject: str, body: str):
    host, user = os.environ.get("SMTP_HOST"), os.environ.get("SMTP_USER")
    pwd, to = os.environ.get("SMTP_PASS"), os.environ.get("MAIL_TO")
    if not all([host, user, pwd, to]):
        return "skipped (no smtp secrets)"
    m = EmailMessage()
    m["Subject"], m["From"], m["To"] = subject, user, to
    m.set_content(body)
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", 587))) as s:
        s.starttls(context=ssl.create_default_context())
        s.login(user, pwd)
        s.send_message(m)
    return "email sent"


def write_ics(events: list[dict], path: str = "docs/fpl-deadlines.ics"):
    """One all-season calendar file. Subscribe to it once in Google Calendar and
    every deadline shows up with a reminder, forever."""
    out = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//fpl-dashboard//EN",
           "X-WR-CALNAME:FPL Deadlines"]
    for e in events:
        if not e.get("deadline_time"):
            continue
        d = dt.datetime.fromisoformat(e["deadline_time"].replace("Z", "+00:00"))
        stamp = d.strftime("%Y%m%dT%H%M%SZ")
        end = (d + dt.timedelta(minutes=30)).strftime("%Y%m%dT%H%M%SZ")
        out += ["BEGIN:VEVENT", f"UID:fpl-gw{e['id']}@dashboard",
                f"DTSTAMP:{stamp}", f"DTSTART:{stamp}", f"DTEND:{end}",
                f"SUMMARY:FPL {e['name']} deadline",
                f"DESCRIPTION:Set your team. {URL}",
                "BEGIN:VALARM", "TRIGGER:-PT24H", "ACTION:DISPLAY",
                "DESCRIPTION:FPL deadline tomorrow — check the dashboard", "END:VALARM",
                "END:VEVENT"]
    out.append("END:VCALENDAR")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("\r\n".join(out))
    return path


def send(payload: dict):
    subject, body = _fmt(payload)
    print(send_telegram(subject, body))
    print(send_email(subject, body))
