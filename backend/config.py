import os
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:123@localhost:5432/procureos")
MODEL_NAME = os.getenv("MODEL_NAME", "openai/gpt-oss-120b")

if not GROQ_API_KEY:
    print("[WARN] GROQ_API_KEY is not set. Add it to backend/.env")
