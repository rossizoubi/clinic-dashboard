"""Keeps bookable slots available for the coming days, generated from the doctors' fixed hours.

Runs on startup and again the first time the site is used each day, so the booking window
rolls forward on its own. Generating is idempotent: existing slots (and bookings) are kept.
"""

import datetime as dt
import logging
import threading

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import clinic, config, services
from app.clock import today
from app.models import Doctor

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_last_run: dt.date | None = None


def ensure_schedule(db: Session, start: dt.date | None = None, days: int | None = None) -> int:
    """Create any missing slots from the clinic's working hours. Returns how many were created."""
    start = start or today()
    days = clinic.BOOKING_WINDOW_DAYS if days is None else days
    created = 0
    for doctor in db.scalars(select(Doctor)):
        for hours in clinic.hours_for(doctor.name):
            for offset in range(days):
                day = start + dt.timedelta(days=offset)
                if day.weekday() in hours.days:
                    result = services.generate_slots(db, doctor.id, day, hours.start, hours.end, hours.slot_minutes)
                    created += result.created
    if created:
        logger.info("Added %d bookable slots for the next %d days", created, days)
    return created


def ensure_schedule_daily(db: Session) -> None:
    """Run ensure_schedule at most once per clinic day (cheap to call on every request)."""
    global _last_run
    if not config.AUTO_SCHEDULE:
        return
    current = today()
    if _last_run == current:
        return
    with _lock:
        if _last_run != current:
            ensure_schedule(db, current)
            _last_run = current
