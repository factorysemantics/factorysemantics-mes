"""The gauge study's arithmetic gives the published worked example's numbers.

The data set is the crossed study of 10 parts, 3 appraisers (A, B, C) and 3
trials printed in the AIAG Measurement Systems Analysis reference manual,
4th edition (2010), chapter III, section B - the data sheet of Figure
III-B 15 and the ANOVA results of Tables III-B 6 and 6a. The manual is
copyrighted; only its numbers are used here, as test data, with this
citation, and none of its text. Every figure asserted is checked to the
decimals the manual prints.
"""

import pytest

from fsmes.services import gage_rr

# Readings [appraiser][trial] -> parts 1..10.
DATA = {
    "A": [[0.29, -0.56, 1.34, 0.47, -0.80, 0.02, 0.59, -0.31, 2.26, -1.36],
          [0.41, -0.68, 1.17, 0.50, -0.92, -0.11, 0.75, -0.20, 1.99, -1.25],
          [0.64, -0.58, 1.27, 0.64, -0.84, -0.21, 0.66, -0.17, 2.01, -1.31]],
    "B": [[0.08, -0.47, 1.19, 0.01, -0.56, -0.20, 0.47, -0.63, 1.80, -1.68],
          [0.25, -1.22, 0.94, 1.03, -1.20, 0.22, 0.55, 0.08, 2.12, -1.62],
          [0.07, -0.68, 1.34, 0.20, -1.28, 0.06, 0.83, -0.34, 2.19, -1.50]],
    "C": [[0.04, -1.38, 0.88, 0.14, -1.46, -0.29, 0.02, -0.46, 1.77, -1.49],
          [-0.11, -1.13, 1.09, 0.20, -1.07, -0.67, 0.01, -0.56, 1.45, -1.77],
          [-0.15, -0.96, 0.67, 0.11, -1.45, -0.49, 0.21, -0.49, 1.87, -2.16]],
}


def values() -> dict:
    return {(part + 1, who, trial + 1): v
            for who, rows in DATA.items()
            for trial, row in enumerate(rows)
            for part, v in enumerate(row)}


@pytest.fixture(scope="module")
def arith():
    return gage_rr.anova(values(), 10, ["A", "B", "C"], 3)


def test_the_anova_table_matches_the_published_one_to_its_printed_decimals(arith):
    rows = {r["source"]: r for r in arith["table"]}
    assert [rows[s]["df"] for s in ("appraiser", "part", "appraiser_x_part",
                                    "repeatability", "total")] == [2, 9, 18, 60, 89]
    assert round(rows["appraiser"]["ss"], 4) == 3.1673
    assert round(rows["part"]["ss"], 4) == 88.3619
    assert round(rows["appraiser_x_part"]["ss"], 4) == 0.3590
    assert round(rows["repeatability"]["ss"], 4) == 2.7589
    assert round(rows["total"]["ss"], 4) == 94.6471
    assert round(rows["appraiser"]["ms"], 5) == 1.58363
    assert round(rows["part"]["ms"], 5) == 9.81799
    assert round(rows["appraiser_x_part"]["ms"], 5) == 0.01994
    assert round(rows["repeatability"]["ms"], 5) == 0.04598
    assert round(rows["appraiser"]["f"], 2) == 79.41
    assert round(rows["part"]["f"], 2) == 492.29
    assert round(rows["appraiser_x_part"]["f"], 3) == 0.434


def test_the_interaction_is_not_significant_and_is_pooled_as_the_manual_does(arith):
    assert arith["interaction_significant"] is False
    assert arith["variance"]["interaction"] == 0.0


def test_the_variance_components_match_the_published_ones(arith):
    var = arith["variance"]
    assert round(var["repeatability"], 6) == 0.039973
    assert round(var["appraiser"], 6) == 0.051455
    assert round(var["gauge_rr"], 5) == 0.09143
    assert round(var["part"], 6) == 1.086447


def test_percent_of_total_variation_and_distinct_categories_match_the_manual(arith):
    judged = gage_rr.judge(arith, tolerance=None)
    by = {c["source"]: c for c in judged["components"]}
    assert round(by["repeatability"]["pct_study_var"], 1) == 18.4
    assert round(by["appraiser"]["pct_study_var"], 1) == 20.9
    assert round(by["gauge_rr"]["pct_study_var"], 1) == 27.9
    assert round(by["part"]["pct_study_var"], 1) == 96.0
    assert round(by["repeatability"]["pct_contribution"], 1) == 3.4
    assert round(by["appraiser"]["pct_contribution"], 1) == 4.4
    assert round(by["gauge_rr"]["pct_contribution"], 1) == 7.8
    assert round(by["part"]["pct_contribution"], 1) == 92.2
    assert judged["distinct_categories"] == 4
    assert judged["band"] == "marginal"


def test_the_f_tail_agrees_with_a_value_anyone_can_look_up():
    # F(2, 10) at 4.10 is the 5 % critical value in every table.
    assert round(gage_rr.f_upper_tail(4.10, 2, 10), 3) == 0.050
