from datetime import date
from decimal import Decimal

import pytest

from solution.normalize import (normalize_header, normalize_phone, parse_date, parse_decimal,
                                parse_timestamp)


@pytest.mark.parametrize("raw", [
    "9876543210", "+91 98765 43210", "09876543210", "91-98765-43210", "(+91) 9876543210",
    "+919876543210", " 98765 43210 ", "6000000000", "7000000000", "8000000000"])
def test_phone_valid_formats_normalise_to_canonical(raw):
    assert normalize_phone(raw) == "+91" + "".join(c for c in raw if c.isdigit())[-10:]


@pytest.mark.parametrize("raw", [
    "", "N/A", "12345", "987654321",            # too short
    "5876543210",                                # bad prefix
    "022-2345-6789",                             # landline (11 digits starting 0 -> 10 digits starting 2)
    "+1 415 555 0134",                           # foreign
    "98765432101",                               # 11 digits not starting with 0
    "+91 0987654321",                            # 12 digits starting 91, remainder starts with 0
    None])
def test_phone_invalid(raw):
    assert normalize_phone(raw) is None


def test_phone_91_prefix_only_stripped_when_12_digits():
    # 10-digit number that merely *starts* with 91 must be kept intact
    assert normalize_phone("9123456789") == "+919123456789"
    assert normalize_phone("919123456789") == "+919123456789"


@pytest.mark.parametrize("raw,expected", [
    ("2026-12-01", date(2026, 12, 1)),
    ("01/12/2026", date(2026, 12, 1)),     # day-first!
    ("03/04/2026", date(2026, 4, 3)),      # ambiguous: must be 3 April, not March 4
    ("01-12-2026", date(2026, 12, 1)),
    ("01-Dec-2026", date(2026, 12, 1)),
    ("01-dec-2026", date(2026, 12, 1)),
    ("01-DEC-2026", date(2026, 12, 1)),
    ("29/02/2028", date(2028, 2, 29)),     # leap year
    ("  2026-01-31 ", date(2026, 1, 31)),
])
def test_dates_valid(raw, expected):
    assert parse_date(raw) == expected


@pytest.mark.parametrize("raw", ["31/02/2026", "29/02/2027", "2026-13-01", "soon", "TBD", "", "2026/12/01",
                                 "12-31-2026", "01-Foo-2026", "1 Dec 2026", "2026-1-1"])
def test_dates_invalid(raw):
    assert parse_date(raw) is None


@pytest.mark.parametrize("raw,expected", [
    ("27554", "27554.00"), ("₹27,554", "27554.00"), ("Rs. 24787", "24787.00"), ("Rs 24787", "24787.00"),
    ("rs.24787", "24787.00"), ("INR 25301.4", "25301.40"), ("inr25301.4", "25301.40"),
    ("1,23,456.789", "123456.79"), ("0", "0.00"), ("18678.30", "18678.30"), ("0.005", "0.01"),
])
def test_decimal_valid(raw, expected):
    assert parse_decimal(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", ["N/A", "-", "twelve thousand", "-5", "", "₹", "12 34", "$100"])
def test_decimal_invalid(raw):
    assert parse_decimal(raw) is None


def test_header_normalisation():
    assert normalize_header("  Policy_No ") == "policy no"
    assert normalize_header("Mobile   __ No") == "mobile no"
    assert normalize_header("PHONE_NUMBER") == "phone number"


def test_timestamps_three_forms_agree():
    z = parse_timestamp("2026-10-03T04:31:00Z")
    off = parse_timestamp("2026-10-03T10:01:00+05:30")
    naive = parse_timestamp("2026-10-03 10:01:00")  # naive means IST
    assert z == off == naive


def test_timestamp_invalid():
    assert parse_timestamp("yesterday") is None
    assert parse_timestamp(None) is None
    assert parse_timestamp(12345) is None
