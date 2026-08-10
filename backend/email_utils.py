"""
Sends meeting-related emails: invites (with one-click Accept/Decline links)
to attendees, and a confirmation email to the organizer with a direct join
link — useful because the organizer is the one whose Google account has
"host" powers in Meet (able to admit waiting guests), so they need fast
access to the link themselves, not just inside the app.

The layout deliberately mirrors Google Calendar's own invite email (When /
Guests on the left, a solid "Join with Google Meet" button + plain-text
link on the right, outlined pill-style reply buttons) so it reads as a
familiar, legitimate meeting invite rather than something that looks
unfamiliar or spam-like.

Configuration is read from environment variables (see .env.example):
    SMTP_HOST        default: smtp.gmail.com
    SMTP_PORT        default: 465
    SMTP_USERNAME    the Gmail address to send from
    SMTP_PASSWORD    a Gmail *App Password* (not your normal password)
    SMTP_FROM_NAME   display name shown to recipients, default: "Timesheet App"
    BASE_URL         the publicly reachable URL of this app, e.g. your ngrok
                      URL. Accept/Decline links are built from this, so it
                      must be reachable by whoever opens the email.

If SMTP_USERNAME / SMTP_PASSWORD aren't set, emails are printed to the
console instead of sent — handy for local development without real
credentials.
"""
import os
import smtplib
import ssl
from email.message import EmailMessage

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv is optional; env vars can be set another way

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
SMTP_FROM_NAME = os.environ.get("SMTP_FROM_NAME", "Timesheet App")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")


def _build_card_html(title: str, date: str, start_time: str, end_time: str,
                      description: str, location_type: str, room: str,
                      meeting_link: str, organizer_name: str, guests: list) -> tuple:
    """Returns (card_html, location_text, guest_lines) shared by both email types."""
    guests = guests or []

    if location_type == "in_person":
        where_label, where_value, location_text = "Where", room, f"Room: {room}"
    elif meeting_link:
        where_label, where_value, location_text = None, None, f"Join: {meeting_link}"
    else:
        where_label, where_value = "Where", "Online (link to be shared)"
        location_text = "Online (link to be shared)"

    guests_html = "".join(
        f'<div style="font-size:13px;color:#3c4043;padding:2px 0;">'
        f'{g}{" <span style=\'color:#5f6368;\'>- organizer</span>" if g == organizer_name else ""}</div>'
        for g in ([organizer_name] + guests)
    )

    left_column_html = f"""
      <div style="font-size:12px;color:#5f6368;letter-spacing:.3px;">
        {date} &nbsp;·&nbsp; {start_time} – {end_time}
      </div>
      <div style="font-size:19px;color:#202124;font-weight:500;margin-top:4px;">{title}</div>
      {f'<div style="font-size:13px;color:#3c4043;margin-top:10px;">{description}</div>' if description else ''}
      {f'''<div style="margin-top:14px;">
        <div style="font-size:12px;color:#5f6368;">{where_label}</div>
        <div style="font-size:14px;color:#202124;">{where_value}</div>
      </div>''' if where_label else ''}
      <div style="margin-top:18px;">
        <div style="font-size:12px;color:#5f6368;margin-bottom:6px;">Guests</div>
        {guests_html}
      </div>
    """

    right_column_html = f"""
      <a href="{meeting_link}" style="background:#1a73e8;color:#fff;padding:10px 22px;
         border-radius:4px;text-decoration:none;display:inline-block;font-size:14px;
         font-weight:500;white-space:nowrap;">Join with Google Meet</a>
      <div style="margin-top:14px;font-size:12px;color:#5f6368;">Meeting link</div>
      <div style="font-size:13px;word-break:break-all;">
        <a href="{meeting_link}" style="color:#202124;text-decoration:none;">{meeting_link}</a>
      </div>
    """ if (location_type == "online" and meeting_link) else ""

    card_html = f"""
      <div style="background:#f1f3f4;border-radius:10px;padding:20px 24px;">
        <table cellpadding="0" cellspacing="0" width="100%"><tr>
          <td valign="top" style="padding-right:20px;">{left_column_html}</td>
          {f'<td valign="top" width="200" style="border-left:1px solid #dadce0;padding-left:20px;">{right_column_html}</td>' if right_column_html else ''}
        </tr></table>
      </div>
    """
    guest_lines = "\n".join(
        f"- {g}" + (" (organizer)" if g == organizer_name else "") for g in ([organizer_name] + guests)
    )
    return card_html, location_text, guest_lines


