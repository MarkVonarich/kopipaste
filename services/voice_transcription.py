from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any

from settings import VOICE_INPUT_ENABLED, VOICE_MAX_SECONDS, VOICE_TRANSCRIBE_MODEL, VOICE_TRANSCRIBE_PROVIDER
from utils.spoken_numbers import normalize_spoken_money

log = logging.getLogger(__name__)

MAX_VOICE_BYTES = 20 * 1024 * 1024
FFMPEG_TIMEOUT_SECONDS = 20
TECHNICAL_REASONS = {
    "voice_provider_not_configured",
    "voice_credentials_missing",
    "voice_download_failed",
    "voice_empty_file",
    "voice_conversion_unavailable",
    "voice_conversion_failed",
    "voice_provider_timeout",
    "voice_provider_auth_failed",
    "voice_provider_insufficient_quota",
    "voice_provider_rate_limited",
    "voice_provider_unsupported_format",
    "voice_provider_request_failed",
}


@dataclass(frozen=True)
class VoiceConfigStatus:
    enabled: bool
    provider: str
    model: str
    credential_present: bool
    dependency_available: bool
    ffmpeg_available: bool
    ffprobe_available: bool
    max_duration_seconds: int


@dataclass(frozen=True)
class VoiceTranscriptionResult:
    ok: bool
    reason: str | None
    transcript: str = ""
    normalized_text: str = ""
    normalized_changed: bool = False
    language: str = "unknown"
    provider: str = VOICE_TRANSCRIBE_PROVIDER
    model: str = VOICE_TRANSCRIBE_MODEL
    latency_ms: int | None = None


class VoicePipelineError(Exception):
    def __init__(
        self,
        reason: str,
        *,
        status_code: int | None = None,
        provider_code: str | None = None,
        request_id: str | None = None,
        file_suffix: str | None = None,
        fallback_attempted: bool = False,
    ):
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code
        self.provider_code = provider_code
        self.request_id = request_id
        self.file_suffix = file_suffix
        self.fallback_attempted = fallback_attempted


def provider_api_key() -> str:
    return (os.getenv("OPENAI_API_KEY") or os.getenv("RECEIPT_OCR_API_KEY") or "").strip()


def provider_supports_ogg(provider: str) -> bool:
    return (provider or "").strip().lower() == "openai"


def voice_config_status() -> VoiceConfigStatus:
    dependency_available = True
    if VOICE_TRANSCRIBE_PROVIDER == "openai":
        try:
            import openai  # noqa: F401
        except Exception:
            dependency_available = False
    return VoiceConfigStatus(
        enabled=bool(VOICE_INPUT_ENABLED),
        provider=VOICE_TRANSCRIBE_PROVIDER,
        model=VOICE_TRANSCRIBE_MODEL,
        credential_present=bool(provider_api_key()),
        dependency_available=dependency_available,
        ffmpeg_available=bool(shutil.which("ffmpeg")),
        ffprobe_available=bool(shutil.which("ffprobe")),
        max_duration_seconds=int(VOICE_MAX_SECONDS),
    )


def validate_voice_config() -> str | None:
    status = voice_config_status()
    if not status.enabled:
        return "voice_disabled"
    if status.provider != "openai":
        return "voice_provider_not_configured"
    if not status.credential_present:
        return "voice_credentials_missing"
    if not status.dependency_available:
        return "voice_provider_not_configured"
    return None


def validate_media(media: Any) -> str | None:
    if media is None:
        return "voice_download_failed"
    duration = int(getattr(media, "duration", 0) or 0)
    if duration > int(VOICE_MAX_SECONDS):
        return "voice_too_long"
    file_size = int(getattr(media, "file_size", 0) or 0)
    if file_size > MAX_VOICE_BYTES:
        return "voice_too_large"
    return None


async def download_telegram_voice(media: Any, destination: Path) -> None:
    try:
        tg_file = await media.get_file()
        if hasattr(tg_file, "download_to_drive"):
            await tg_file.download_to_drive(custom_path=str(destination))
        else:
            raise AttributeError("download_to_drive")
    except Exception as exc:
        log.info("voice_download_failed reason=%s", type(exc).__name__)
        raise VoicePipelineError("voice_download_failed") from exc
    if not destination.exists() or destination.stat().st_size <= 0:
        raise VoicePipelineError("voice_empty_file")
    if destination.stat().st_size > MAX_VOICE_BYTES:
        raise VoicePipelineError("voice_too_large")


def convert_audio_to_wav(src: Path, dst: Path, *, timeout: int = FFMPEG_TIMEOUT_SECONDS) -> None:
    if not shutil.which("ffmpeg"):
        raise VoicePipelineError("voice_conversion_unavailable")
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-ac", "1", "-ar", "16000", str(dst)],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise VoicePipelineError("voice_conversion_failed") from exc
    except Exception as exc:
        raise VoicePipelineError("voice_conversion_failed") from exc
    if proc.returncode != 0 or not dst.exists() or dst.stat().st_size <= 0:
        raise VoicePipelineError("voice_conversion_failed")


