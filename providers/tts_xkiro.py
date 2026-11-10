"""
xKiro text-to-speech.

The provider exposes a public voice catalogue and an authenticated speech
endpoint. It is a TTS-only integration: xKiro currently does not provide speech
transcription, so pair it with local or hosted Whisper for microphone input.

Audio is requested as WAV first so MINDER can decode it with the standard
library. If the selected voice/model rejects WAV, MP3 is attempted only when
PyAV is installed; otherwise the error explains the optional decoder.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from collections import OrderedDict
from importlib.util import find_spec
from typing import AsyncIterator

from . import audio
from .base import DEFAULT_CAPS, Field

API = "https://api.xkiro.com/v1"
DEFAULT_MODEL = "xkiro-voice"
DEFAULT_VOICE = "mexican-female"


class XKiroTTS:
    def __init__(self, cfg: dict):
        key = str(cfg.get("api_key") or "").strip()
        if not key:
            # LLM and TTS use the same xKiro account gateway. Reuse its key
            # when the user leaves this field blank; an explicit TTS key still
            # takes precedence for accounts that keep separate credentials.
            try:
                from memory.config_manager import get_provider_config
                key = str(
                    get_provider_config("openai_compat@xkiro").get("api_key") or ""
                ).strip()
            except Exception:
                key = ""
        self.key = key
        self.voice_id = str(cfg.get("voice_id") or DEFAULT_VOICE).strip()
        self.model = str(cfg.get("model") or DEFAULT_MODEL).strip()
        try:
            self.speed = float(cfg.get("speed") or 1.0)
        except (TypeError, ValueError):
            self.speed = 1.0
        self.speed = max(0.25, min(4.0, self.speed))
        self.caps = dict(DEFAULT_CAPS)
        self.caps["audio_out"] = True
        self.base = API
        # Short-lived, bounded, in-memory cache. The gateway bills by input
        # characters, so saying the same sentence twice in one session should
        # not submit and pay for the same synthesis twice. No text or audio is
        # persisted to disk.
        self._audio_cache = OrderedDict()
        self._audio_cache_bytes = 0
        self._audio_cache_limit = 12 * 1024 * 1024
        self._audio_cache_lock = threading.RLock()

    def _cache_key(self, text: str) -> str:
        material = f"{self.model}\\0{self.voice_id}\\0{self.speed:.2f}\\0{text}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _cache_get(self, key: str):
        with self._audio_cache_lock:
            value = self._audio_cache.pop(key, None)
            if value is not None:
                self._audio_cache[key] = value
            return value

    def _cache_put(self, key: str, pcm: bytes) -> None:
        if not pcm or len(pcm) > self._audio_cache_limit:
            return
        with self._audio_cache_lock:
            previous = self._audio_cache.pop(key, None)
            if previous is not None:
                self._audio_cache_bytes -= len(previous)
            self._audio_cache[key] = pcm
            self._audio_cache_bytes += len(pcm)
            while (self._audio_cache_bytes > self._audio_cache_limit
                   or len(self._audio_cache) > 64):
                _, removed = self._audio_cache.popitem(last=False)
                self._audio_cache_bytes -= len(removed)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }

    def _payload(self, text: str, fmt: str) -> dict:
        return {
            "model": self.model,
            "input": text,
            "voice": self.voice_id,
            "response_format": fmt,
            "speed": self.speed,
            "stream": True,
        }

    async def _request_audio(self, client, text: str, fmt: str) -> bytes:
        async with client.stream(
            "POST",
            f"{API}/audio/speech",
            headers=self._headers(),
            json=self._payload(text, fmt),
        ) as response:
            if response.status_code >= 400:
                body = (await response.aread()).decode("utf-8", "replace")
                raise _SpeechHTTPError(response.status_code, body)
            return await response.aread()

    async def _decode(self, data: bytes, fmt: str) -> bytes:
        if fmt == "wav":
            pcm = await asyncio.to_thread(audio.wav_to_pcm, data, audio.SPEAKER_RATE)
            if not pcm:
                raise RuntimeError("xKiro returned an empty or invalid WAV audio response.")
            return pcm
        if find_spec("av") is None:
            raise RuntimeError(
                "xKiro did not provide WAV audio and its MP3 response needs PyAV. "
                "Install it with: python -m pip install av"
            )
        from .tts_elevenlabs import _decode_mp3
        pcm = await asyncio.to_thread(_decode_mp3, data)
        if not pcm:
            raise RuntimeError("xKiro returned an empty or invalid MP3 audio response.")
        return pcm

    async def speak(self, text: str, voice: str = "", language: str = "") -> AsyncIterator[bytes]:
        import httpx
        from .tts_system import _split

        if voice:
            self.voice_id = voice
        if not self.key:
            raise RuntimeError("No xKiro API key is set for speech synthesis.")
        if not self.voice_id:
            raise RuntimeError("Choose an xKiro voice in the provider panel.")

        sentences = _split((text or "").strip(), max_chars=240)
        if not sentences:
            return

        async with httpx.AsyncClient(timeout=90.0) as client:
            for sentence in sentences:
                cache_key = self._cache_key(sentence)
                pcm = self._cache_get(cache_key)
                if pcm is None:
                    fmt = "wav"
                    try:
                        data = await self._request_audio(client, sentence, fmt)
                    except _SpeechHTTPError as exc:
                        body = exc.body.lower()
                        unsupported_format = (
                            exc.status == 400
                            and ("response_format" in body or "wav" in body or "format" in body)
                        )
                        if not unsupported_format:
                            raise RuntimeError(_http_message(exc.status, exc.body)) from None
                        fmt = "mp3"
                        try:
                            data = await self._request_audio(client, sentence, fmt)
                        except _SpeechHTTPError as mp3_exc:
                            raise RuntimeError(
                                _http_message(mp3_exc.status, mp3_exc.body)
                            ) from None

                    pcm = await self._decode(data, fmt)
                    self._cache_put(cache_key, pcm)
                for piece in audio.chunk(pcm):
                    yield piece

    def voices(self) -> list[tuple[str, str]]:
        """(voice_id, display_name) for the public xKiro voice catalogue."""
        import httpx
        with httpx.Client(timeout=12.0) as client:
            response = client.get(f"{API}/audio/voices?limit=200")
            response.raise_for_status()
            payload = response.json()
        rows = payload.get("voices", []) if isinstance(payload, dict) else []
        result = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            voice_id = str(row["id"])
            name = str(row.get("name") or voice_id)
            if row.get("isVip"):
                name += " · PREMIUM"
            result.append((voice_id, name))
        return result

    def _verify_key(self) -> dict:
        """Validate the key through xKiro's free, non-metered usage endpoint."""
        import httpx
        with httpx.Client(timeout=10.0) as client:
            response = client.get(f"{API}/usage", headers=self._headers())
            if response.status_code >= 400:
                raise RuntimeError(_http_message(response.status_code, response.text))
            payload = response.json()
        if not isinstance(payload, dict) or payload.get("object") != "usage":
            raise RuntimeError("xKiro returned an unexpected usage response.")
        return payload

    def probe(self) -> tuple[bool, str]:
        if not self.key:
            return False, "Add an xKiro API key or save one in the xKiro LLM provider."
        try:
            choices = self.voices()
            usage = self._verify_key()
        except Exception as exc:
            return False, f"xKiro check failed: {type(exc).__name__}: {str(exc)[:120]}"
        selected = next((name for ident, name in choices if ident == self.voice_id), "")
        if not selected:
            return False, f"Voice {self.voice_id!r} was not found. Press LIST to choose one."
        plan = str(usage.get("plan") or "pay-as-you-go")
        return True, (
            f"Key accepted · plan: {plan} · voice: {selected}. "
            "Speech is billed per character; your daily character allowance and any wallet charges are controlled by xKiro."
        )


