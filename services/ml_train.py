from __future__ import annotations

import time
from typing import Dict

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder

from db.queries import get_ml_training_rows
from services.ml_model import (
    ensure_artifacts_dir,
    VECTORIZER_PATH,
    MODEL_PATH,
    LABELS_PATH,
    save_meta,
    now_iso,
)


def _topk_acc(y_true, probas, k: int = 2) -> float:
    ok = 0
    for i, y in enumerate(y_true):
        topk = probas[i].argsort()[::-1][:k]
        if int(y) in set(int(x) for x in topk):
            ok += 1
    return ok / len(y_true) if len(y_true) else 0.0


def train_model(days: int = 180, op_type: str = 'Расходы', limit: int = 20000) -> Dict:
    t0 = time.time()
    rows = get_ml_training_rows(days=days, op_type=op_type, limit=limit)
    samples = [(r[0], r[1]) for r in rows if r and r[0] and r[1]]
    if len(samples) < 30:
        return {'ok': False, 'error': 'not_enough_samples', 'samples': len(samples)}

    X_text = [x for x, _ in samples]
    y_text = [y for _, y in samples]

    le = LabelEncoder()
    y = le.fit_transform(y_text)

    X_train, X_test, y_train, y_test = train_test_split(
        X_text, y, test_size=0.2, random_state=42, stratify=y
    )

    vec = TfidfVectorizer(ngram_range=(1, 2), min_df=2, max_features=30000)
    Xtr = vec.fit_transform(X_train)
    Xte = vec.transform(X_test)

    clf = LogisticRegression(max_iter=1000, n_jobs=1, multi_class='auto')
    clf.fit(Xtr, y_train)

    prob = clf.predict_proba(Xte)
    pred = prob.argmax(axis=1)
    top1 = float((pred == y_test).mean()) if len(y_test) else 0.0
    top2 = float(_topk_acc(y_test, prob, k=2))

    ensure_artifacts_dir()
    joblib.dump(vec, VECTORIZER_PATH)
    joblib.dump(clf, MODEL_PATH)
    joblib.dump(le, LABELS_PATH)

    meta = {
        'model_version': 'tfidf_logreg_v2',
        'trained_at': now_iso(),
        'samples_total': len(samples),
        'classes': [str(c) for c in list(le.classes_)],
        'holdout_top1': round(top1, 4),
        'holdout_top2': round(top2, 4),
        'op_type': op_type,
        'days': int(days),
        'train_sec': round(time.time() - t0, 2),
    }
    save_meta(meta)
    return {'ok': True, **meta}
