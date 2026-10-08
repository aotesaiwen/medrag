"""Authenticated application-side gateway to the local, resident TTS worker."""

import asyncio
import io
import wave

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

from rag.config import Settings
from speech.text import clean_text

MAX_AUDIO_BYTES = 64 * 1024 * 1024


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=6000)

    @field_validator('text')
    @classmethod
    def spoken_text(cls, value: str) -> str:
        text = clean_text(value)
        if not text or not any(char.isalnum() for char in text):
            raise ValueError('Spoken text is required.')
        return text


def validate_wav(audio: bytes) -> None:
    try:
        with wave.open(io.BytesIO(audio)) as wav:
            frames = wav.getnframes()
            if (wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != 24000
                    or frames <= 0 or len(wav.readframes(frames)) != frames * 2):
                raise ValueError('Invalid audio')
    except (wave.Error, EOFError, ValueError):
        raise HTTPException(502, 'The speech service returned invalid audio.') from None


async def synthesize(request: SpeechRequest, config: Settings) -> bytes:
    async def fetch() -> bytes:
        async with httpx.AsyncClient(timeout=config.speech_timeout, trust_env=False) as client:
            async with client.stream('POST', config.speech_url.rstrip('/') + '/synthesize',
                                     json={'text': request.text},
                                     headers={'Authorization': f'Bearer {config.api_key}'}) as response:
                if response.status_code in {429, 503, 504}:
                    raise HTTPException(response.status_code, 'The speech service is busy or unavailable.')
                response.raise_for_status()
                if response.headers.get('content-type', '').split(';')[0] != 'audio/wav':
                    raise HTTPException(502, 'The speech service returned invalid audio.')
                audio = bytearray()
                async for chunk in response.aiter_bytes():
                    audio.extend(chunk)
                    if len(audio) > MAX_AUDIO_BYTES:
                        raise HTTPException(502, 'The speech service returned oversized audio.')
        data = bytes(audio)
        validate_wav(data)
        return data

    try:
        return await asyncio.wait_for(fetch(), timeout=config.speech_timeout)
    except (asyncio.TimeoutError, httpx.TimeoutException):
        raise HTTPException(504, 'Speech synthesis timed out. Try a shorter passage.') from None
    except httpx.ConnectError:
        raise HTTPException(503, 'The local speech service is not ready.') from None
    except httpx.HTTPError:
        # Never expose upstream exception text, which can contain spoken content.
        raise HTTPException(502, 'Speech synthesis failed.') from None
