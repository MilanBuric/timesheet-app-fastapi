"""
Sends meeting-invite emails with one-click Accept / Decline links.

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


def send_meeting_invite(to_email: str, attendee_name: str, organizer_name: str,
                         title: str, date: str, start_time: str, end_time: str,
                         description: str, rsvp_token: str,
                         location_type: str = "online", room: str = None,
                         meeting_link: str = None) -> None:
    accept_url = f"{BASE_URL}/meetings/rsvp?token={rsvp_token}&action=accept"
    decline_url = f"{BASE_URL}/meetings/rsvp?token={rsvp_token}&action=decline"

    if location_type == "in_person":
        location_html = f"""
        <tr><td style="padding-top:14px;">
          <span style="color:#5f6368;font-size:13px;">📍 Room</span><br>
          <span style="font-size:14px;color:#202124;">{room}</span>
        </td></tr>"""
        location_text = f"Room: {room}"
    elif meeting_link:
        location_html = f"""
        <tr><td style="padding-top:16px;">
          <a href="{meeting_link}" style="background:#1a73e8;color:#fff;padding:10px 22px;
             border-radius:6px;text-decoration:none;display:inline-block;font-size:14px;
             font-weight:500;">Join meeting</a>
          <div style="margin-top:8px;font-size:12px;color:#5f6368;word-break:break-all;">{meeting_link}</div>
        </td></tr>"""
        location_text = f"Join: {meeting_link}"
    else:
        location_html = ""
        location_text = "Online (link to be shared)"

    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto;">
      <div style="background:#f1f3f4;border-radius:10px;padding:20px 24px;">
        <div style="font-size:12px;color:#5f6368;letter-spacing:.3px;">
          {date} &nbsp;·&nbsp; {start_time} – {end_time}
        </div>
        <div style="font-size:19px;color:#202124;font-weight:500;margin-top:4px;">{title}</div>
        <div style="font-size:13px;color:#5f6368;margin-top:2px;">Organized by {organizer_name}</div>
        {f'<div style="font-size:13px;color:#3c4043;margin-top:10px;">{description}</div>' if description else ''}
        <table cellpadding="0" cellspacing="0" style="margin-top:4px;">{location_html}</table>
      </div>

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
    """
    text = (
        f"{title}\nOrganized by {organizer_name}\n"
        f"When: {date}, {start_time} - {end_time}\n"
        f"{location_text}\n\n"
        f"{description or ''}\n\n"
        f"Accept: {accept_url}\nDecline: {decline_url}\n"
    )

    msg = EmailMessage()
    msg["Subject"] = f"Meeting invite: {title}"
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
        print(f"✅ Meeting invite emailed to {to_email}")
    except Exception as exc:
        # Never let an email failure break meeting creation
        print(f"❌ Failed to send meeting invite to {to_email}: {exc}")
