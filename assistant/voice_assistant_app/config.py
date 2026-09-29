import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"

if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH)
else:
    load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

DEFAULT_VOICE = os.getenv("DEFAULT_VOICE", "en-US-AvaMultilingualNeural")  # Expressive Conversational Ava
ALTERNATIVE_VOICE = os.getenv("ALTERNATIVE_VOICE", "en-IN-NeerjaExpressiveNeural")  # Indian Expressive English

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq").lower()

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True, parents=True)

WORKFLOWS_JSON_PATH = DATA_DIR / "workflows.json"
WORKFLOWS_TXT_PATH = DATA_DIR / "workflows.txt"
STATIC_DIR = BASE_DIR / "frontend"

DOMAIN_KEYWORDS = [
    "Zomato", "Domino's", "Swiggy", "Blinkit", "Zepto",
    "Margherita pizza", "Farmhouse pizza", "garlic bread",
    "Amazon", "Myntra", "Flipkart", "phone case", "wireless earbuds",
    "checkout", "cart", "payment", "UPI", "OTP", "deliver to work", "deliver to home"
]
WHISPER_PROMPT = ", ".join(DOMAIN_KEYWORDS)
