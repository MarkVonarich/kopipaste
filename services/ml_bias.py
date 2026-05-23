from __future__ import annotations

from typing import List, Dict, Tuple

from db.queries import get_recent_choices_for_text


def apply_user_bias(user_id: int, normalized_text: str, top2: List[Dict]) -> Tuple[List[Dict], Dict]:
    """Boost scores by user's recent choices for the same/close normalized text."""
    if not top2:
        return top2, {'reason': 'empty_top2', 'stage': '2.2', 'bias_applied': False}

    recent = get_recent_choices_for_text(user_id=user_id, normalized_text=normalized_text, days=90, limit=200)
    if not recent:
        return top2, {'reason': 'no_recent_choice_match', 'stage': '2.2', 'bias_applied': False}

    counts = {cat: cnt for cat, cnt in recent}
    total = max(1, sum(counts.values()))

    reweighted = []
    for item in top2:
        cat = item.get('cat')
        base = float(item.get('score') or 0.0)
        boost = 0.35 * (counts.get(cat, 0) / total)
        reweighted.append({'cat': cat, 'score': base + boost})

    # normalize to sum=1 and keep order by score
    score_sum = sum(max(0.0, float(x.get('score') or 0.0)) for x in reweighted) or 1.0
    out = []
    for x in reweighted:
        out.append({'cat': x['cat'], 'score': round(max(0.0, x['score']) / score_sum, 4)})
    out.sort(key=lambda x: x['score'], reverse=True)

    return out[:2], {
        'reason': 'user_recent_choice_bias',
        'stage': '2.2',
        'bias_applied': True,
        'choices_seen': recent[:5],
    }
