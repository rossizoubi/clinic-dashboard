"""Patient self-service booking: choose a doctor, a day and a time, then confirm.

Also lets patients look up and cancel their own bookings (name + phone number required).
There are no staff pages: doctors' hours come from app/clinic.py and slots are generated
automatically (app/schedule.py).
"""

import datetime as dt
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app import clinic, config, messages, schedule, services
from app.clock import now, today
from app.database import get_db
from app.models import Appointment, Doctor
from app.schemas import BookingCreate, error_code
from app.templating import templates

router = APIRouter()

PERIODS = (("Morning", dt.time(0), dt.time(12)), ("Afternoon", dt.time(12), dt.time(17)), ("Evening", dt.time(17), dt.time.max))


def url(path: str, **params) -> str:
    """Build a URL with properly encoded query parameters (empty values are dropped)."""
    query = urlencode({k: v for k, v in params.items() if v not in (None, "")})
    return f"{path}?{query}" if query else path


def redirect(path: str, **params) -> RedirectResponse:
    return RedirectResponse(url=url(path, **params), status_code=303)


def _parse_date(value: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _render(request: Request, name: str, status_code: int = 200, **context) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name=name, context=context, status_code=status_code)


def _get_doctor(db: Session, doctor_id: int) -> Doctor | None:
    return db.get(Doctor, doctor_id)


def _bookable(slot: Appointment) -> bool:
    return not slot.is_booked and dt.datetime.combine(slot.date, slot.start_time) > now()


def _mask_phone(phone: str | None) -> str:
    return f"{'•' * max(len(phone) - 3, 0)}{phone[-3:]}" if phone else ""


