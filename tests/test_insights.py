from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from services.insights import (
    InsightEngine,
    InsightState,
    PostgresInsightStateStore,
    PeriodRef,
    assign_fingerprint,
    build_snapshot,
    detect_average_check_change,
    detect_candidates,
    detect_category_contribution,
    detect_frequency_change,
    detect_limit_pace,
    detect_merchant_contribution,
    detect_spending_change,
    group_candidates,
    rank_candidates,
)


NOW = datetime(2026, 8, 10, 12, tzinfo=timezone.utc)
PERIOD = PeriodRef("current_month", date(2026, 8, 1), date(2026, 8, 10))
PREVIOUS = PeriodRef("previous_month_to_date", date(2026, 7, 1), date(2026, 7, 10))


class MemoryStore:
    def __init__(self) -> None:
        self.values: dict[tuple[int, int | None, str], InsightState] = {}
        self.now = NOW

    def load(self, user_id: int, workspace_id: int | None) -> list[InsightState]:
        return [state for (owner, workspace, _fingerprint), state in self.values.items() if owner == user_id and workspace == workspace_id]

    def ensure(self, user_id: int, workspace_id: int | None, candidates) -> None:
        for candidate in candidates:
            key = (user_id, workspace_id, candidate.fingerprint)
            self.values.setdefault(key, InsightState(candidate.fingerprint, candidate.detector_type))

    def record_impression(self, user_id: int, workspace_id: int | None, fingerprint: str) -> InsightState | None:
        key = (user_id, workspace_id, fingerprint)
        state = self.values.get(key)
        if not state:
            return None
        if not state.first_shown_at or state.first_shown_at <= self.now - timedelta(hours=24):
            updated = replace(state, show_count=1, first_shown_at=self.now, last_shown_at=self.now)
        else:
            updated = replace(state, show_count=state.show_count + 1, last_shown_at=self.now)
        self.values[key] = updated
        return updated

    def record_feedback(self, user_id: int, workspace_id: int | None, fingerprint: str, feedback_type: str) -> InsightState | None:
        key = (user_id, workspace_id, fingerprint)
        state = self.values.get(key)
        if not state:
            return None
        updated = replace(
            state,
            feedback_type=feedback_type,
            suppression_until=NOW + timedelta(days=30) if feedback_type == "not_useful" else None,
        )
        self.values[key] = updated
        return updated


def snapshot(
    current_rows,
    previous_rows,
    *,
    currency: str = "RUB",
    user_id: int = 42,
    workspace_id: int = 10,
    limits=(),
    scope_category: str | None = None,
    can_write: bool = True,
):
    return build_snapshot(
        user_id=user_id,
        workspace_id=workspace_id,
        workspace_kind="group",
        currency=currency,
        period=PERIOD,
        comparison_period=PREVIOUS,
        current_rows=current_rows,
        previous_rows=previous_rows,
        limits=limits,
        scope_category=scope_category,
        can_write=can_write,
    )


def acceptance_snapshot(**kwargs):
    return snapshot(
        [
            ("Продукты", "Яндекс Лавка", "RUB", Decimal("8200"), 12),
            ("Продукты", "Другой магазин", "RUB", Decimal("10200"), 8),
        ],
        [
            ("Продукты", "Яндекс-Лавка", "RUB", Decimal("5400"), 7),
            ("Продукты", "Другой магазин", "RUB", Decimal("8900"), 8),
        ],
        **kwargs,
    )


def test_meaningful_overall_spending_change_detected():
    items = detect_spending_change(acceptance_snapshot())

    assert len(items) == 1
    assert items[0].absolute_delta == Decimal("4100.00")
    assert items[0].relative_delta == Decimal("0.2867")


