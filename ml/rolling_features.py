"""Rolling directed-graph features for a chronological transaction replay."""

import numpy as np

DAY_MINUTES = 24 * 60
RECENT_MINUTES = 2 * 60
BEHAVIOR_FEATURES = (
    "sender_debit_count_24h",
    "sender_credit_count_24h",
    "receiver_debit_count_24h",
    "receiver_credit_count_24h",
    "sender_in_out_ratio_24h",
    "sender_same_currency_debit_history_24h",
    "sender_recent_inflow_coverage_2h",
)
NETWORK_FEATURES = (
    "unique_beneficiaries_24h",
    "receiver_unique_originators_24h",
    "counterparty_novelty",
    "reverse_pair_count_24h",
)


def _ratio(numerator, denominator):
    return np.divide(numerator, denominator, out=np.zeros_like(numerator), where=denominator > 0)


def _move_amounts(counts, totals, keys, amounts, change):
    np.add.at(counts, keys, change)
    np.add.at(totals, keys, change * amounts)
    if change < 0:
        empty = keys[counts[keys] == 0]
        totals[empty] = 0


def rolling_features(*, minutes, sender, receiver, paid, received, pay_currency, receive_currency):
    """Read history strictly before each event minute, then incorporate its transfers.

    IDs are dense nonnegative integers. Monetary totals are indexed by the
    account and the currency on its side of the transfer. Self-transfers do
    not contribute to historical flow or counterparty state.
    """
    if len(minutes) == 0:
        return np.empty((0, len(BEHAVIOR_FEATURES) + len(NETWORK_FEATURES)), dtype=np.float32)
    if np.any(np.diff(minutes) < 0):
        raise ValueError("Transactions must be ordered chronologically")
    account_count = int(max(sender.max(), receiver.max())) + 1
    currency_count = int(max(pay_currency.max(), receive_currency.max())) + 1
    amount_cells = account_count * currency_count

    sender_key = sender * currency_count + pay_currency
    receiver_key = receiver * currency_count + receive_currency
    distinct_transfer = sender != receiver
    pair_keys, pair = np.unique(sender.astype(np.int64) * account_count + receiver, return_inverse=True)
    pair_sender, pair_receiver = np.divmod(pair_keys, account_count)
    reverse_keys = pair_receiver * account_count + pair_sender
    reverse = np.searchsorted(pair_keys, reverse_keys)
    found = (reverse < len(pair_keys)) & (
        pair_keys[np.minimum(reverse, len(pair_keys) - 1)] == reverse_keys
    )
    reverse = np.where(found, reverse, len(pair_keys))

    debit_count = np.zeros(account_count, dtype=np.int32)
    credit_count = np.zeros(account_count, dtype=np.int32)
    beneficiaries = np.zeros(account_count, dtype=np.int32)
    originators = np.zeros(account_count, dtype=np.int32)
    pair_count = np.zeros(len(pair_keys) + 1, dtype=np.int32)
    seen = np.zeros(len(pair_keys), dtype=bool)
    debit_currency_count = np.zeros(amount_cells, dtype=np.int32)
    credit_currency_count = np.zeros(amount_cells, dtype=np.int32)
    recent_currency_count = np.zeros(amount_cells, dtype=np.int32)
    debits = np.zeros(amount_cells)
    credits = np.zeros(amount_cells)
    recent_credits = np.zeros(amount_cells)

    def move_day(start, end, change):
        part = slice(start, end)
        include = distinct_transfer[part]
        debit = sender[part][include]
        credit = receiver[part][include]
        np.add.at(debit_count, debit, change)
        np.add.at(credit_count, credit, change)
        _move_amounts(
            debit_currency_count, debits, sender_key[part][include], paid[part][include], change
        )
        _move_amounts(
            credit_currency_count, credits, receiver_key[part][include], received[part][include], change
        )
        active, count = np.unique(pair[part][include], return_counts=True)
        previous = pair_count[active]
        updated = previous + change * count
        delta = (updated > 0).astype(np.int32) - (previous > 0).astype(np.int32)
        pair_count[active] = updated
        np.add.at(beneficiaries, pair_sender[active], delta)
        np.add.at(originators, pair_receiver[active], delta)
        if change > 0:
            seen[active] = True

    def move_recent(start, end, change):
        part = slice(start, end)
        include = distinct_transfer[part]
        _move_amounts(
            recent_currency_count,
            recent_credits,
            receiver_key[part][include],
            received[part][include],
            change,
        )

    starts = np.r_[0, np.flatnonzero(np.diff(minutes)) + 1]
    ends = np.r_[starts[1:], len(minutes)]
    day_starts = np.searchsorted(minutes, minutes[starts] - DAY_MINUTES)
    recent_starts = np.searchsorted(minutes, minutes[starts] - RECENT_MINUTES)
    old_day = old_recent = 0
    result = np.empty((len(minutes), len(BEHAVIOR_FEATURES) + len(NETWORK_FEATURES)), dtype=np.float32)
    for start, end, day_start, recent_start in zip(starts, ends, day_starts, recent_starts, strict=True):
        if day_start > old_day:
            move_day(old_day, day_start, -1)
        if recent_start > old_recent:
            move_recent(old_recent, recent_start, -1)
        part = slice(start, end)
        debit, credit = sender[part], receiver[part]
        key = sender_key[part]
        active_pair = pair[part]
        history_ratio = _ratio(np.maximum(credits[key], 0), debits[key])
        inflow_coverage = np.minimum(1, _ratio(np.maximum(recent_credits[key], 0), paid[part]))
        result[part] = np.column_stack(
            (
                debit_count[debit],
                credit_count[debit],
                debit_count[credit],
                credit_count[credit],
                history_ratio,
                debit_currency_count[key] > 0,
                np.where(distinct_transfer[part], inflow_coverage, 0),
                beneficiaries[debit],
                originators[credit],
                distinct_transfer[part] & ~seen[active_pair],
                pair_count[reverse[active_pair]],
            )
        )
        move_day(start, end, 1)
        move_recent(start, end, 1)
        old_day, old_recent = day_start, recent_start
    return result