# --- Step 1: choose a doctor ---------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
def choose_doctor(
    request: Request,
    specialty: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    schedule.ensure_schedule_daily(db)
    doctors = services.list_doctors(db)
    specialties = sorted({d.specialty for d in doctors})
    if specialty not in specialties:
        specialty = None
    shown = [d for d in doctors if specialty is None or d.specialty == specialty]
    cards = [
        {"doctor": d, "hours": clinic.hours_for(d.name), "next_slot": services.next_free_slot(db, d.id)}
        for d in shown
    ]
    return _render(
        request,
        "book_doctor.html",
        cards=cards,
        specialties=specialties,
        specialty=specialty,
        error_message=messages.error_message(error),
    )


# --- Step 2: choose a day and time ---------------------------------------------------


@router.get("/doctors/{doctor_id}", response_class=HTMLResponse)
def choose_time(
    request: Request,
    doctor_id: int,
    date: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    schedule.ensure_schedule_daily(db)
    doctor = _get_doctor(db, doctor_id)
    if doctor is None:
        return redirect("/", error="doctor_not_found")

    first_day = today()
    window = [first_day + dt.timedelta(days=i) for i in range(clinic.BOOKING_WINDOW_DAYS)]
    free = services.free_slots_by_day(db, doctor.id, first_day, len(window))
    working_days = {d for h in clinic.hours_for(doctor.name) for d in h.days}

    selected = _parse_date(date)
    if selected not in window:
        selected = next((d for d in window if free.get(d)), first_day)

    slots = services.list_available_slots(db, doctor.id, selected)
    periods = [
        (label, [s for s in slots if start <= s.start_time < end]) for label, start, end in PERIODS
    ]
    days = [
        {
            "date": d,
            "free": free.get(d, 0),
            "closed": d.weekday() not in working_days,
            "url": url(f"/doctors/{doctor.id}", date=d.isoformat()),
        }
        for d in window
    ]
    return _render(
        request,
        "book_time.html",
        doctor=doctor,
        hours=clinic.hours_for(doctor.name),
        days=days,
        selected=selected,
        periods=[p for p in periods if p[1]],
        error_message=messages.error_message(error),
    )


# --- Step 3: patient details and confirmation ---------------------------------------


def _details_page(request: Request, slot: Appointment, status_code: int = 200, **context) -> HTMLResponse:
    return _render(request, "book_details.html", status_code=status_code, slot=slot, doctor=slot.doctor, **context)


@router.get("/book/{slot_id}", response_class=HTMLResponse)
def booking_form(request: Request, slot_id: int, db: Session = Depends(get_db)):
    slot = db.get(Appointment, slot_id)
    if slot is None:
        return redirect("/", error="slot_not_found")
    if not _bookable(slot):
        return redirect(f"/doctors/{slot.doctor_id}", date=slot.date.isoformat(), error="slot_taken")
    return _details_page(request, slot)


@router.post("/book/{slot_id}", response_class=HTMLResponse)
def book(
    request: Request,
    slot_id: int,
    patient_name: str = Form(""),
    phone_number: str = Form(""),
    db: Session = Depends(get_db),
):
    slot = db.get(Appointment, slot_id)
    if slot is None:
        return redirect("/", error="slot_not_found")
    values = {"patient_name": patient_name, "phone_number": phone_number}
    try:
        data = BookingCreate(patient_name=patient_name, phone_number=phone_number)
    except ValidationError as exc:
        return _details_page(request, slot, 400, values=values, error_message=messages.error_message(error_code(exc)))

    try:
        booked = services.book_slot(db, slot_id, data.patient_name, data.phone_number)
    except (services.SlotAlreadyBooked, services.SlotInPast):
        return redirect(f"/doctors/{slot.doctor_id}", date=slot.date.isoformat(), error="slot_taken")
    except services.ServiceError as exc:
        return _details_page(request, slot, 409, values=values, error_message=messages.error_message(exc.code))
    return redirect(f"/appointments/{booked.reference}")


@router.get("/appointments/{reference}", response_class=HTMLResponse)
def booking_confirmed(request: Request, reference: str, db: Session = Depends(get_db)):
    try:
        slot = services.get_booking_by_reference(db, reference)
    except services.BookingNotFound:
        return _render(request, "booking_missing.html", status_code=404)
    return _render(request, "booking_confirmed.html", slot=slot, doctor=slot.doctor, masked_phone=_mask_phone(slot.phone_number))


@router.get("/appointments/{reference}/calendar.ics")
def booking_calendar(reference: str, db: Session = Depends(get_db)):
    try:
        slot = services.get_booking_by_reference(db, reference)
    except services.BookingNotFound:
        return Response(status_code=404)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    start = dt.datetime.combine(slot.date, slot.start_time).strftime("%Y%m%dT%H%M%S")
    end = dt.datetime.combine(slot.date, slot.end_time).strftime("%Y%m%dT%H%M%S")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//ClinicBook//Appointments//EN",
        "BEGIN:VEVENT",
        f"UID:{slot.reference}@clinicbook",
        f"DTSTAMP:{stamp}",
        f"DTSTART;TZID={config.CLINIC_TIMEZONE}:{start}",
        f"DTEND;TZID={config.CLINIC_TIMEZONE}:{end}",
        f"SUMMARY:Appointment with {slot.doctor.name} ({slot.doctor.specialty})",
        f"DESCRIPTION:Booking reference {slot.reference}",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return Response(
        "\r\n".join(lines) + "\r\n",
        media_type="text/calendar",
        headers={"Content-Disposition": f'attachment; filename="appointment-{slot.reference}.ics"'},
    )


# --- My appointments -------------------------------------------------------------------


def _my_appointments(request: Request, db: Session, name: str, phone: str, status_code: int = 200, **context):
    """Look up a patient's bookings. Nothing is shown unless name AND phone match."""
    try:
        data = BookingCreate(patient_name=name, phone_number=phone)
    except ValidationError as exc:
        return _render(
            request, "my_appointments.html", 400, values={"patient_name": name, "phone_number": phone},
            error_message=messages.error_message(error_code(exc)), **context,
        )
    bookings = services.find_patient_bookings(db, data.patient_name, data.phone_number)
    return _render(
        request, "my_appointments.html", status_code, values={"patient_name": name, "phone_number": phone},
        searched=True, bookings=bookings, **context,
    )


@router.get("/my-appointments", response_class=HTMLResponse)
def my_appointments_form(request: Request):
    return _render(request, "my_appointments.html", values={})


# POST (not GET) so names and phone numbers never end up in URLs, logs or browser history.
@router.post("/my-appointments", response_class=HTMLResponse)
def my_appointments(
    request: Request,
    patient_name: str = Form(""),
    phone_number: str = Form(""),
    db: Session = Depends(get_db),
):
    return _my_appointments(request, db, patient_name, phone_number)


@router.post("/my-appointments/cancel", response_class=HTMLResponse)
def cancel_my_appointment(
    request: Request,
    slot_id: int = Form(...),
    patient_name: str = Form(""),
    phone_number: str = Form(""),
    db: Session = Depends(get_db),
):
    try:
        data = BookingCreate(patient_name=patient_name, phone_number=phone_number)
        services.cancel_patient_booking(db, slot_id, data.patient_name, data.phone_number)
    except (ValidationError, services.ServiceError):
        return _my_appointments(
            request, db, patient_name, phone_number, 404, error_message=messages.error_message("booking_not_found")
        )
    return _my_appointments(request, db, patient_name, phone_number, success_message="Your appointment was cancelled.")
