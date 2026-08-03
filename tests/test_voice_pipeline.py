from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace


class _TelegramFile:
    def __init__(self, data: bytes):
        self.data = data

    async def download_to_drive(self, custom_path):
        Path(custom_path).write_bytes(self.data)


class _Voice:
    def __init__(self, data: bytes = b"ogg-opus-bytes", duration: int = 2, file_size: int | None = None):
        self.duration = duration
        self.file_size = len(data) if file_size is None else file_size
        self._data = data

    async def get_file(self):
        return _TelegramFile(self._data)


class _Message:
    def __init__(self, voice=None):
        self.voice = voice
        self.audio = None
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


def _update(message):
    return SimpleNamespace(
        message=message,
        effective_message=message,
        effective_chat=SimpleNamespace(id=55, type="private"),
        effective_user=SimpleNamespace(id=55),
    )


def test_voice_config_classifies_disabled_and_missing_credentials(monkeypatch):
    from services import voice_transcription as voice

    monkeypatch.setattr(voice, "VOICE_INPUT_ENABLED", False)
    assert voice.validate_voice_config() == "voice_disabled"

    monkeypatch.setattr(voice, "VOICE_INPUT_ENABLED", True)
    monkeypatch.setattr(voice, "VOICE_TRANSCRIBE_PROVIDER", "openai")
    monkeypatch.setattr(voice, "provider_api_key", lambda: "")
    assert voice.validate_voice_config() == "voice_credentials_missing"


def test_telegram_voice_download_success_and_empty_file(tmp_path):
    from services.voice_transcription import VoicePipelineError, download_telegram_voice

    dest = tmp_path / "voice.ogg"
    asyncio.run(download_telegram_voice(_Voice(b"abc"), dest))
    assert dest.read_bytes() == b"abc"

    try:
        asyncio.run(download_telegram_voice(_Voice(b""), tmp_path / "empty.ogg"))
    except VoicePipelineError as exc:
        assert exc.reason == "voice_empty_file"
    else:
        raise AssertionError("empty voice did not fail")


def test_openai_ogg_path_is_used_directly(monkeypatch):
    from services import voice_transcription as voice

    seen = {}
    monkeypatch.setattr(voice, "VOICE_INPUT_ENABLED", True)
    monkeypatch.setattr(voice, "VOICE_TRANSCRIBE_PROVIDER", "openai")
    monkeypatch.setattr(voice, "VOICE_TRANSCRIBE_MODEL", "test-model")
    monkeypatch.setattr(voice, "provider_api_key", lambda: "secret")
    monkeypatch.setattr(voice, "voice_config_status", lambda: SimpleNamespace(enabled=True, provider="openai", model="test-model", credential_present=True, dependency_available=True))

    def _transcribe(path, *, api_key, model, language):
        seen["suffix"] = Path(path).suffix
        seen["api_key"] = api_key
        seen["model"] = model
        seen["language"] = language
        return "Магнит пятьсот семьдесят"

    monkeypatch.setattr(voice, "transcribe_with_openai", _transcribe)
    result = asyncio.run(voice.transcribe_telegram_voice(_Voice()))
    assert result.ok
    assert result.normalized_text == "магнит 570"
    assert result.language == "ru"
    assert seen == {"suffix": ".ogg", "api_key": "secret", "model": "test-model", "language": "ru"}


def test_wav_conversion_path_and_ffmpeg_missing(monkeypatch):
    from services import voice_transcription as voice

    converted = []
    original_convert = voice.convert_audio_to_wav
    monkeypatch.setattr(voice, "VOICE_INPUT_ENABLED", True)
    monkeypatch.setattr(voice, "VOICE_TRANSCRIBE_PROVIDER", "openai")
    monkeypatch.setattr(voice, "provider_api_key", lambda: "secret")
    monkeypatch.setattr(voice, "voice_config_status", lambda: SimpleNamespace(enabled=True, provider="openai", model="test-model", credential_present=True, dependency_available=True))
    monkeypatch.setattr(voice, "convert_audio_to_wav", lambda src, dst: converted.append((src.suffix, dst.suffix)) or dst.write_bytes(b"wav"))
    monkeypatch.setattr(voice, "transcribe_with_openai", lambda path, **_kwargs: "Дикси тысяча")

    result = asyncio.run(voice.transcribe_telegram_voice(_Voice(), force_wav_conversion=True))
    assert result.ok
    assert result.normalized_text == "дикси 1000"
    assert converted == [(".ogg", ".wav")]

    monkeypatch.setattr(voice.shutil, "which", lambda _name: None)
    try:
        original_convert(Path("/tmp/in.oga"), Path("/tmp/out.wav"))
    except voice.VoicePipelineError as exc:
        assert exc.reason == "voice_conversion_unavailable"
    else:
        raise AssertionError("missing ffmpeg did not fail")


def test_provider_failures_are_classified(monkeypatch):
    from services import voice_transcription as voice

    monkeypatch.setattr(voice, "VOICE_INPUT_ENABLED", True)
    monkeypatch.setattr(voice, "VOICE_TRANSCRIBE_PROVIDER", "openai")
    monkeypatch.setattr(voice, "provider_api_key", lambda: "secret")
    monkeypatch.setattr(voice, "voice_config_status", lambda: SimpleNamespace(enabled=True, provider="openai", model="test-model", credential_present=True, dependency_available=True))

    def _raise_timeout(*_args, **_kwargs):
        raise voice.VoicePipelineError("voice_provider_timeout")

    monkeypatch.setattr(voice, "transcribe_with_openai", _raise_timeout)
    result = asyncio.run(voice.transcribe_telegram_voice(_Voice()))
    assert not result.ok
    assert result.reason == "voice_provider_timeout"


