"""Opaque 26-character identifiers used by the control-plane schema."""

from __future__ import annotations

import base64
from uuid import uuid4


def generate_id() -> str:
    """Generate a compact opaque identifier suitable for a CHAR(26) key."""

    return base64.b32encode(uuid4().bytes).decode("ascii").rstrip("=").lower()[:26]


__all__ = ["generate_id"]
