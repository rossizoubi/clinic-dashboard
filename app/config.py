"""Application settings loaded from environment variables (and an optional .env file)."""

import logging
import os

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _num_threads() -> int | None:
    """CPU threads for Ollama. Defaults to half the logical CPUs (one per physical core on most machines).

    Ollama otherwise uses every logical CPU, which on hyper-threaded CPUs and inside Docker Desktop's
    VM made generation about 2x slower in our measurements. Set OLLAMA_NUM_THREAD=0 to let Ollama decide.
    """
    raw = os.getenv("OLLAMA_NUM_THREAD", "").strip()
    if not raw:
        return max(1, (os.cpu_count() or 2) // 2)
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid OLLAMA_NUM_THREAD, letting Ollama decide")
        return None
    return value if value > 0 else None


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        logger.warning("Invalid %s, using %s", name, default)
        return default


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./clinic.db")
CLINIC_TIMEZONE = os.getenv("CLINIC_TIMEZONE", "Asia/Amman")
# Generate bookable slots from the doctors' working hours (app/clinic.py) automatically.
AUTO_SCHEDULE = os.getenv("AUTO_SCHEDULE", "1").strip().lower() not in ("0", "false", "no")

# --- AI assistant -----------------------------------------------------------
# Local-first: Ollama is the default and needs no API key or internet connection.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
# Optional second provider, used only when the first one fails. Off when empty.
LLM_FALLBACK_PROVIDER = os.getenv("LLM_FALLBACK_PROVIDER", "").strip().lower()

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
# Low temperature makes small models more consistent at tool calling. Kept within 0-0.2.
OLLAMA_TEMPERATURE = min(max(_float("OLLAMA_TEMPERATURE", 0.1), 0.0), 0.2)
# The first request loads the model into memory, which can take a while on CPU.
OLLAMA_TIMEOUT = _float("OLLAMA_TIMEOUT", 120.0)
OLLAMA_NUM_THREAD = _num_threads()
# Load the model and cache the system prompt in the background when the server starts,
# so the first patient doesn't wait for a cold start.
ASSISTANT_WARMUP = os.getenv("ASSISTANT_WARMUP", "1").strip().lower() not in ("0", "false", "no")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-5-5")
