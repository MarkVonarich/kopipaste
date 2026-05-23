from __future__ import annotations

from typing import List, Dict, Tuple

from db.queries import get_personal_category_suggestion, get_global_category_suggestion, get_user_top_categories
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
    personal = get_personal_category_suggestion(user_id, normalized_text)
    if personal and personal.get('type') == detected_type and personal.get('category'):
        if personal.get('reason') == 'personal_exact':
            return _pack(personal['category'], 'Другое', 0.92, 0.08), 'personal_exact'
        return _pack(personal['category'], 'Другое', 0.82, 0.18), 'personal_fuzzy'

    glob = get_global_category_suggestion(normalized_text, detected_type)
    if glob and glob.get('category') and glob.get('level') in ('high', 'medium'):
        s1 = max(0.6, float(glob.get('confidence', 0.6)))
        s2 = max(0.01, 1.0 - s1)
        return _pack(glob['category'], 'Другое', s1, s2), f"global_{glob.get('level')}"

    top = get_user_top_categories(user_id=user_id, op_type=detected_type, lookback_ops=50)
    if len(top) >= 2:
        return _pack(top[0], top[1], 0.6, 0.4), 'user_frequency_prior'
    if len(top) == 1:
        return _pack(top[0], 'Другое', 0.6, 0.4), 'user_frequency_prior'

    return [], 'fallback'


def get_top2_suggestions(user_id: int, normalized_text: str, detected_type: str) -> Tuple[List[Dict], Dict]:
    top2, baseline_reason = _baseline_top2(user_id, normalized_text, detected_type)
    if baseline_reason in ('personal_exact', 'personal_fuzzy', 'global_high'):
        biased, bias_meta = apply_user_bias(user_id, normalized_text, top2)
        return biased, {
            'reason': baseline_reason,
            'source': 'baseline',
            'stage': '2.5.4',
            'bias': bias_meta,
        }

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
                    'stage': '2.5.4',
                    'model_version': model_meta.get('model_version'),
                    'trained_at': model_meta.get('trained_at'),
                    'bias': bias_meta,
                }
    except Exception:
        source = 'baseline'

    biased, bias_meta = apply_user_bias(user_id, normalized_text, top2)
    return biased, {
        'reason': baseline_reason,
        'source': source,
        'stage': '2.3',
        'model_version': model_meta.get('model_version'),
        'trained_at': model_meta.get('trained_at'),
        'bias': bias_meta,
    }
