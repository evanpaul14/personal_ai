import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
    OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
    SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8888")
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
    DB_PATH = os.getenv("DB_PATH", "personal_ai.db")
    UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")
    MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
    SANDBOX_TIMEOUT = 10  # seconds
    MODELS_CACHE_TTL = 600  # 10 minutes

config = Config()