def _pill_button(label: str, href: str, color: str) -> str:
    return f"""
      <a href="{href}" style="border:1px solid {color};color:{color};background:#fff;
         padding:8px 22px;border-radius:20px;text-decoration:none;display:inline-block;
         font-size:13px;font-weight:500;">{label}</a>
    """


def _send(to_email: str, subject: str, html: str, text: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{SMTP_FROM_NAME} <{SMTP_USERNAME}>" if SMTP_USERNAME else SMTP_FROM_NAME
    msg["To"] = to_email
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    if not SMTP_USERNAME or not SMTP_PASSWORD:
        print(f"⚠️  SMTP not configured — printing email instead of sending to {to_email}:\n{text}")
        return

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context) as server:
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)
        print(f"✅ Email sent to {to_email}")
    except Exception as exc:
        # Never let an email failure break meeting creation
        print(f"❌ Failed to send email to {to_email}: {exc}")


def send_meeting_invite(to_email: str, attendee_name: str, organizer_name: str,
                         title: str, date: str, start_time: str, end_time: str,
                         description: str, rsvp_token: str,
                         location_type: str = "online", room: str = None,
                         meeting_link: str = None, guests: list = None) -> None:
    accept_url = f"{BASE_URL}/meetings/rsvp?token={rsvp_token}&action=accept"
    decline_url = f"{BASE_URL}/meetings/rsvp?token={rsvp_token}&action=decline"

    card_html, location_text, guest_lines = _build_card_html(
        title, date, start_time, end_time, description, location_type, room,
        meeting_link, organizer_name, guests
    )

    html = f"""
    <!DOCTYPE html>
    <html><head><meta charset="utf-8"></head><body>
    <div style="font-family: Arial, sans-serif; max-width: 560px; margin: 0 auto;">
      {card_html}
      <p style="font-size:13px;color:#5f6368;margin:16px 0 8px;">
        Reply for {to_email}
      </p>
      <table cellpadding="0" cellspacing="0"><tr>
        <td style="padding-right: 8px;">{_pill_button("Yes", accept_url, "#1a73e8")}</td>
        <td>{_pill_button("No", decline_url, "#5f6368")}</td>
      </tr></table>
      <p style="color:#999;font-size:12px;margin-top:20px;">
        You can also respond from inside the Timesheet App.
      </p>
    </div>
    </body></html>
    """
    text = (
        f"{title}\n"
        f"When: {date}, {start_time} - {end_time}\n"
        f"{location_text}\n\n"
        f"{description or ''}\n\n"
        f"Guests:\n{guest_lines}\n\n"
        f"Reply for {to_email}\n"
        f"Yes: {accept_url}\nNo: {decline_url}\n"
    )
    _send(to_email, f"Invitation: {title} @ {date} {start_time} – {end_time}", html, text)


def send_organizer_confirmation(to_email: str, organizer_name: str,
                                 title: str, date: str, start_time: str, end_time: str,
                                 description: str, location_type: str = "online",
                                 room: str = None, meeting_link: str = None,
                                 guests: list = None) -> None:
    """
    Sent to the organizer themselves after scheduling — no Accept/Decline
    needed since it's their own meeting. Gives them a fast, direct join
    link by email, which matters because their Google account is the one
    with "host" powers to admit waiting guests in Meet.
    """
    card_html, location_text, guest_lines = _build_card_html(
        title, date, start_time, end_time, description, location_type, room,
        meeting_link, organizer_name, guests
    )

    html = f"""
    <!DOCTYPE html>
    <html><head><meta charset="utf-8"></head><body>
    <div style="font-family: Arial, sans-serif; max-width: 560px; margin: 0 auto;">
      {card_html}
      <p style="font-size:13px;color:#5f6368;margin:16px 0 0;">
        You're the organizer — this is your copy for quick access. If Meet puts guests in a
        waiting room, join from here so you can admit them.
      </p>
    </div>
    </body></html>
    """
    text = (
        f"{title} (you're the organizer)\n"
        f"When: {date}, {start_time} - {end_time}\n"
        f"{location_text}\n\n"
        f"{description or ''}\n\n"
        f"Guests:\n{guest_lines}\n"
    )
    _send(to_email, f"Your meeting: {title} @ {date} {start_time} – {end_time}", html, text)