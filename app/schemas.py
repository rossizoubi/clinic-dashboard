"""Input validation for the booking forms.

Custom checks raise PydanticCustomError with a short code (e.g. "invalid_phone"),
so routes can turn any ValidationError into a message code with `error_code()`.
"""

import re

from pydantic import BaseModel, ValidationError, field_validator
from pydantic_core import PydanticCustomError

MAX_NAME_LENGTH = 100


def normalize_name(value: str) -> str:
    """Collapse whitespace; raise PydanticCustomError if empty or too long."""
    value = " ".join(value.split())
    if not value or len(value) > MAX_NAME_LENGTH:
        raise PydanticCustomError("invalid_name", "Name must be 1-100 characters")
    return value


def normalize_phone(value: str) -> str:
    """Normalize a phone number to an optional '+' followed by 7-15 digits."""
    value = value.strip()
    has_plus = value.startswith("+")
    digits = re.sub(r"[\s\-().]", "", value[1:] if has_plus else value)
    if not digits.isdigit() or not 7 <= len(digits) <= 15:
        raise PydanticCustomError("invalid_phone", "Phone number must contain 7-15 digits")
    return ("+" if has_plus else "") + digits


class BookingCreate(BaseModel):
    patient_name: str
    phone_number: str

    @field_validator("patient_name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return normalize_name(value)

    @field_validator("phone_number")
    @classmethod
    def clean_phone(cls, value: str) -> str:
        return normalize_phone(value)


_CUSTOM_CODES = {"invalid_name", "invalid_phone"}
_FIELD_CODES = {"patient_name": "invalid_name", "phone_number": "invalid_phone"}


def error_code(exc: ValidationError) -> str:
    """Map the first validation error to a message code."""
    err = exc.errors()[0]
    if err["type"] in _CUSTOM_CODES:
        return err["type"]
    field = err["loc"][0] if err["loc"] else None
    return _FIELD_CODES.get(field, "invalid_input")
