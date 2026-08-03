# Voice Input Diagnostics

Voice input is optional. Bot startup must not fail solely because voice recognition is unavailable.

## Environment Variables

- `VOICE_INPUT_ENABLED`
- `VOICE_TRANSCRIBE_PROVIDER`
- `VOICE_TRANSCRIBE_MODEL`
- `VOICE_MAX_SECONDS`
- `OPENAI_API_KEY`
- `RECEIPT_OCR_API_KEY`

`OPENAI_API_KEY` is preferred when present. `RECEIPT_OCR_API_KEY` is a fallback credential already used by OCR code. Never print their values.

## Safe Production Checks

List loaded systemd environment variable names without values:

```bash
systemctl show finuchet.service -p Environment --no-pager \
  | tr ' ' '\n' \
  | sed 's/=.*//' \
  | grep -E '^(Environment=)?(VOICE_|OPENAI_API_KEY|RECEIPT_OCR_API_KEY)'
```

Check audio tooling:

```bash
ffmpeg -version
ffprobe -version
```

Run import diagnostics without provider or Telegram calls:

```bash
.venv/bin/python - <<'PY'
from services.voice_transcription import voice_config_status
status = voice_config_status()
print({
    "enabled": status.enabled,
    "provider": status.provider,
    "model": status.model,
    "credential_present": status.credential_present,
    "dependency_available": status.dependency_available,
    "ffmpeg_available": status.ffmpeg_available,
    "ffprobe_available": status.ffprobe_available,
    "max_duration_seconds": status.max_duration_seconds,
})
PY
```

Run the local mocked voice pipeline tests:

```bash
.venv/bin/python -m pytest -q tests/test_voice_pipeline.py
```

Check recent aggregate voice API failure reason codes without transcripts:

```sql
SELECT error_code, COUNT(*)
FROM analytics.api_usage_events
WHERE feature='voice_transcription'
  AND status='failed'
  AND occurred_at >= now() - interval '7 days'
GROUP BY error_code
ORDER BY COUNT(*) DESC, error_code;
```

## Privacy

Logs and analytics must not include provider credentials, Telegram tokens, full transcripts, raw audio bytes, raw user text, or exact financial amounts.
