from __future__ import annotations

import json
import logging
from typing import Any

from services.analytics_privacy import sanitize_properties


ALLOWED_FIELDS = {
    "event",
    "component",
    "status",
    "duration_ms",
    "error_code",
    "correlation_id",
    "analytics_user_id",
    "workspace_id",
}


def safe_log_fields(**fields: Any) -> dict[str, Any]:
    scoped = {k: v for k, v in fields.items() if k in ALLOWED_FIELDS}
    return sanitize_properties(scoped, max_json_bytes=2048)


def log_structured(logger: logging.Logger, level: int, message: str, **fields: Any) -> None:
    logger.log(level, "%s %s", message, json.dumps(safe_log_fields(**fields), ensure_ascii=False, sort_keys=True))