def test_selected_category_scope_uses_scoped_wording_and_actions():
    value = snapshot(
        [("Рестораны", "Bistro", "RUB", Decimal("8400"), 8)],
        [("Рестораны", "Bistro", "RUB", Decimal("4400"), 5)],
        scope_category="Рестораны",
    )

    assert detect_category_contribution(value) == []
    result = InsightEngine(MemoryStore()).generate(value, today=date(2026, 8, 10), now=NOW)

    assert len(result) == 1
    assert result[0]["title"] == "Расходы на Рестораны выросли на 4 000 ₽"
    assert {item["kind"] for item in result[0]["evidence"]} >= {"amount_comparison", "merchant_contribution"}
    merchant_action = next(action for action in result[0]["actions"] if action["type"] == "OPEN_MERCHANT")
    assert merchant_action["params"]["category"] == "Рестораны"
    assert merchant_action["params"]["scope_category"] == "Рестораны"
    assert merchant_action["params"]["category_key"] == "рестораны"


def test_merchant_action_keeps_canonical_category_key_without_exact_display_scope():
    value = snapshot(
        [
            ("Прочее", "Shop", "RUB", Decimal("3000"), 3),
            (" прочее ", "Shop", "RUB", Decimal("3000"), 3),
            ("ПРОЧЕЕ", "Other", "RUB", Decimal("2400"), 3),
        ],
        [
            ("Прочее", "Shop", "RUB", Decimal("1000"), 3),
            (" прочее ", "Shop", "RUB", Decimal("1000"), 3),
            ("ПРОЧЕЕ", "Other", "RUB", Decimal("1400"), 3),
        ],
    )

    grouped = group_candidates(detect_candidates(value, today=date(2026, 8, 10)))
    merchant_action = next(action for action in grouped[0].actions if action.type == "OPEN_MERCHANT")

    assert merchant_action.params["category"] == "all"
    assert merchant_action.params["scope_category"] is None
    assert merchant_action.params["category_key"] == "прочее"
    assert merchant_action.params["target_category"] in {"Прочее", "прочее", "ПРОЧЕЕ"}


def test_tiny_high_percentage_change_is_suppressed():
    value = snapshot(
        [("Кофе", "Cafe", "RUB", Decimal("50"), 3)],
        [("Кофе", "Cafe", "RUB", Decimal("10"), 3)],
    )

    assert detect_spending_change(value) == []
    assert detect_candidates(value, today=date(2026, 8, 10)) == []


def test_large_change_is_eligible_and_zero_baseline_is_safe():
    large = snapshot(
        [("Продукты", "Shop", "RUB", Decimal("27000"), 12)],
        [("Продукты", "Shop", "RUB", Decimal("20000"), 10)],
    )
    zero = snapshot(
        [("Продукты", "Shop", "RUB", Decimal("27000"), 12)],
        [],
    )

    assert detect_spending_change(large)[0].absolute_delta == Decimal("7000.00")
    assert detect_spending_change(zero) == []


def test_category_and_merchant_contributions_reconcile():
    value = acceptance_snapshot()
    category = detect_category_contribution(value)[0]
    merchant = detect_merchant_contribution(value)[0]

    assert category.entity_key == "продукты"
    assert category.absolute_delta == Decimal("4100.00")
    assert merchant.entity_key == "яндекс лавка"
    assert merchant.absolute_delta == Decimal("2800.00")
    assert merchant.current_value == Decimal("8200.00")
    assert merchant.baseline_value == Decimal("5400.00")


def test_read_only_workspace_does_not_offer_create_limit_action():
    category = detect_category_contribution(acceptance_snapshot(can_write=False))[0]

    assert [action.type for action in category.actions] == ["OPEN_CATEGORY", "OPEN_OPERATIONS"]


def test_merchant_display_uses_current_scope_alias_like_merchant_detail():
    value = snapshot(
        [("Продукты", "яндекс лавка", "RUB", Decimal("8200"), 12)],
        [("Продукты", "Яндекс Лавка", "RUB", Decimal("5400"), 7)],
    )

    assert value.merchants[0].key == "яндекс лавка"
    assert value.merchants[0].name == "яндекс лавка"


def test_acceptance_narrative_groups_category_merchant_and_frequency():
    grouped = group_candidates(detect_candidates(acceptance_snapshot(), today=date(2026, 8, 10)))

    assert [item.detector_type for item in grouped] == ["category_contribution"]
    assert {item["kind"] for item in grouped[0].evidence} >= {
        "amount_comparison",
        "merchant_contribution",
        "count_comparison",
    }
    assert [action.type for action in grouped[0].actions] == ["OPEN_MERCHANT", "OPEN_OPERATIONS", "CREATE_LIMIT"]


