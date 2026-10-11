import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")



def _int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


# Rate limits (see app/ratelimit.py). Override in .env or the host's settings.
ASK_PER_IP_10MIN = _int("ASK_PER_IP_10MIN", 8)
ASK_GLOBAL_PER_MIN = _int("ASK_GLOBAL_PER_MIN", 3)
ASK_GLOBAL_PER_DAY = _int("ASK_GLOBAL_PER_DAY", 400)
UPLOAD_PER_IP_10MIN = _int("UPLOAD_PER_IP_10MIN", 6)

# How many reverse proxies sit in front of the app. 0 = use the direct connection address.
TRUSTED_PROXY_HOPS = _int("TRUSTED_PROXY_HOPS", 0)

# Extra origins allowed to POST, comma separated, for example https://myapp.onrender.com
ALLOWED_ORIGINS = {o.strip().rstrip("/") for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()}