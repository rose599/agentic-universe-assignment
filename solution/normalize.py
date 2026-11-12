"""Pure value-normalisation functions (no I/O). These implement the spec rules."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

IST = timezone(timedelta(hours=5, minutes=30))

_NON_DIGIT = re.compile(r"[^0-9]")
_WS_OR_UNDERSCORE = re.compile(r"[_\s]+")


def normalize_header(h: str) -> str:
    """lower-case, trim, then collapse every run of '_' / whitespace to one space."""
    return _WS_OR_UNDERSCORE.sub(" ", h.lower().strip())


def normalize_phone(raw) -> Optional[str]:
    """Indian mobile -> canonical '+91XXXXXXXXXX', or None if invalid."""
    if raw is None:
        return None
    digits = _NON_DIGIT.sub("", str(raw))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in "6789":
        return "+91" + digits
    return None


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})", re.ASCII)
_DMY_SLASH = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})", re.ASCII)
_DMY_DASH = re.compile(r"(\d{1,2})-(\d{1,2})-(\d{4})", re.ASCII)
_D_MON_Y = re.compile(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})", re.ASCII)


def parse_date(raw: str) -> Optional[date]:
    """YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY, DD-Mon-YYYY. Day-first. Impossible dates -> None."""
    s = raw.strip()
    try:
        if m := _ISO.fullmatch(s):
            y, mo, d = int(m[1]), int(m[2]), int(m[3])
        elif m := (_DMY_SLASH.fullmatch(s) or _DMY_DASH.fullmatch(s)):
            d, mo, y = int(m[1]), int(m[2]), int(m[3])
        elif m := _D_MON_Y.fullmatch(s):
            d, y = int(m[1]), int(m[3])
            mo = _MONTHS.get(m[2].lower())
            if mo is None:
                return None
        else:
            return None
        return date(y, mo, d)
    except ValueError:  # e.g. 31/02
        return None


_DECIMAL = re.compile(r"(?:₹|rs\.?|inr)?\s*(\d[\d,]*(?:\.\d+)?|\.\d+)", re.IGNORECASE)


def parse_decimal(raw: str) -> Optional[Decimal]:
    """Optional currency prefix, commas as thousands separators, >= 0, rounded to 2 places."""
    m = _DECIMAL.fullmatch(raw.strip())
    if not m:
        return None
    return Decimal(m[1].replace(",", "")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def parse_timestamp(raw) -> Optional[datetime]:
    """Event timestamp -> aware UTC datetime. 'Z' = UTC, '+05:30' = offset, naive = IST."""
    if not isinstance(raw, str):
        return None
    try:
        dt = datetime.fromisoformat(raw.strip())
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(timezone.utc)
