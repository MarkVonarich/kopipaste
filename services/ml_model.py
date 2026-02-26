from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone

ARTIFACTS_DIR = Path('_ml_artifacts')
VECTORIZER_PATH = ARTIFACTS_DIR / 'vectorizer.pkl'
MODEL_PATH = ARTIFACTS_DIR / 'model.pkl'
LABELS_PATH = ARTIFACTS_DIR / 'label_encoder.pkl'
META_PATH = ARTIFACTS_DIR / 'meta.json'


def ensure_artifacts_dir():
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)


def save_meta(meta: dict):
    ensure_artifacts_dir()
    META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2))


def load_meta() -> dict:
    if not META_PATH.exists():
        return {}
    try:
        return json.loads(META_PATH.read_text())
    except Exception:
        return {}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
