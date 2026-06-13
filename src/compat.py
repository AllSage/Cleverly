"""Small compatibility helpers shared by Cleverly modules."""

from __future__ import annotations

import os
from typing import Iterable


def getenv(name: str, default: str | None = None) -> str | None:
    return os.getenv(name, default)


def request_header(headers, name: str, default: str = "") -> str:
    return headers.get(name, default)


def first_column(columns: Iterable[str], *names: str) -> str | None:
    present = set(columns)
    for name in names:
        if name in present:
            return name
    return None
