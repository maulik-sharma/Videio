"""Opaque identifiers used in public video URLs."""

import secrets
import string

PUBLIC_ID_LENGTH = 11
_ALPHABET = string.ascii_letters + string.digits


def new_public_id() -> str:
    """Return an 11-character base62 id generated from the OS CSPRNG."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(PUBLIC_ID_LENGTH))