def test_frequency_requires_meaningful_absolute_counts():
    meaningful = acceptance_snapshot()
    noisy = snapshot(
        [("Кофе", "Cafe", "RUB", Decimal("2000"), 2)],
        [("Кофе", "Cafe", "RUB", Decimal("1000"), 1)],
    )

    frequency = detect_frequency_change(meaningful)
    assert len(frequency) == 1
    assert frequency[0].content_data["current_count"] == 12
    assert frequency[0].content_data["previous_count"] == 7
    assert detect_frequency_change(noisy) == []


def test_average_check_change_uses_merchant_features():
    value = snapshot(
        [("Рестораны", "Bistro", "RUB", Decimal("7200"), 12)],
        [("Рестораны", "Bistro", "RUB", Decimal("4000"), 10)],
    )

    average = detect_average_check_change(value)[0]
    assert average.current_value == Decimal("600.00")
    assert average.baseline_value == Decimal("400.00")
    assert average.absolute_delta == Decimal("200.00")


def test_limit_pace_uses_elapsed_period_math_and_active_control():
    value = snapshot(
        [("Рестораны", "Bistro", "RUB", Decimal("8200"), 8)],
        [("Рестораны", "Bistro", "RUB", Decimal("6000"), 7)],
        limits=[{
            "id": "category:month:Рестораны",
            "title": "Рестораны",
            "category": "Рестораны",
            "amount": Decimal("10000"),
            "spent": Decimal("8200"),
            "currency": "RUB",
            "period": "month",
            "percent": 82,
            "enabled": True,
        }],
    )

    limit = detect_limit_pace(value, today=date(2026, 8, 10))[0]
    assert limit.detector_type == "limit_pace"
    assert limit.content_data["used_percent"] == 82
    assert limit.content_data["period_progress"] == 32
    assert limit.content_data["pace_excess"] == Decimal("5000.00")
    assert limit.absolute_delta == Decimal("5000.00")
    assert limit.relative_delta == Decimal("0.5")
    assert limit.active_control is True


def test_limit_pace_ranks_more_used_and_larger_monetary_excess_higher():
    value = snapshot([], [], limits=[
        {"id": "limit-70", "title": "70", "amount": Decimal("10000"), "spent": Decimal("7000"), "currency": "RUB", "period": "month", "percent": 70},
        {"id": "limit-97", "title": "97", "amount": Decimal("10000"), "spent": Decimal("9700"), "currency": "RUB", "period": "month", "percent": 97},
    ])
    candidates = detect_limit_pace(value, today=date(2026, 8, 10))
    for item in candidates:
        assign_fingerprint(item, generated_at=NOW)

    by_id = {item.entity_key: item for item in candidates}
    assert by_id["limit-97"].impact > by_id["limit-70"].impact
    assert rank_candidates(candidates, now=NOW)[0].entity_key == "limit-97"

    same_severity = snapshot([], [], limits=[
        {"id": "small", "title": "Small", "amount": Decimal("1000"), "spent": Decimal("900"), "currency": "RUB", "period": "month", "percent": 90},
        {"id": "large", "title": "Large", "amount": Decimal("2000"), "spent": Decimal("1800"), "currency": "RUB", "period": "month", "percent": 90},
    ])
    candidates = detect_limit_pace(same_severity, today=date(2026, 8, 10))
    for item in candidates:
        assign_fingerprint(item, generated_at=NOW)
    assert rank_candidates(candidates, now=NOW)[0].entity_key == "large"