def classify_provider_exception(exc: Exception) -> str:
    name = type(exc).__name__.lower()
    status_code = getattr(exc, "status_code", None)
    code = getattr(exc, "code", None) or getattr(getattr(exc, "body", None), "code", None)
    msg = str(exc).lower()
    if "timeout" in name:
        return "voice_provider_timeout"
    if status_code in {401, 403} or "auth" in name or "permission" in name:
        return "voice_provider_auth_failed"
    if status_code == 429 and (code == "insufficient_quota" or "quota" in msg):
        return "voice_provider_insufficient_quota"
    if status_code == 429 or "rate limit" in msg or "rate_limited" in str(code).lower():
        return "voice_provider_rate_limited"
    if status_code == 400 and (
        "unsupported" in msg
        or "format" in msg
        or str(code).lower() in {"unsupported_value", "unsupported_format", "invalid_file_format"}
    ):
        return "voice_provider_unsupported_format"
    return "voice_provider_request_failed"


def transcribe_with_openai(audio_path: Path, *, api_key: str, model: str, language: str | None = "ru") -> str:
    try:
        from openai import OpenAI
    except Exception as exc:
        raise VoicePipelineError("voice_provider_not_configured") from exc
    try:
        client = OpenAI(api_key=api_key, timeout=30)
        with audio_path.open("rb") as audio_file:
            kwargs = {"model": model, "file": audio_file}
            if language:
                kwargs["language"] = language
            result = client.audio.transcriptions.create(**kwargs)
        return (getattr(result, "text", None) or "").strip()
    except VoicePipelineError:
        raise
    except Exception as exc:
        raise VoicePipelineError(
            classify_provider_exception(exc),
            status_code=getattr(exc, "status_code", None),
            provider_code=getattr(exc, "code", None) or getattr(getattr(exc, "body", None), "code", None),
            request_id=getattr(exc, "request_id", None),
            file_suffix=audio_path.suffix,
        ) from exc


async def transcribe_telegram_voice(media: Any, *, force_wav_conversion: bool = False) -> VoiceTranscriptionResult:
    started = monotonic()
    config_error = validate_voice_config()
    if config_error:
        return VoiceTranscriptionResult(False, config_error)
    media_error = validate_media(media)
    if media_error:
        return VoiceTranscriptionResult(False, media_error)

    try:
        with tempfile.TemporaryDirectory(prefix="fin_voice_") as tmpdir:
            src = Path(tmpdir) / "voice.ogg"
            wav = Path(tmpdir) / "voice.wav"
            await download_telegram_voice(media, src)
            audio_path = src
            if force_wav_conversion or not provider_supports_ogg(VOICE_TRANSCRIBE_PROVIDER):
                convert_audio_to_wav(src, wav)
                audio_path = wav
            try:
                transcript = transcribe_with_openai(audio_path, api_key=provider_api_key(), model=VOICE_TRANSCRIBE_MODEL, language="ru")
            except VoicePipelineError as exc:
                fallback_allowed = (
                    exc.reason == "voice_provider_unsupported_format"
                    and audio_path == src
                    and not force_wav_conversion
                )
                log.info(
                    "voice_provider_failed reason=%s status=%s code=%s request_id_present=%s suffix=%s fallback_allowed=%s",
                    exc.reason,
                    exc.status_code,
                    exc.provider_code,
                    bool(exc.request_id),
                    audio_path.suffix,
                    fallback_allowed,
                )
                if not fallback_allowed:
                    raise
                convert_audio_to_wav(src, wav)
                try:
                    transcript = transcribe_with_openai(wav, api_key=provider_api_key(), model=VOICE_TRANSCRIBE_MODEL, language="ru")
                except VoicePipelineError as retry_exc:
                    log.info(
                        "voice_provider_failed reason=%s status=%s code=%s request_id_present=%s suffix=%s fallback_attempted=%s",
                        retry_exc.reason,
                        retry_exc.status_code,
                        retry_exc.provider_code,
                        bool(retry_exc.request_id),
                        wav.suffix,
                        True,
                    )
                    raise retry_exc
    except VoicePipelineError as exc:
        return VoiceTranscriptionResult(False, exc.reason, latency_ms=int((monotonic() - started) * 1000))

    if not transcript:
        return VoiceTranscriptionResult(False, "voice_empty_transcript", latency_ms=int((monotonic() - started) * 1000))
    normalized, changed, language = normalize_spoken_money(transcript)
    return VoiceTranscriptionResult(
        True,
        None,
        transcript=transcript,
        normalized_text=normalized,
        normalized_changed=changed,
        language=language,
        latency_ms=int((monotonic() - started) * 1000),
    )


def user_message_for_voice_reason(reason: str) -> str:
    if reason == "voice_disabled":
        return "Голосовой ввод сейчас выключен в настройках бота."
    if reason == "voice_too_long":
        return f"Слишком длинное аудио. Максимум {VOICE_MAX_SECONDS} сек."
    if reason in TECHNICAL_REASONS or reason == "voice_too_large":
        return "Голосовой ввод временно недоступен. Попробуйте позже или напишите текстом."
    if reason == "voice_empty_transcript":
        return "Не расслышал голос. Попробуйте сказать короче: «Продукты 500»."
    return "Не смог распознать голос. Попробуйте ещё раз или напишите текстом."
