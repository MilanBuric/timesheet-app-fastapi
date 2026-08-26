"""
Encrypts data at rest using a symmetric key from the environment. Currently
used for the Google OAuth tokens stored in users.google_token (see
google_meet.py) — without this, anyone with a copy of the database file (a
backup, a leak, an accidental commit) would have live, usable Google
account access for every user who'd connected their calendar. The token
IS the access; no password check stands between a copy of this column and
actually using someone's Google account, so it can't sit there in plain
text.

Uses Fernet (from the `cryptography` package) — an authenticated symmetric
cipher (AES-128-CBC + HMAC). "Authenticated" matters here specifically: a
tampered or corrupted ciphertext fails to decrypt LOUDLY (raises an
exception) rather than silently returning garbage that might look like a
valid token until something breaks downstream in a confusing way.
"""
import os

from cryptography.fernet import Fernet, InvalidToken

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv is optional; env vars can be set another way

_KEY = os.environ.get("TOKEN_ENCRYPTION_KEY")
if not _KEY:
    raise RuntimeError(
        "TOKEN_ENCRYPTION_KEY is not set. Add it to your .env file, e.g.:\n"
        "  TOKEN_ENCRYPTION_KEY=<a generated key>\n"
        "Generate one with:\n"
        '  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
    )
_fernet = Fernet(_KEY.encode() if isinstance(_KEY, str) else _KEY)


def encrypt_text(plaintext: str) -> str:
    """Encrypts a string for storage. Returns a string safe to put in a
    TEXT column — the output is already base64-encoded internally by
    Fernet, so no further encoding is needed before saving it."""
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_text(ciphertext: str) -> str:
    """Decrypts a string previously produced by encrypt_text. Raises
    ValueError (not the library's InvalidToken directly) on a tampered/
    corrupted value or a key mismatch, so callers only need to catch one
    clear, project-level exception type rather than reaching into
    `cryptography`'s exception hierarchy."""
    try:
        return _fernet.decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        raise ValueError("Could not decrypt value — wrong key, or the stored data is corrupted or tampered with.")