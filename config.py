import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()


def _as_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}

class Config:
    FLASK_ENV = os.getenv("FLASK_ENV", "production").strip().lower()
    IS_DEV = FLASK_ENV == "development"

    OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
    OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
    GOOGLE_AI_STUDIO_API_KEY = os.getenv("GOOGLE_AI_STUDIO_API_KEY", "")
    GOOGLE_AI_STUDIO_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
    NVIDIA_NIM_API_KEY = os.getenv("NVIDIA_NIM_API_KEY", "")
    NVIDIA_NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
    SEARXNG_URL = os.getenv("SEARXNG_URL", "http://evans-rasberry-pi.local:8888")
    SECRET_KEY = os.getenv("SECRET_KEY", "").strip()
    DB_PATH = os.getenv("DB_PATH", "personal_ai.db")
    UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")
    MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
    SANDBOX_TIMEOUT = 10  # seconds
    MODELS_CACHE_TTL = 600  # 10 minutes
    TRUST_X_FORWARDED_FOR = _as_bool("TRUST_X_FORWARDED_FOR", False)

    # Dangerous features are opt-in only.
    ENABLE_UNSAFE_PYTHON_TOOL = _as_bool("ENABLE_UNSAFE_PYTHON_TOOL", True)
    ENABLE_BROWSER_FETCH = _as_bool("ENABLE_BROWSER_FETCH", True)

    # Session security
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # Only send cookies over HTTPS when explicitly opted in (set SESSION_COOKIE_SECURE=true
    # in .env when running behind an HTTPS reverse proxy in production).
    SESSION_COOKIE_SECURE = _as_bool("SESSION_COOKIE_SECURE", False)
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)

    if not SECRET_KEY:
        raise RuntimeError("SECRET_KEY must be set in environment.")

    if SECRET_KEY in {"dev-secret-change-me", "change-me-to-a-random-string"}:
        raise RuntimeError("SECRET_KEY is insecure. Set a strong random value.")

    if len(SECRET_KEY) < 32:
        raise RuntimeError("SECRET_KEY is too short. Use at least 32 characters.")

config = Config()
