import numpy as np
import pytest

from ml.rolling_features import BEHAVIOR_FEATURES, NETWORK_FEATURES, rolling_features


def calculate(events):
    minutes, sender, receiver, amount, currency = np.asarray(events, dtype=np.int64).T
    values = rolling_features(
        minutes=minutes,
        sender=sender,
        receiver=receiver,
        paid=amount.astype(float),
        received=amount.astype(float),
        pay_currency=currency,
        receive_currency=currency,
    )
    return [dict(zip(BEHAVIOR_FEATURES + NETWORK_FEATURES, row, strict=True)) for row in values]


def test_same_minute_transfers_are_scored_before_any_same_minute_update():
    events = [
        (0, 1, 0, 60, 0),
        (5, 0, 1, 120, 0),
        (5, 0, 2, 20, 0),
        (6, 0, 1, 10, 1),
        (7, 0, 2, 40, 0),
    ]
    features = calculate(events)
    first, second = features[1:3]
    assert first["sender_debit_count_24h"] == second["sender_debit_count_24h"] == 0
    assert first["unique_beneficiaries_24h"] == second["unique_beneficiaries_24h"] == 0
    assert first["reverse_pair_count_24h"] == 1
    assert first["sender_recent_inflow_coverage_2h"] == pytest.approx(0.5)
    assert second["sender_recent_inflow_coverage_2h"] == 1
    assert features[3]["counterparty_novelty"] == 0
    assert features[3]["sender_recent_inflow_coverage_2h"] == 0
    assert features[4]["sender_in_out_ratio_24h"] == pytest.approx(60 / 140)
    assert features[4]["unique_beneficiaries_24h"] == 2
    reordered = calculate([events[0], events[2], events[1], *events[3:]])
    assert reordered[1:3] == [second, first]
    assert reordered[3:] == features[3:]
    assert calculate(events[:3]) == features[:3]


def test_active_window_expires_without_erasing_an_observed_relationship():
    features = calculate(
        [
            (0, 0, 1, 40, 0),
            (0, 3, 0, 60, 1),
            (0, 0, 0, 1000, 1),
            (120, 0, 2, 100, 1),
            (121, 0, 4, 100, 1),
            (1440, 0, 5, 100, 0),
            (1441, 0, 1, 200, 1),
        ]
    )
    assert features[3]["unique_beneficiaries_24h"] == 1
    assert features[3]["sender_recent_inflow_coverage_2h"] == pytest.approx(0.6)
    assert features[4]["sender_recent_inflow_coverage_2h"] == 0
    assert features[5]["sender_debit_count_24h"] == 3
    assert features[5]["sender_same_currency_debit_history_24h"] == 1
    assert features[6]["sender_debit_count_24h"] == 3
    assert features[6]["sender_credit_count_24h"] == 0
    assert features[6]["counterparty_novelty"] == 0


def test_replay_rejects_unsorted_events():
    with pytest.raises(ValueError, match="chronologically"):
        calculate([(1, 0, 1, 100, 0), (0, 1, 2, 50, 0)])


def test_foreign_exchange_history_uses_each_side_amount_and_currency():
    values = rolling_features(
        minutes=np.array([0, 1, 1]),
        sender=np.array([1, 0, 0]),
        receiver=np.array([0, 1, 2]),
        paid=np.array([9000.0, 200.0, 200.0]),
        received=np.array([100.0, 200.0, 200.0]),
        pay_currency=np.array([0, 1, 0]),
        receive_currency=np.array([1, 1, 0]),
    )
    coverage = BEHAVIOR_FEATURES.index("sender_recent_inflow_coverage_2h")
    assert values[1, coverage] == pytest.approx(0.5)
    assert values[2, coverage] == 0
