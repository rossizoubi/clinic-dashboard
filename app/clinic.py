"""The clinic's doctors and their fixed weekly working hours.

This is the one place to change who works when. Bookable time slots are generated from
these hours automatically for the next BOOKING_WINDOW_DAYS days (see app/schedule.py), so
patients can always book ahead without anyone creating schedules by hand.
"""

import datetime as dt
import os
from dataclasses import dataclass

MON, TUE, WED, THU, FRI, SAT, SUN = range(7)  # same numbering as date.weekday()
WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# How many days ahead patients can book.
BOOKING_WINDOW_DAYS = int(os.getenv("BOOKING_WINDOW_DAYS", "14"))


@dataclass(frozen=True)
class WorkingHours:
    days: tuple[int, ...]
    start: dt.time
    end: dt.time
    slot_minutes: int = 30

    def label(self) -> str:
        return f"{_days_label(self.days)} · {self.start:%H:%M}–{self.end:%H:%M}"


@dataclass(frozen=True)
class DoctorProfile:
    name: str
    specialty: str
    hours: tuple[WorkingHours, ...]


def _t(text: str) -> dt.time:
    return dt.time.fromisoformat(text)


def _days_label(days: tuple[int, ...]) -> str:
    """(SUN, MON, TUE, WED, THU) -> 'Sun–Thu'; (SUN, TUE, THU) -> 'Sun, Tue, Thu'."""
    order = sorted(days, key=lambda d: (d - SUN) % 7)  # weeks start on Sunday here
    steps = [(d - SUN) % 7 for d in order]
    if len(order) > 2 and steps == list(range(steps[0], steps[0] + len(steps))):
        return f"{WEEKDAY_NAMES[order[0]]}–{WEEKDAY_NAMES[order[-1]]}"
    return ", ".join(WEEKDAY_NAMES[d] for d in order)


# Fridays are closed. Hours are examples for a small clinic in Amman; adjust freely.
# Keep the order stable: doctors are created in this order, so it decides their database IDs.
DOCTORS: tuple[DoctorProfile, ...] = (
    DoctorProfile(
        "Dr. Zoubi",
        "Cardiology",
        (WorkingHours((SUN, MON, WED), _t("10:00"), _t("14:00"), 30),),
    ),
    DoctorProfile(
        "Dr. Sarah",
        "Pediatrics",
        (
            WorkingHours((SUN, TUE, THU), _t("09:00"), _t("14:00"), 30),
            WorkingHours((SAT,), _t("10:00"), _t("13:00"), 30),
        ),
    ),
    DoctorProfile(
        "Dr. Rasool",
        "Dermatology",
        (
            WorkingHours((MON, WED), _t("15:00"), _t("19:00"), 30),
            WorkingHours((SAT,), _t("10:00"), _t("14:00"), 30),
        ),
    ),
    DoctorProfile(
        "Dr. Ali",
        "General Medicine",
        (WorkingHours((SUN, MON, TUE, WED, THU), _t("09:00"), _t("13:00"), 20),),
    ),
)

PROFILES = {profile.name: profile for profile in DOCTORS}


def hours_for(doctor_name: str) -> tuple[WorkingHours, ...]:
    profile = PROFILES.get(doctor_name)
    return profile.hours if profile else ()