def test_category_growth_uses_existing_limit_or_create_action_by_canonical_key():
    existing = acceptance_snapshot(limits=[{
        "id": "category:month:OTHER",
        "title": "Продукты",
        "category": " ПРОДУКТЫ ",
        "amount": Decimal("46000"),
        "spent": Decimal("18400"),
        "currency": "RUB",
        "period": "month",
        "percent": 40,
    }])
    with_limit = detect_category_contribution(existing)[0]
    without_limit = detect_category_contribution(acceptance_snapshot())[0]
    read_only = detect_category_contribution(replace(existing, can_write=False))[0]

    assert [action.type for action in with_limit.actions] == ["OPEN_CATEGORY", "OPEN_OPERATIONS", "OPEN_LIMIT"]
    assert with_limit.actions[-1].params["limit_id"] == "category:month:OTHER"
    assert with_limit.active_control is True
    assert [action.type for action in without_limit.actions][-1] == "CREATE_LIMIT"
    assert [action.type for action in read_only.actions] == ["OPEN_CATEGORY", "OPEN_OPERATIONS"]


def test_mixed_currencies_are_filtered_not_combined():
    value = snapshot(
        [
            ("Продукты", "Shop", "RUB", Decimal("27000"), 12),
            ("Продукты", "Shop", "USD", Decimal("900"), 12),
        ],
        [
            ("Продукты", "Shop", "RUB", Decimal("20000"), 10),
            ("Продукты", "Shop", "USD", Decimal("500"), 10),
        ],
        currency="RUB",
    )

    assert value.current_total == Decimal("27000.00")
    assert value.previous_total == Decimal("20000.00")
    assert all(item.currency == "RUB" for item in detect_candidates(value, today=date(2026, 8, 10)))


def test_no_useful_candidates_returns_empty_and_no_usually_claim():
    engine = InsightEngine(MemoryStore())
    value = snapshot(
        [("Кофе", "Cafe", "RUB", Decimal("100"), 2)],
        [("Кофе", "Cafe", "RUB", Decimal("90"), 2)],
    )

    assert engine.generate(value, today=date(2026, 8, 10), now=NOW) == []
    assert "обычно" not in str(engine.generate(acceptance_snapshot(), today=date(2026, 8, 10), now=NOW)).lower()


def test_ranking_prefers_large_impact_to_tiny_percentage_and_is_deterministic():
    large = detect_spending_change(snapshot(
        [("A", "A", "RUB", Decimal("27000"), 10)],
        [("A", "A", "RUB", Decimal("20000"), 10)],
    ))[0]
    tiny = replace(
        large,
        entity_key="tiny",
        current_value=Decimal("50"),
        baseline_value=Decimal("10"),
        absolute_delta=Decimal("40"),
        relative_delta=Decimal("4"),
        impact=Decimal("0.08"),
    )
    assign_fingerprint(large, generated_at=NOW)
    assign_fingerprint(tiny, generated_at=NOW)

    first = rank_candidates([tiny, large], now=NOW)
    second = rank_candidates([large, tiny], now=NOW)
    assert first[0].entity_key == "expenses"
    assert [item.fingerprint for item in first] == [item.fingerprint for item in second]


def test_active_limit_risk_outranks_minor_novelty_and_low_confidence_loses():
    value = acceptance_snapshot(limits=[{
        "id": "category:month:Продукты",
        "title": "Продукты",
        "category": "Продукты",
        "amount": Decimal("19000"),
        "spent": Decimal("18400"),
        "currency": "RUB",
        "period": "month",
        "percent": 97,
    }])
    grouped = group_candidates(detect_candidates(value, today=date(2026, 8, 10)))
    for item in grouped:
        assign_fingerprint(item, generated_at=NOW)
    ranked = rank_candidates(grouped, now=NOW)

    assert ranked[0].detector_type == "limit_pace"

    high = replace(ranked[0], entity_key="high", active_control=False, confidence="high", score=Decimal("0"))
    low = replace(high, entity_key="low", confidence="medium", score=Decimal("0"))
    assign_fingerprint(high, generated_at=NOW)
    assign_fingerprint(low, generated_at=NOW)
    assert rank_candidates([low, high], now=NOW)[0].entity_key == "high"


