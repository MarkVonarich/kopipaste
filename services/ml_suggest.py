from __future__ import annotations

from typing import List, Dict, Tuple

from db.queries import get_local_alias, get_global_alias, get_user_top_categories
from services.ml_bias import apply_user_bias
from services.ml_infer import model_is_fresh, predict_top2


def _pack(cat1: str, cat2: str, s1: float = 0.6, s2: float = 0.4) -> List[Dict]:
    if not cat1 and not cat2:
        return []
    if not cat2:
        cat2 = 'Другое'
    return [
        {'cat': cat1, 'score': float(s1)},
        {'cat': cat2, 'score': float(s2)},
    ]


def _baseline_top2(user_id: int, normalized_text: str, detected_type: str) -> Tuple[List[Dict], str]:
    local = get_local_alias(user_id, normalized_text)
    if local:
        typ, cat = local
        if typ == detected_type and cat:
            return _pack(cat, 'Другое', 0.8, 0.2), 'local_alias'

    glob = get_global_alias(normalized_text)
    if glob:
        typ, cat = glob
        if typ == detected_type and cat:
            return _pack(cat, 'Другое', 0.7, 0.3), 'global_alias'

    top = get_user_top_categories(user_id=user_id, op_type=detected_type, lookback_ops=50)
    if len(top) >= 2:
        return _pack(top[0], top[1], 0.6, 0.4), 'user_frequency_prior'
    if len(top) == 1:
        return _pack(top[0], 'Другое', 0.6, 0.4), 'user_frequency_prior'

    return [], 'fallback'


def get_top2_suggestions(user_id: int, normalized_text: str, detected_type: str) -> Tuple[List[Dict], Dict]:
    source = 'baseline'
    model_meta: Dict = {}
    try:
        if model_is_fresh(max_age_days=7):
            model_top2, model_meta = predict_top2(normalized_text)
            if len(model_top2) >= 2:
                source = 'model'
                biased, bias_meta = apply_user_bias(user_id, normalized_text, model_top2)
                return biased, {
                    'reason': 'model_predict',
                    'source': source,
                    'stage': '2.3',
                    'model_version': model_meta.get('model_version'),
                    'trained_at': model_meta.get('trained_at'),
                    'bias': bias_meta,
                }
    except Exception:
        source = 'baseline'

    top2, baseline_reason = _baseline_top2(user_id, normalized_text, detected_type)
    biased, bias_meta = apply_user_bias(user_id, normalized_text, top2)
    return biased, {
        'reason': baseline_reason,
        'source': source,
        'stage': '2.3',
        'model_version': model_meta.get('model_version'),
        'trained_at': model_meta.get('trained_at'),
        'bias': bias_meta,
    }
