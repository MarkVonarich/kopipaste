from __future__ import annotations

from typing import List, Dict

from db.queries import get_local_alias, get_global_alias, get_user_top_categories


def _pack(cat1: str, cat2: str, s1: float = 0.6, s2: float = 0.4) -> List[Dict]:
    if not cat1 and not cat2:
        return []
    if not cat2:
        cat2 = 'Другое'
    return [
        {'cat': cat1, 'score': float(s1)},
        {'cat': cat2, 'score': float(s2)},
    ]


def get_top2_suggestions(user_id: int, normalized_text: str, detected_type: str) -> List[Dict]:
    """Baseline suggester v1:
    1) user/local alias match
    2) global alias match
    3) user recent frequency prior (top categories for detected_type)
    """
    # 1) локальный alias
    local = get_local_alias(user_id, normalized_text)
    if local:
        typ, cat = local
        if typ == detected_type and cat:
            return _pack(cat, 'Другое', 0.8, 0.2)

    # 2) глобальный alias
    glob = get_global_alias(normalized_text)
    if glob:
        typ, cat = glob
        if typ == detected_type and cat:
            return _pack(cat, 'Другое', 0.7, 0.3)

    # 3) частотный prior по пользователю
    top = get_user_top_categories(user_id=user_id, op_type=detected_type, lookback_ops=50)
    if len(top) >= 2:
        return _pack(top[0], top[1], 0.6, 0.4)
    if len(top) == 1:
        return _pack(top[0], 'Другое', 0.6, 0.4)

    return []
