"""
TOTP (Time-Based One-Time Password) — core operations.

RFC 6238 compliant implementation via pyotp 2.9.0.
Google Authenticator, Authy, and all RFC 6238 compatible apps supported.

Official references (verified March 2026):
  RFC 6238  — TOTP: Time-Based One-Time Password Algorithm
    https://tools.ietf.org/html/rfc6238
  pyotp 2.9.0 official PyPI checklist:
    https://pypi.org/project/pyotp/
  OWASP Multifactor Authentication Cheat Sheet:
    https://cheatsheetseries.owasp.org/cheatsheets/Multifactor_Authentication_Cheat_Sheet.html
  Authgear — 5 Common TOTP Mistakes (2026):
    https://www.authgear.com/post/5-common-totp-mistakes

Security rules applied from these sources:

  1. Secrets encrypted at rest (Fernet = AES-128-CBC + HMAC-SHA256).
     Key loaded from TOTP_ENCRYPTION_KEY env var — never hardcoded.

  2. Replay-attack prevention (pyotp checklist, RFC 6238 §5.2):
     The last_used_at timestamp of the most recent successful verification
     is stored in user_mfa.  If a code is presented in the same 30-second
     time slot, it is rejected even if pyotp.verify() returns True.

  3. Clock-skew tolerance (Authgear 2026, RFC 6238 §5.2):
     valid_window=1 accepts codes ±30 seconds from the current slot —
     enough for real-world clock drift without widening the attack window.

  4. Constant-time comparison:
     pyotp.TOTP.verify() internally calls utils.strings_equal(), which is
     an HMAC-based constant-time comparison — safe against timing attacks.

  5. Secrets are never logged.
     The raw secret is only ever held in memory during the current call.

  6. Google Authenticator / Authy compatible parameters:
     digits=6, interval=30s, algorithm=SHA-1 (RFC 6238 default).
     SHA-256 and SHA-512 are in the RFC but not supported by most apps.

TOTP_ENCRYPTION_KEY setup:
  Generate once and add to .env:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  The key is a URL-safe base64-encoded 32-byte value.
"""

from __future__ import annotations

import base64
import io
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import pyotp
import qrcode
import qrcode.constants
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TOTP parameters (RFC 6238 defaults — Google Authenticator compatible)
# ---------------------------------------------------------------------------

# Number of digits in each OTP code.
# 6 is the RFC 6238 standard and is required by Google Authenticator.
TOTP_DIGITS: int = 6

# Time step in seconds.  30 is the RFC 6238 default.
TOTP_INTERVAL: int = 30

# ±N time steps accepted during verification.
# 1 means we accept codes from the previous, current, and next 30-second window.
# This covers up to ±30 s of clock skew — standard practice per RFC 6238 §5.2.
TOTP_VALID_WINDOW: int = 1

# Issuer name shown in the authenticator app (e.g. "Talky.ai (user@example.com)").
TOTP_ISSUER_NAME: str = os.getenv("TOTP_ISSUER_NAME", "Talky.ai")


# ---------------------------------------------------------------------------
# Internal: Fernet cipher loader
# ---------------------------------------------------------------------------


