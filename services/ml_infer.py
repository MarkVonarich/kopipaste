from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import List, Dict, Tuple

import joblib

from services.ml_model import VECTORIZER_PATH, MODEL_PATH, LABELS_PATH, load_meta


_CACHE = {'vec': None, 'model': None, 'le': None, 'meta': None}


def _load_artifacts():
    if _CACHE['vec'] is not None:
        return _CACHE['vec'], _CACHE['model'], _CACHE['le'], _CACHE['meta']
    if not (VECTORIZER_PATH.exists() and MODEL_PATH.exists() and LABELS_PATH.exists()):
        return None, None, None, {}
    _CACHE['vec'] = joblib.load(VECTORIZER_PATH)
    _CACHE['model'] = joblib.load(MODEL_PATH)
    _CACHE['le'] = joblib.load(LABELS_PATH)
    _CACHE['meta'] = load_meta()
    return _CACHE['vec'], _CACHE['model'], _CACHE['le'], _CACHE['meta']


def model_is_fresh(max_age_days: int = 7) -> bool:
    meta = load_meta()
    ts = meta.get('trained_at')
    if not ts:
        return False
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - dt <= timedelta(days=max_age_days)
    except Exception:
        return False


def predict_top2(normalized_text: str) -> Tuple[List[Dict], Dict]:
    vec, model, le, meta = _load_artifacts()
    if vec is None or model is None or le is None:
        return [], {'reason': 'artifacts_missing'}
    X = vec.transform([normalized_text])
    probs = model.predict_proba(X)[0]
    order = probs.argsort()[::-1][:2]
    out = []
    for idx in order:
        out.append({'cat': str(le.inverse_transform([idx])[0]), 'score': round(float(probs[idx]), 4)})
    return out, {
        'reason': 'model_predict',
        'model_version': meta.get('model_version', 'tfidf_logreg_v2'),
        'trained_at': meta.get('trained_at'),
    }
