# tests/test_verify_numbers.py
"""check_numbers: the bypasses the audit confirmed by running them are closed (small rupee amounts, years
written as rupees, the wrong currency on matching digits, derivations over unrelated values), honest narrations
still pass, and a random property check: exact values always pass, a value off by more than 1% never does."""
import random

import pytest

from wealthos.verify import _derived, check_numbers

SPEND = [38699.0, 30000.0]


@pytest.mark.parametrize("text", ["You spent ₹2000 on Swiggy.", "That was ₹25.", "You paid 1999 rupees.",
                                  "You spent Rs 2000.", "You spent Rs2000.", "You spent 2000 on food."])
def test_small_amounts_and_years_as_money_are_checked(text):
    assert [i.reason for i in check_numbers(text, SPEND, "INR")] == ["unmatched"]


@pytest.mark.parametrize("text, unit", [("You spent $38,699.", "INR"), ("You spent 38,699 dollars.", "INR"),
                                        ("USD 38,699 in total", "INR"), ("Revenue was ₹38,699.", "USD"),
                                        ("Revenue was 3.87 lakh.", "USD")])
def test_wrong_currency_fails_even_when_the_digits_match(text, unit):
    assert [i.reason for i in check_numbers(text, [38699.0, 387000.0], unit)] == ["currency_mismatch"]


@pytest.mark.parametrize("text", [
    "over 3 months", "in FY 2025-26", "FY2025-26", "in 2026", "since 2019", "₹38,699", "₹38.7 thousand",
    "Rs. 38,699", "38,699 rupees", "INR 38,699", "your top 5 merchants", "From 2026-04-01 to 2026-06-30",
    "On 5 April 2026 (see [p. 12])", "on April 5, 2026", "between 01/04/2026 and 30/06/2026", "by 31st March 2026",
    "from 2026‑04‑01 to 2026‑10‑26",  # non-breaking hyphens, as gpt-oss writes dates
])
def test_honest_text_passes(text):
    assert check_numbers(text, SPEND, "INR") == []


def test_sizes_derivations_and_rows():
    assert check_numbers("₹3.87 lakh", [387000.0], "INR") == []
    assert check_numbers("up 12.5%", [100.0, 112.5], "INR") == []
    rows = [{"total_income": 90000.0, "total_spend": 29000.0}]
    assert check_numbers("You kept ₹61,000 of ₹90,000.", [], "INR", rows=rows) == []  # two columns of one row
    holdings = [{"symbol": "INFY", "value": 20000.0}, {"symbol": "TCS", "value": 8000.0}]
    assert check_numbers("INFY is ₹12,000 more than TCS; ₹28,000 in all.", [], "INR", rows=holdings) == []


# Derivations stay inside a row or a column: a difference between unrelated cells of different rows is refused.
def test_derivations_are_scoped():
    rows = [{"a": 50000.0, "b": 1.0}, {"a": 20000.0, "b": 7000.0}]
    assert check_numbers("₹43,000", [], "INR", rows=rows) != []  # 50,000 - 7,000 mixes row 1 col a with row 2 col b
    assert check_numbers("₹43,000", [50000.0, 7000.0], "INR") == []  # one plain group: allowed


def test_property_exact_pass_perturbed_fail():
    rnd = random.Random(7)
    checked = 0
    for _ in range(300):
        vals = [round(rnd.uniform(1000, 9_000_000), 2) for _ in range(rnd.randint(1, 6))]
        assert check_numbers(" and ".join(f"₹{v:,.2f}" for v in vals), vals, "INR") == []
        off = rnd.choice(vals) * rnd.choice([0.97, 1.03, 1.5, 0.5])
        if all(abs(off - d) > 0.01 * abs(d) for d in _derived(vals, total=False)):  # not reachable by chance
            assert check_numbers(f"₹{off:,.2f}", vals, "INR") != [], (off, vals)
            checked += 1
    assert checked > 200