def _get_fernet() -> Fernet:
    """
    Build and return a Fernet cipher using the TOTP_ENCRYPTION_KEY env var.

    Raises RuntimeError with a clear diagnostic message if the key is missing
    or malformed — so misconfiguration is caught at startup / first MFA call,
    not silently swallowed.
    """
    raw_key = os.getenv("TOTP_ENCRYPTION_KEY", "").strip()
    if not raw_key:
        raise RuntimeError(
            "TOTP_ENCRYPTION_KEY is not set. Generate a key with: python -c "
            "'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' "
            "and add it to your .env file."
        )
    try:
        key_bytes = raw_key.encode("utf-8")
        return Fernet(key_bytes)
    except Exception as exc:
        raise RuntimeError(
            f"TOTP_ENCRYPTION_KEY is invalid — it must be a URL-safe base64-encoded 32-byte Fernet key. Original error: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Secret generation
# ---------------------------------------------------------------------------


def generate_totp_secret() -> str:
    """
    Generate a cryptographically random TOTP base32 secret.

    pyotp.random_base32() uses os.urandom() under the hood, which is backed
    by the OS CSPRNG.  The result is a 32-character base32 string (160-bit
    entropy) compatible with Google Authenticator and all RFC 6238 apps.

    Returns
    -------
    str
        Raw base32 secret string.  This is the PLAINTEXT value — it must be
        encrypted with encrypt_totp_secret() before being written to the DB.
        It must never be logged.
    """
    return pyotp.random_base32()


# ---------------------------------------------------------------------------
# Fernet encryption / decryption
# ---------------------------------------------------------------------------


def encrypt_totp_secret(raw_secret: str) -> str:
    """
    Encrypt a raw TOTP base32 secret for database storage.

    Uses Fernet (AES-128-CBC + HMAC-SHA256) with the application encryption
    key from TOTP_ENCRYPTION_KEY.  The ciphertext is URL-safe and includes
    an HMAC, so any tampering is detected on decryption.

    Parameters
    ----------
    raw_secret:
        The plaintext base32 secret returned by generate_totp_secret().

    Returns
    -------
    str
        Fernet ciphertext as a UTF-8 string.  Safe to store in a TEXT column.

    Raises
    ------
    RuntimeError
        If TOTP_ENCRYPTION_KEY is missing or malformed.
    """
    fernet = _get_fernet()
    ciphertext = fernet.encrypt(raw_secret.encode("utf-8"))
    return ciphertext.decode("utf-8")


def decrypt_totp_secret(encrypted_secret: str) -> str:
    """
    Decrypt a Fernet-encrypted TOTP secret loaded from the database.

    Parameters
    ----------
    encrypted_secret:
        The ciphertext string previously returned by encrypt_totp_secret().

    Returns
    -------
    str
        The raw base32 TOTP secret.

    Raises
    ------
    RuntimeError
        If decryption fails (wrong key, tampered ciphertext, or truncated data).
        The caller should treat this as an internal server error — do not
        expose the reason to the client.
    """
    fernet = _get_fernet()
    try:
        plaintext = fernet.decrypt(encrypted_secret.encode("utf-8"))
        return plaintext.decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError(
            "TOTP secret decryption failed — the ciphertext is invalid, the key may have been rotated, or the data is corrupted."
        ) from exc


# ---------------------------------------------------------------------------
# Provisioning URI + QR code
# ---------------------------------------------------------------------------


def get_provisioning_uri(
    raw_secret: str,
    email: str,
    *,
    issuer: str = TOTP_ISSUER_NAME,
) -> str:
    """
    Build the otpauth:// provisioning URI for QR code generation.

    This URI encodes the TOTP parameters in the format expected by Google
    Authenticator, Authy, and all RFC 6238 compatible apps.  The user scans
    it once to register the account in their authenticator app.

    Example output:
        otpauth://totp/Talky.ai:user%40example.com
          ?secret=JBSWY3DPEHPK3PXP&issuer=Talky.ai

    Parameters
    ----------
    raw_secret:
        The PLAINTEXT base32 secret (NOT encrypted).
    email:
        The user's email address — shown as the account label in the app.
    issuer:
        The service name shown in the authenticator app.  Defaults to the
        value of the TOTP_ISSUER_NAME env var (fallback: "Talky.ai").

    Returns
    -------
    str
        A valid otpauth:// URI string.
    """
    totp = pyotp.TOTP(
        raw_secret,
        digits=TOTP_DIGITS,
        interval=TOTP_INTERVAL,
        issuer=issuer,
        name=email,
    )
    return totp.provisioning_uri(name=email, issuer_name=issuer)


def generate_qr_code_data_uri(provisioning_uri: str) -> str:
    """
    Render the provisioning URI as a base64-encoded PNG data URI.

    The returned string can be used directly in an HTML <img> tag:
        <img src="data:image/png;base64,..." />

    No external HTTP call is made — the QR code is generated locally using
    the qrcode library.

    Parameters
    ----------
    provisioning_uri:
        The otpauth:// URI from get_provisioning_uri().

    Returns
    -------
    str
        A data URI of the form "data:image/png;base64,<base64_png>".
    """
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(provisioning_uri)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, "PNG")
    buffer.seek(0)

    encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{encoded}"