class _SpeechHTTPError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(str(status))
        self.status = status
        self.body = body


def _http_message(status: int, body: str) -> str:
    try:
        payload = json.loads(body)
        error = payload.get("error", payload)
        if isinstance(error, dict):
            message = error.get("message") or error.get("code") or error
        else:
            message = error
        detail = str(message)
    except Exception:
        detail = " ".join((body or "").split())[:180]
    if status == 401:
        return f"xKiro rejected the API key (401): {detail}"
    if status == 402:
        return f"xKiro reports that the account has no eligible plan or balance (402): {detail}"
    if status == 429:
        return f"xKiro speech limit reached (429). Wait for the limit to reset: {detail}"
    return f"xKiro speech request failed ({status}): {detail}"


def _make(cfg: dict) -> XKiroTTS:
    return XKiroTTS(cfg)


PROVIDER = {
    "id": "xkiro_tts",
    "kind": "tts",
    "label": "xKiro Voice",
    "hint": "Cloud speech · includes a Mexican Spanish voice · per-character billing",
    "requires": ("httpx",),
    "catalog": (
        {"id": "xkiro", "label": "xKiro · 145 voices",
         "note": "cloud · per character · daily allowance varies by plan",
         "local": False, "values": {}},
    ),
    "needs": (
        Field("api_key", "xKiro API key (optional)", kind="secret",
              required=False,
              hint="Leave blank to reuse the key saved for xKiro LLM, or enter a separate xKiro key."),
        Field("voice_id", "Voice", kind="choice", choices=(),
              default=DEFAULT_VOICE,
              hint="Press LIST to load the public voice catalogue; Mexican Female is the default."),
        Field("model", "Speech model", kind="text", default=DEFAULT_MODEL,
              required=False, advanced=True,
              hint="xKiro speech model ID."),
        Field("speed", "Speed", kind="number", default=1.0,
              required=False, advanced=True, hint="0.25 to 4.0; 1.0 is natural rate."),
    ),
    "caps": {"audio_out": True},
    "factory": _make,
}


def available() -> bool:
    return find_spec("httpx") is not None
