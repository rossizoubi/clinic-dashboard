"""Validation of the patient booking form (name and phone number)."""

import pytest
from pydantic import ValidationError

from app.schemas import BookingCreate, error_code


def code_for(**data) -> str:
    with pytest.raises(ValidationError) as exc_info:
        BookingCreate(**data)
    return error_code(exc_info.value)


def test_booking_name_is_normalised():
    data = BookingCreate(patient_name="  Lina    Haddad ", phone_number="0791234567")
    assert data.patient_name == "Lina Haddad"


@pytest.mark.parametrize("name", ["", "   ", "x" * 101])
def test_invalid_patient_name(name):
    assert code_for(patient_name=name, phone_number="0791234567") == "invalid_name"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("079 123 4567", "0791234567"), ("+962 (79) 123-4567", "+962791234567")],
)
def test_phone_is_normalised(raw, expected):
    assert BookingCreate(patient_name="Lina", phone_number=raw).phone_number == expected


@pytest.mark.parametrize("phone", ["", "123", "phone", "07912345abc", "1" * 16, "++962791234567"])
def test_invalid_phone(phone):
    assert code_for(patient_name="Lina", phone_number=phone) == "invalid_phone"
