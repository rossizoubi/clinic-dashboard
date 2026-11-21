"""Patient self-service booking: fixed doctor hours, the 3-step booking flow, and My appointments."""

import datetime as dt
import re

import pytest

from app import clinic, schedule, services
from app.clock import today
from app.models import Appointment, Doctor

PHONE = "0791234567"


def next_weekday(weekday: int, after: dt.date | None = None) -> dt.date:
    """The next date (after `after`, default today) that falls on `weekday`."""
    after = after or today()
    return after + dt.timedelta(days=(weekday - after.weekday()) % 7 or 7)


@pytest.fixture
def ali(db) -> Doctor:
    return db.query(Doctor).filter_by(name="Dr. Ali").one()


@pytest.fixture
def ali_day(db, ali):
    """Slots for Dr. Ali on his next Sunday, generated from the clinic's fixed hours."""
    day = next_weekday(clinic.SUN)
    schedule.ensure_schedule(db, start=day, days=1)
    return day, services.list_slots(db, ali.id, day)


# --- Fixed working hours -> slots ------------------------------------------------------


def test_hours_labels():
    assert clinic.PROFILES["Dr. Ali"].hours[0].label() == "Sun–Thu · 09:00–13:00"
    assert clinic.PROFILES["Dr. Sarah"].hours[0].label() == "Sun, Tue, Thu · 09:00–14:00"


def test_slots_follow_each_doctors_working_hours(db):
    start = next_weekday(clinic.SAT)  # a full week starting on a Saturday
    schedule.ensure_schedule(db, start=start, days=7)
    by_doctor = {}
    for slot in db.query(Appointment).join(Doctor):
        by_doctor.setdefault(slot.doctor.name, set()).add(slot.date.weekday())

    assert by_doctor["Dr. Ali"] == {clinic.SUN, clinic.MON, clinic.TUE, clinic.WED, clinic.THU}
    assert by_doctor["Dr. Rasool"] == {clinic.MON, clinic.WED, clinic.SAT}
    assert clinic.FRI not in set().union(*by_doctor.values())  # closed on Fridays

    sunday = start + dt.timedelta(days=1)
    ali = db.query(Doctor).filter_by(name="Dr. Ali").one()
    times = [s.start_time.strftime("%H:%M") for s in services.list_slots(db, ali.id, sunday)]
    assert times[:3] == ["09:00", "09:20", "09:40"] and times[-1] == "12:40" and len(times) == 12


def test_generating_again_keeps_bookings_and_adds_nothing(db, ali_day):
    day, slots = ali_day
    services.book_slot(db, slots[0].id, "Lina Haddad", PHONE)

    assert schedule.ensure_schedule(db, start=day, days=1) == 0
    assert services.get_slot(db, slots[0].id).patient_name == "Lina Haddad"


def test_daily_refresh_runs_once_per_day(db, monkeypatch):
    calls = []
    monkeypatch.setattr(schedule.config, "AUTO_SCHEDULE", True)
    monkeypatch.setattr(schedule, "_last_run", None)
    monkeypatch.setattr(schedule, "ensure_schedule", lambda db, start: calls.append(start))

    schedule.ensure_schedule_daily(db)
    schedule.ensure_schedule_daily(db)
    assert calls == [today()]


def test_booking_reference_format():
    reference = services.new_reference()
    assert re.fullmatch(r"[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}", reference)


# --- Step 1: choose a doctor -------------------------------------------------------------


def test_doctor_list_shows_hours_and_next_free_time(client, ali_day):
    day, slots = ali_day
    response = client.get("/")
    assert response.status_code == 200
    assert "Sun–Thu · 09:00–13:00" in response.text
    assert f"{day.strftime('%a %d %b')}, 09:00" in response.text
    assert 'href="/doctors/' in response.text


def test_specialty_filter(client):
    response = client.get("/", params={"specialty": "Cardiology"})
    assert "Dr. Zoubi" in response.text and "Dr. Ali</h2>" not in response.text
    # An unknown specialty just shows everyone.
    assert "Dr. Ali</h2>" in client.get("/", params={"specialty": "Astrology"}).text


# --- Step 2: choose a time -----------------------------------------------------------------


def test_time_page_lists_free_times_for_the_day(client, db, ali, ali_day):
    day, slots = ali_day
    services.book_slot(db, slots[0].id, "Omar Khalil", "0787654321")

    response = client.get(f"/doctors/{ali.id}", params={"date": day.isoformat()})

    assert response.status_code == 200
    assert f'href="/book/{slots[0].id}"' not in response.text  # taken
    assert f'href="/book/{slots[1].id}"' in response.text
    assert "11 free" in response.text
    assert "Omar" not in response.text  # never show other patients


def test_time_page_defaults_to_the_first_day_with_free_times(client, ali, ali_day):
    day, _ = ali_day
    response = client.get(f"/doctors/{ali.id}")
    assert day.strftime("%A, %d %B") in response.text


def test_closed_days_are_marked(client, ali):
    assert "Closed" in client.get(f"/doctors/{ali.id}").text  # Fridays and Saturdays


