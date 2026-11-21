"""Patient-facing text for message codes.

Only codes are put in URLs; the text always comes from here, so a crafted
link can't inject arbitrary content into the page.
"""

ERRORS = {
    "doctor_not_found": "We couldn't find that doctor. Please choose one from the list.",
    "slot_not_found": "That appointment time is no longer available. Please choose another time.",
    "slot_taken": "Sorry, that time was just booked or has already started. Please choose another time.",
    "slot_already_booked": "Sorry, that time was just booked. Please choose another time.",
    "slot_in_past": "That time has already started. Please choose a later time.",
    "patient_already_booked": (
        "You already have an appointment with this doctor on that day, or another appointment at "
        "the same time. You can manage it from My appointments."
    ),
    "booking_not_found": "We couldn't find that appointment. It may already have been cancelled.",
    "invalid_name": "Please enter your full name (up to 100 characters).",
    "invalid_phone": "Please enter a valid phone number (7-15 digits, optionally starting with +).",
    "invalid_input": "Some details weren't valid. Please check the form and try again.",
}

GENERIC_ERROR = "Something went wrong. Please try again."


def error_message(code: str | None) -> str | None:
    if not code:
        return None
    return ERRORS.get(code, GENERIC_ERROR)
