"""
Sends meeting-related emails: invites (with one-click Accept/Decline links)
to attendees, and a confirmation email to the organizer with a direct join
link — useful because the organizer is the one whose Google account has
"host" powers in Meet (able to admit waiting guests), so they need fast
access to the link themselves, not just inside the app.

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
        location_label = "📍 Where"
        location_value_html = room
        location_text = f"Room: {room}"
    elif meeting_link:
        location_label = None
        location_value_html = None
        location_text = f"Join: {meeting_link}"
    else:
        location_label = "💻 Where"
        location_value_html = "Online (link to be shared)"
        location_text = "Online (link to be shared)"

    guests_html = "".join(
        f'<div style="font-size:13px;color:#3c4043;padding:2px 0;">'
        f'{"👤 " if g == organizer_name else ""}{g}{" — organizer" if g == organizer_name else ""}</div>'
        for g in ([organizer_name] + guests)
    )

    join_button_html = f"""
      <a href="{meeting_link}" style="background:#1a73e8;color:#fff;padding:10px 22px;
         border-radius:6px;text-decoration:none;display:inline-block;font-size:14px;
         font-weight:500;">Join meeting</a>
      <div style="margin-top:8px;font-size:12px;color:#5f6368;word-break:break-all;">{meeting_link}</div>
    """ if (location_type == "online" and meeting_link) else ""

    where_row_html = f"""
        <tr><td style="padding-top:14px;">
          <div style="font-size:12px;color:#5f6368;">{location_label}</div>
          <div style="font-size:14px;color:#202124;">{location_value_html}</div>
        </td></tr>
    """ if location_label else ""

    card_html = f"""
      <div style="background:#f1f3f4;border-radius:10px;padding:20px 24px;">
        <div style="font-size:12px;color:#5f6368;letter-spacing:.3px;">
          {date} &nbsp;·&nbsp; {start_time} – {end_time}
        </div>
        <div style="font-size:19px;color:#202124;font-weight:500;margin-top:4px;">{title}</div>
        {f'<div style="font-size:13px;color:#3c4043;margin-top:10px;">{description}</div>' if description else ''}

        <table cellpadding="0" cellspacing="0" style="margin-top:4px;">{where_row_html}</table>
        {f'<div style="margin-top:14px;">{join_button_html}</div>' if join_button_html else ''}

        <div style="margin-top:18px;padding-top:14px;border-top:1px solid #dadce0;">
          <div style="font-size:12px;color:#5f6368;margin-bottom:6px;">Guests</div>
          {guests_html}
        </div>
      </div>
    """
    guest_lines = "\n".join(
        f"- {g}" + (" (organizer)" if g == organizer_name else "") for g in ([organizer_name] + guests)
    )
    return card_html, location_text, guest_lines


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
    <div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto;">
      {card_html}
      <p style="font-size:14px;color:#3c4043;margin:18px 0 10px;">
        Hi {attendee_name}, you've been invited to this meeting. Please respond:
      </p>
      <table cellpadding="0" cellspacing="0"><tr>
        <td style="padding-right: 10px;">
          <a href="{accept_url}" style="background:#16a34a;color:#fff;padding:10px 20px;
             border-radius:20px;text-decoration:none;display:inline-block;font-size:14px;">Accept</a>
        </td>
        <td>
          <a href="{decline_url}" style="background:#dc2626;color:#fff;padding:10px 20px;
             border-radius:20px;text-decoration:none;display:inline-block;font-size:14px;">Decline</a>
        </td>
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
        f"Accept: {accept_url}\nDecline: {decline_url}\n"
    )
    _send(to_email, f"Meeting invite: {title}", html, text)


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
    <div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto;">
      {card_html}
      <p style="font-size:14px;color:#3c4043;margin:18px 0 10px;">
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
    _send(to_email, f"Your meeting: {title}", html, text)