def test_general_limit_risk_absorbs_related_growth_narrative():
    value = acceptance_snapshot(limits=[{
        "id": "general:1",
        "title": "Все расходы",
        "category": None,
        "amount": Decimal("19000"),
        "spent": Decimal("18400"),
        "currency": "RUB",
        "period": "month",
        "percent": 97,
    }])

    grouped = group_candidates(detect_candidates(value, today=date(2026, 8, 10)))
    assert [item.detector_type for item in grouped] == ["limit_pace"]
    assert {item["kind"] for item in grouped[0].evidence} >= {"limit_pace", "amount_comparison", "merchant_contribution"}


def test_selected_category_limit_keeps_one_grouped_story():
    value = snapshot(
        [("Рестораны", "Bistro", "RUB", Decimal("9700"), 12)],
        [("Рестораны", "Bistro", "RUB", Decimal("5400"), 7)],
        scope_category="Рестораны",
        limits=[{
            "id": "category:month:restaurants",
            "title": "Рестораны",
            "category": "РЕСТОРАНЫ",
            "amount": Decimal("10000"),
            "spent": Decimal("9700"),
            "currency": "RUB",
            "period": "month",
            "percent": 97,
        }],
    )

    grouped = group_candidates(detect_candidates(value, today=date(2026, 8, 10)))

    assert [item.detector_type for item in grouped] == ["limit_pace"]
    assert {item["kind"] for item in grouped[0].evidence} >= {
        "limit_pace", "amount_comparison", "merchant_contribution", "count_comparison",
    }


def test_final_list_is_capped_at_three():
    base = detect_spending_change(snapshot(
        [("A", "A", "RUB", Decimal("27000"), 10)],
        [("A", "A", "RUB", Decimal("20000"), 10)],
    ))[0]
    values = []
    for index in range(5):
        item = replace(base, entity_key=f"entity-{index}", score=Decimal("0"))
        assign_fingerprint(item, generated_at=NOW)
        values.append(item)

    assert len(rank_candidates(values, now=NOW, limit=10)) == 3


def test_repeat_penalty_and_hard_repeat_window():
    candidate = detect_spending_change(acceptance_snapshot())[0]
    assign_fingerprint(candidate, generated_at=NOW)
    once = InsightState(candidate.fingerprint, candidate.detector_type, show_count=1, first_shown_at=NOW, last_shown_at=NOW)
    repeated = InsightState(candidate.fingerprint, candidate.detector_type, show_count=3, first_shown_at=NOW, last_shown_at=NOW)

    assert rank_candidates([candidate], [once], now=NOW)[0].score < rank_candidates([candidate], now=NOW)[0].score
    assert rank_candidates([candidate], [repeated], now=NOW) == []


def test_repeat_window_resets_count_and_penalty_after_24_hours():
    store = MemoryStore()
    engine = InsightEngine(store)
    first = engine.generate(acceptance_snapshot(), today=date(2026, 8, 10), now=NOW)
    fingerprint = first[0]["id"]
    key = (42, 10, fingerprint)
    store.values[key] = replace(
        store.values[key],
        show_count=2,
        first_shown_at=NOW - timedelta(hours=25),
        last_shown_at=NOW - timedelta(hours=23),
    )

    candidate = group_candidates(detect_candidates(acceptance_snapshot(), today=date(2026, 8, 10)))[0]
    assign_fingerprint(candidate, generated_at=NOW)
    fresh_score = rank_candidates([candidate], now=NOW)[0].score
    reset_score = rank_candidates([candidate], [store.values[key]], now=NOW)[0].score
    assert reset_score == fresh_score

    store.now = NOW
    state = engine.impression(42, 10, fingerprint)
    assert state and state.show_count == 1
    assert state.first_shown_at == NOW


def test_postgres_impression_update_resets_the_existing_24_hour_window(monkeypatch):
    captured = {}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params):
            captured["sql"] = sql
            captured["params"] = params

        def fetchone(self):
            return ("a" * 64, "spending_change", 1, NOW, NOW, None, None)

    class Connection:
        def cursor(self):
            return Cursor()

        def commit(self):
            captured["committed"] = True

        def rollback(self):
            raise AssertionError("unexpected rollback")

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr("services.insights.get_conn", lambda: Connection())

    state = PostgresInsightStateStore().record_impression(42, 10, "a" * 64)

    assert state and state.show_count == 1 and state.first_shown_at == NOW
    assert "first_shown_at <= now() - interval '24 hours'" in captured["sql"]
    assert "THEN 1" in captured["sql"]
    assert captured["params"] == (42, 10, "a" * 64)
    assert captured["committed"] is True and captured["closed"] is True