# ---------------------------------------------------------------------------
# TOTP verification (with replay-attack prevention)
# ---------------------------------------------------------------------------


def is_replay_attack(
    last_used_at: Optional[datetime],
    *,
    interval: int = TOTP_INTERVAL,
) -> bool:
    """
    Detect whether a TOTP code is being replayed in the same time slot.

    pyotp.verify() checks that the code is mathematically valid for the
    current window, but it does NOT prevent the same code from being used
    twice within that 30-second window.  This function closes that gap.

    pyotp official checklist (https://pypi.org/project/pyotp/):
      "Deny replay attacks by rejecting one-time passwords that have been
       used by the client (this requires storing the most recently
       authenticated timestamp, OTP, or hash of the OTP in your database,
       and rejecting the OTP when a match is seen)."

    Parameters
    ----------
    last_used_at:
        The UTC datetime of the most recent successful TOTP verification
        stored in user_mfa.last_used_at.  None means no previous use.
    interval:
        The TOTP time step in seconds (default 30).

    Returns
    -------
    bool
        True  → this is a replay within the same time slot — REJECT.
        False → different time slot or first use — OK to proceed.
    """
    if last_used_at is None:
        return False

    now_ts = datetime.now(timezone.utc).timestamp()
    last_ts = (
        last_used_at.timestamp()
        if last_used_at.tzinfo is not None
        else last_used_at.replace(tzinfo=timezone.utc).timestamp()
    )

    current_slot = int(now_ts) // interval
    last_slot = int(last_ts) // interval

    return current_slot == last_slot


def verify_totp_code(
    raw_secret: str,
    code: str,
    *,
    last_used_at: Optional[datetime] = None,
    valid_window: int = TOTP_VALID_WINDOW,
) -> bool:
    """
    Verify a user-submitted TOTP code.

    Applies both pyotp verification AND replay-attack prevention.

    Steps
    -----
    1. Normalise the code (strip spaces / hyphens / whitespace).
    2. Reject if the code is clearly not a 6-digit numeric string.
    3. Reject if the current time slot matches last_used_at (replay guard).
    4. Verify with pyotp.TOTP.verify(valid_window=1) — constant-time.

    Parameters
    ----------
    raw_secret:
        The PLAINTEXT base32 TOTP secret (must be decrypted before passing).
    code:
        The 6-digit OTP submitted by the user.
    last_used_at:
        The UTC datetime of the last successful TOTP use, loaded from
        user_mfa.last_used_at.  Used for replay prevention.
    valid_window:
        Number of 30-second windows to accept on either side of the current
        window.  Default=1 (±30 s clock skew tolerance, RFC 6238 §5.2).

    Returns
    -------
    bool
        True if the code is valid AND is not a replay.  False otherwise.
        Never raises — all exceptions are caught and False is returned.
    """
    if not raw_secret or not code:
        return False

    # Normalise: strip spaces and hyphens that some apps display
    normalised = code.replace(" ", "").replace("-", "").strip()

    # Reject obviously malformed input without touching crypto
    if not normalised.isdigit() or len(normalised) != TOTP_DIGITS:
        return False

    # Replay-attack check (same 30-second slot as last verified use)
    if is_replay_attack(last_used_at):
        logger.warning(
            "TOTP replay attack detected — code submitted in same time slot as last_used_at=%s",
            last_used_at,
        )
        return False

    try:
        totp = pyotp.TOTP(
            raw_secret,
            digits=TOTP_DIGITS,
            interval=TOTP_INTERVAL,
        )
        # valid_window=1 → accept [-1, 0, +1] time steps (±30 s)
        # pyotp.TOTP.verify uses utils.strings_equal() — constant-time
        return totp.verify(normalised, valid_window=valid_window)
    except Exception as exc:
        # Log without exposing the secret
        logger.warning(
            "TOTP verification error (non-secret details): %s", type(exc).__name__
        )
        return False
