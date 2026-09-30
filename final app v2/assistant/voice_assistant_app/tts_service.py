import io
import asyncio
from typing import Optional, AsyncGenerator
import edge_tts
from voice_assistant_app.config import DEFAULT_VOICE

class TTSService:
    """Expressive Neural Speech Synthesizer using Edge-TTS with natural conversational prosody."""

    def __init__(self, voice: Optional[str] = None):
        self.voice = voice or DEFAULT_VOICE

    async def synthesize_to_bytes(
        self,
        text: str,
        voice: Optional[str] = None,
        rate: str = "+0%",
        pitch: str = "+0Hz"
    ) -> bytes:
        """Synthesize text into natural, expressive MP3 audio bytes."""
        target_voice = voice or self.voice
        # Use conversational punctuation
        clean_text = text.replace("...", ", ").strip()
        communicate = edge_tts.Communicate(clean_text, target_voice, rate=rate, pitch=pitch)
        audio_stream = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_stream.write(chunk["data"])
        return audio_stream.getvalue()

    async def stream_audio_chunks(
        self,
        text: str,
        voice: Optional[str] = None
    ) -> AsyncGenerator[bytes, None]:
        """Stream synthesized audio chunks as they arrive for low-latency playback."""
        target_voice = voice or self.voice
        communicate = edge_tts.Communicate(text, target_voice)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                yield chunk["data"]