def test_negative_feedback_suppresses_only_same_detector_in_same_scope():
    store = MemoryStore()
    engine = InsightEngine(store)
    value = acceptance_snapshot()
    first = engine.generate(value, today=date(2026, 8, 10), now=NOW)
    fingerprint = first[0]["id"]
    assert engine.feedback(42, 10, fingerprint, "not_useful") is not None

    assert engine.generate(value, today=date(2026, 8, 10), now=NOW + timedelta(minutes=1)) == []
    assert InsightEngine(store).generate(acceptance_snapshot(user_id=43), today=date(2026, 8, 10), now=NOW + timedelta(minutes=1))
    assert InsightEngine(store).generate(acceptance_snapshot(workspace_id=11), today=date(2026, 8, 10), now=NOW + timedelta(minutes=1))


def test_useful_feedback_is_presented_for_same_insight_fingerprint():
    store = MemoryStore()
    engine = InsightEngine(store)
    value = acceptance_snapshot()
    first = engine.generate(value, today=date(2026, 8, 10), now=NOW)
    fingerprint = first[0]["id"]

    assert engine.feedback(42, 10, fingerprint, "useful") is not None

    repeated = engine.generate(value, today=date(2026, 8, 10), now=NOW + timedelta(minutes=1))
    assert repeated[0]["id"] == fingerprint
    assert repeated[0]["feedback"] == "useful"


def test_negative_feedback_does_not_suppress_unrelated_detector():
    spending = detect_spending_change(acceptance_snapshot())[0]
    frequency = detect_frequency_change(acceptance_snapshot())[0]
    assign_fingerprint(spending, generated_at=NOW)
    assign_fingerprint(frequency, generated_at=NOW)
    suppressed = InsightState(
        spending.fingerprint,
        "spending_change",
        feedback_type="not_useful",
        suppression_until=NOW + timedelta(days=30),
    )

    ranked = rank_candidates([spending, frequency], [suppressed], now=NOW)
    assert [item.detector_type for item in ranked] == ["merchant_frequency"]


def test_old_period_state_does_not_hide_current_candidate():
    candidate = detect_spending_change(acceptance_snapshot())[0]
    assign_fingerprint(candidate, generated_at=NOW)
    old_state = InsightState(
        "f" * 64,
        candidate.detector_type,
        show_count=3,
        last_shown_at=NOW,
    )

    assert rank_candidates([candidate], [old_state], now=NOW) == [candidate]


def test_lifecycle_is_idempotent_and_material_change_gets_new_fingerprint():
    store = MemoryStore()
    engine = InsightEngine(store)
    first = engine.generate(acceptance_snapshot(), today=date(2026, 8, 10), now=NOW)
    second = engine.generate(acceptance_snapshot(), today=date(2026, 8, 10), now=NOW)

    assert first[0]["id"] == second[0]["id"]
    assert len(store.values) == 1
    state = engine.impression(42, 10, first[0]["id"])
    assert state and state.show_count == 1

    changed = snapshot(
        [("Продукты", "Яндекс Лавка", "RUB", Decimal("20000"), 14)],
        [("Продукты", "Яндекс Лавка", "RUB", Decimal("14300"), 7)],
    )
    changed_result = engine.generate(changed, today=date(2026, 8, 10), now=NOW + timedelta(hours=1))
    assert changed_result[0]["id"] != first[0]["id"]


def test_frequency_material_count_change_gets_new_fingerprint():
    first = detect_frequency_change(acceptance_snapshot())[0]
    changed = replace(first, current_value=Decimal("15"), absolute_delta=Decimal("8"), content_data={**first.content_data, "current_count": 15})
    assign_fingerprint(first, generated_at=NOW)
    assign_fingerprint(changed, generated_at=NOW)

    assert first.fingerprint != changed.fingerprint