def test_unknown_doctor_goes_back_to_the_list(client):
    response = client.get("/doctors/999", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/?error=doctor_not_found"


# --- Step 3: details and confirmation ------------------------------------------------------


def test_full_booking_flow(client, db, ali_day):
    day, slots = ali_day
    slot = slots[2]

    form = client.get(f"/book/{slot.id}")
    assert form.status_code == 200 and "Confirm booking" in form.text

    response = client.post(
        f"/book/{slot.id}", data={"patient_name": " Lina  Haddad ", "phone_number": "079-123-4567"}, follow_redirects=False
    )
    assert response.status_code == 303
    reference = response.headers["location"].rsplit("/", 1)[-1]

    db.refresh(slot)
    assert slot.is_booked and slot.patient_name == "Lina Haddad" and slot.reference == reference

    confirmation = client.get(f"/appointments/{reference}")
    assert "Your appointment is booked" in confirmation.text
    assert reference in confirmation.text and "09:40" in confirmation.text
    assert PHONE not in confirmation.text and "567" in confirmation.text  # phone is masked

    calendar = client.get(f"/appointments/{reference}/calendar.ics")
    assert calendar.headers["content-type"].startswith("text/calendar")
    assert f"DTSTART;TZID=Asia/Amman:{day.strftime('%Y%m%d')}T094000" in calendar.text


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"patient_name": "", "phone_number": PHONE}, "Please enter your full name"),
        ({"patient_name": "Lina", "phone_number": "12"}, "valid phone number"),
    ],
)
def test_invalid_details_keep_the_form(client, db, ali_day, data, message):
    _, slots = ali_day
    response = client.post(f"/book/{slots[0].id}", data=data)
    assert response.status_code == 400
    assert message in response.text
    assert f'value="{data["phone_number"]}"' in response.text  # what they typed is kept
    db.refresh(slots[0])
    assert not slots[0].is_booked


def test_taken_time_sends_the_patient_back_to_choose_again(client, db, ali, ali_day):
    day, slots = ali_day
    services.book_slot(db, slots[0].id, "Omar Khalil", "0787654321")

    for response in (
        client.get(f"/book/{slots[0].id}", follow_redirects=False),
        client.post(f"/book/{slots[0].id}", data={"patient_name": "Lina", "phone_number": PHONE}, follow_redirects=False),
    ):
        assert response.status_code == 303
        assert response.headers["location"] == f"/doctors/{ali.id}?date={day.isoformat()}&error=slot_taken"
    assert services.get_slot(db, slots[0].id).patient_name == "Omar Khalil"


def test_patient_cannot_double_book_the_same_doctor(client, db, ali_day):
    _, slots = ali_day
    services.book_slot(db, slots[0].id, "Lina Haddad", PHONE)
    response = client.post(f"/book/{slots[5].id}", data={"patient_name": "Lina Haddad", "phone_number": PHONE})
    assert response.status_code == 409
    assert "You already have an appointment with this doctor" in response.text


def test_unknown_reference(client):
    assert client.get("/appointments/ZZZZ-ZZZZ").status_code == 404
    assert client.get("/appointments/ZZZZ-ZZZZ/calendar.ics").status_code == 404


# --- My appointments -----------------------------------------------------------------------


@pytest.fixture
def lina_booking(db, ali_day):
    _, slots = ali_day
    return services.book_slot(db, slots[0].id, "Lina Haddad", PHONE)


def test_my_appointments_lists_only_matching_bookings(client, db, ali_day, lina_booking):
    _, slots = ali_day
    services.book_slot(db, slots[1].id, "Omar Khalil", "0787654321")

    response = client.post("/my-appointments", data={"patient_name": "lina haddad", "phone_number": "079 123 4567"})
    assert lina_booking.reference in response.text
    assert "Omar" not in response.text

    wrong = client.post("/my-appointments", data={"patient_name": "Lina Haddad", "phone_number": "0787654321"})
    assert "No upcoming appointments found" in wrong.text and lina_booking.reference not in wrong.text


def test_cancel_from_my_appointments(client, db, lina_booking):
    response = client.post(
        "/my-appointments/cancel", data={"slot_id": lina_booking.id, "patient_name": "Lina Haddad", "phone_number": PHONE}
    )
    assert response.status_code == 200 and "Your appointment was cancelled." in response.text
    db.refresh(lina_booking)
    assert not lina_booking.is_booked and lina_booking.reference is None


def test_cannot_cancel_someone_elses_booking(client, db, lina_booking):
    response = client.post(
        "/my-appointments/cancel", data={"slot_id": lina_booking.id, "patient_name": "Omar Khalil", "phone_number": PHONE}
    )
    assert response.status_code == 404
    db.refresh(lina_booking)
    assert lina_booking.is_booked


def test_lookup_validates_input(client):
    response = client.post("/my-appointments", data={"patient_name": "Lina", "phone_number": "1"})
    assert response.status_code == 400 and "valid phone number" in response.text


def test_navigation_marks_the_current_section(client):
    assert client.get("/").text.count('aria-current="page"') == 1
    page = client.get("/my-appointments").text
    assert re.search(r'href="/my-appointments" aria-current="page"', page)
