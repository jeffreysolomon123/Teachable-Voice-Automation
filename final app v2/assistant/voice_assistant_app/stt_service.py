import os
import io
import re
from typing import Optional
from voice_assistant_app.config import GROQ_API_KEY, GEMINI_API_KEY, WHISPER_PROMPT

class STTService:
    """Speech-to-Text service with Indian brand prompt biasing & multi-model fallback."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or GROQ_API_KEY
        self.client = None
        self.gemini_client = None

        if self.api_key:
            try:
                from groq import Groq
                self.client = Groq(api_key=self.api_key)
            except Exception as e:
                print(f"[STTService] Warning: Failed to initialize Groq client: {e}")

        if GEMINI_API_KEY:
            try:
                from google import genai
                self.gemini_client = genai.Client(api_key=GEMINI_API_KEY)
            except Exception as e:
                print(f"[STTService] Warning: Failed to initialize Gemini client: {e}")

        # Phonetic & typo correction map for common STT mishearings of Indian entities
        self.correction_map = {
            r"\b(tomato|so mato|zo mato|zomatto|zomto)\b": "Zomato",
            r"\b(the minos|dominos|dominoes|the mino's|domino)\b": "Domino's",
            r"\b(sweetie|swiggi|swigy)\b": "Swiggy",
            r"\b(margarita|margharita|margerita)\b": "Margherita",
            r"\b(farm house|farmer's house)\b": "Farmhouse",
            r"\b(ear buds|earbud|airbuds)\b": "earbuds",
            r"\b(myntra|mantra app)\b": "Myntra",
            r"\b(blink it)\b": "Blinkit",
            r"\b(zepto)\b": "Zepto",
        }

    def transcribe_audio_bytes(self, audio_bytes: bytes, filename: str = "voice_input.webm") -> str:
        """Transcribe in-memory audio bytes using Groq Whisper with Gemini fallback."""
        if not audio_bytes or len(audio_bytes) < 400:
            print("[STTService] Audio buffer empty or too short.")
            return ""

        print(f"[STTService] Receiving audio file: {filename}, size: {len(audio_bytes)} bytes")

        # Normalize extension based on content
        safe_filename = filename
        if not any(safe_filename.endswith(ext) for ext in [".webm", ".wav", ".mp3", ".ogg", ".m4a", ".mp4"]):
            safe_filename = "voice_input.webm"

        # 1. Try Groq Whisper
        if self.client:
            try:
                transcription = self.client.audio.transcriptions.create(
                    file=(safe_filename, audio_bytes),
                    model="whisper-large-v3-turbo",
                    prompt=WHISPER_PROMPT,
                    temperature=0.0,
                    language="en"
                )
                raw_text = transcription.text.strip()
                corrected = self.post_process_text(raw_text)
                print(f"[STTService] Groq Transcription: '{raw_text}' -> Corrected: '{corrected}'")
                return corrected
            except Exception as e:
                print(f"[STTService] Groq Whisper transcription error: {e}. Attempting Gemini fallback...")

        # 2. Fallback to Gemini Multimodal Audio
        if self.gemini_client:
            try:
                from google.genai import types
                mime_type = "audio/webm"
                if safe_filename.endswith(".mp4") or safe_filename.endswith(".m4a"):
                    mime_type = "audio/mp4"
                elif safe_filename.endswith(".wav"):
                    mime_type = "audio/wav"
                elif safe_filename.endswith(".mp3"):
                    mime_type = "audio/mp3"

                prompt = (
                    f"Transcribe the spoken audio verbatim in English. "
                    f"Return ONLY the plain transcription text without timestamps, notes, or explanations. "
                    f"Context keywords: {WHISPER_PROMPT}"
                )
                for model_name in ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]:
                    try:
                        response = self.gemini_client.models.generate_content(
                            model=model_name,
                            contents=[
                                types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
                                prompt
                            ]
                        )
                        if response and response.text:
                            raw_text = response.text.strip().strip('"').strip("'")
                            corrected = self.post_process_text(raw_text)
                            print(f"[STTService] Gemini ({model_name}) Transcription: '{raw_text}' -> Corrected: '{corrected}'")
                            return corrected
                    except Exception as mod_err:
                        print(f"[STTService] Gemini ({model_name}) error: {mod_err}")
            except Exception as e:
                print(f"[STTService] Gemini audio fallback error: {e}")

        return ""

    def post_process_text(self, text: str) -> str:
        """Apply brand dictionary and regex corrections to ensure brand name fidelity."""
        corrected = text
        for pattern, replacement in self.correction_map.items():
            corrected = re.sub(pattern, replacement, corrected, flags=re.IGNORECASE)
        return corrected