def test_unsupported_ogg_format_retries_once_as_wav(monkeypatch):
    from services import voice_transcription as voice

    calls = []
    converted = []
    monkeypatch.setattr(voice, "VOICE_INPUT_ENABLED", True)
    monkeypatch.setattr(voice, "VOICE_TRANSCRIBE_PROVIDER", "openai")
    monkeypatch.setattr(voice, "provider_api_key", lambda: "secret")
    monkeypatch.setattr(voice, "voice_config_status", lambda: SimpleNamespace(enabled=True, provider="openai", model="test-model", credential_present=True, dependency_available=True))
    monkeypatch.setattr(voice, "convert_audio_to_wav", lambda src, dst: converted.append((src.suffix, dst.suffix)) or dst.write_bytes(b"wav"))

    def _transcribe(path, **_kwargs):
        calls.append(Path(path).suffix)
        if Path(path).suffix == ".ogg":
            raise voice.VoicePipelineError("voice_provider_unsupported_format", status_code=400, provider_code="unsupported_value", file_suffix=".ogg")
        return "Чижик двести шестнадцать рублей тридцать четыре копейки"

    monkeypatch.setattr(voice, "transcribe_with_openai", _transcribe)
    result = asyncio.run(voice.transcribe_telegram_voice(_Voice()))
    assert result.ok
    assert result.normalized_text == "чижик 216.34"
    assert calls == [".ogg", ".wav"]
    assert converted == [(".ogg", ".wav")]


def test_auth_and_rate_limit_do_not_trigger_wav_fallback(monkeypatch):
    from services import voice_transcription as voice

    for reason in ("voice_provider_auth_failed", "voice_provider_rate_limited"):
        converted = []
        monkeypatch.setattr(voice, "VOICE_INPUT_ENABLED", True)
        monkeypatch.setattr(voice, "VOICE_TRANSCRIBE_PROVIDER", "openai")
        monkeypatch.setattr(voice, "provider_api_key", lambda: "secret")
        monkeypatch.setattr(voice, "voice_config_status", lambda: SimpleNamespace(enabled=True, provider="openai", model="test-model", credential_present=True, dependency_available=True))
        monkeypatch.setattr(voice, "convert_audio_to_wav", lambda src, dst: converted.append((src, dst)))
        monkeypatch.setattr(voice, "transcribe_with_openai", lambda *_args, **_kwargs: (_ for _ in ()).throw(voice.VoicePipelineError(reason)))

        result = asyncio.run(voice.transcribe_telegram_voice(_Voice()))
        assert not result.ok
        assert result.reason == reason
        assert converted == []


def test_handle_voice_uses_existing_text_parser_path(monkeypatch):
    from routers import messages
    from services.voice_transcription import VoiceTranscriptionResult

    calls = []
    usage = []

    async def _transcribe(_media):
        return VoiceTranscriptionResult(True, None, "Магнит пятьсот семьдесят", "магнит 570", True, "ru")

    monkeypatch.setattr(messages, "transcribe_telegram_voice", _transcribe)
    monkeypatch.setattr(messages, "parse_user_input", lambda text: ("магнит", 570, SimpleNamespace(), None))

    async def _process(update, context, text):
        calls.append((text, context.user_data.get("operation_source")))

    monkeypatch.setattr(messages, "_process_free_text", _process)
    monkeypatch.setattr(messages, "track_api_usage", lambda ev: usage.append(ev))
    context = SimpleNamespace(user_data={})
    msg = _Message(_Voice())
    asyncio.run(messages.handle_voice(_update(msg), context))

    assert calls == [("магнит 570", "voice")]
    assert usage[0].status == "success"
    assert "transcript" not in usage[0].metadata


def test_handle_voice_parse_failure_is_specific(monkeypatch):
    from routers import messages
    from services.voice_transcription import VoiceTranscriptionResult

    async def _transcribe(_media):
        return VoiceTranscriptionResult(True, None, "что-то непонятное", "что-то непонятное", False, "unknown")

    monkeypatch.setattr(messages, "transcribe_telegram_voice", _transcribe)
    monkeypatch.setattr(messages, "parse_user_input", lambda _text: (_ for _ in ()).throw(ValueError("no_amount")))
    monkeypatch.setattr(messages, "track_api_usage", lambda _ev: None)

    msg = _Message(_Voice())
    asyncio.run(messages.handle_voice(_update(msg), SimpleNamespace(user_data={})))
    assert "Я услышал" in msg.replies[-1][0]
    assert "Не удалось определить сумму или категорию" in msg.replies[-1][0]


def test_handle_voice_disabled_message(monkeypatch):
    from routers import messages
    from services.voice_transcription import VoiceTranscriptionResult

    async def _transcribe(_media):
        return VoiceTranscriptionResult(False, "voice_disabled")

    monkeypatch.setattr(messages, "transcribe_telegram_voice", _transcribe)
    monkeypatch.setattr(messages, "track_api_usage", lambda _ev: None)

    msg = _Message(_Voice())
    asyncio.run(messages.handle_voice(_update(msg), SimpleNamespace(user_data={})))
    assert msg.replies[-1][0] == "Голосовой ввод сейчас выключен в настройках бота."
