"""Virus-scan seam. No scanner exists on the server, so nothing is scanned."""
from __future__ import annotations

from typing import Literal

ScanStatus = Literal["not_scanned", "clean", "infected"]


def scan_document(plaintext: bytes) -> ScanStatus:
    """Return the scan status of a document. Today always ``not_scanned``."""
    return "not_scanned"
