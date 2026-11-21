import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import config
from app.database import Base, SessionLocal, engine
from app.migrations import add_missing_columns
from app.routers import appointments, assistant
from app.seed import seed_doctors

APP_DIR = Path(__file__).parent

logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    add_missing_columns(engine)
    with SessionLocal() as db:
        seed_doctors(db)
    if config.ASSISTANT_WARMUP:
        threading.Thread(target=_warm_up_assistant, daemon=True).start()
    yield


def _warm_up_assistant() -> None:
    with SessionLocal() as db:
        assistant.get_assistant().warm_up(db)


app = FastAPI(title="Clinic Appointment Booking", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
app.include_router(appointments.router)
app.include_router(assistant.router)


@app.get("/health", include_in_schema=False)
def health() -> dict:
    return {"status": "ok"}
