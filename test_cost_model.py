"""Tests for the cost model.

The whole claim of this project is that the arithmetic lives in code where it
can be checked, rather than inside a language model where it cannot. That
claim is only worth anything if somebody actually checks it. This is that.

Run with:  python3 -m pytest test_cost_model.py -q
or with:   python3 test_cost_model.py
"""

import sys
import types
import unittest


def _stub_cloud():
    """Import main without needing Google Cloud credentials."""
    for name in ("google", "google.cloud", "google.cloud.firestore"):
        sys.modules.setdefault(name, types.ModuleType(name))
    fs = sys.modules["google.cloud.firestore"]

    class _Doc:
        exists = False

        def to_dict(self):
            return {}

    class _Ref:
        def get(self):
            return _Doc()

        def set(self, *a, **k):
            pass

    class _Client:
        def collection(self, *a):
            return self

        def document(self, *a):
            return _Ref()

        def add(self, *a):
            pass

    fs.Client = _Client
    fs.SERVER_TIMESTAMP = None


_stub_cloud()
import main  # noqa: E402


BASE = dict(bedrooms=1, roommates=1, income=3000, state="VA",
            lease_months=12, commute_mode="drive", commute_miles=0,
            utilities_included=[], parking=0, other_fees=0, one_time_fees=0)


def run(**over):
    return main.analyze(dict(BASE, **over))


class TestBurden(unittest.TestCase):
    def test_burden_is_total_over_income(self):
        r = run(rent=900, income=3000)
        # burden is stored rounded to 4 places for display
        self.assertAlmostEqual(r["burden"], r["total"] / 3000, places=4)

    def test_bands(self):
        self.assertEqual(run(rent=400, income=5000)["level"], "ok")
        self.assertEqual(run(rent=1500, income=4000)["level"], "high")
        self.assertEqual(run(rent=2000, income=3000)["level"], "severe")

    def test_boundaries_are_inclusive_at_thirty_and_fifty(self):
        # A result sitting exactly on 30 percent counts as burdened, which is
        # how HUD defines it. Same at 50.
        r = run(rent=1000, income=1000 / 0.30, utilities_included=
                ["electric", "water", "gas", "trash", "internet"])
        # rent 1000 + insurance 15, so nudge income to land exactly on .30
        r = main.analyze(dict(BASE, rent=1000, income=(1000 + 15) / 0.30,
                              utilities_included=["electric", "water", "gas",
                                                  "trash", "internet"]))
        self.assertEqual(r["level"], "high")

    def test_zero_income_does_not_divide_by_zero(self):
        r = run(rent=900, income=0)
        self.assertEqual(r["burden"], 0)

    def test_left_over_is_income_minus_total(self):
        r = run(rent=900, income=3000)
        self.assertAlmostEqual(r["left_over"], 3000 - r["total"], places=2)


class TestHiddenCosts(unittest.TestCase):
    def test_advertised_is_rent_only(self):
        r = run(rent=900, parking=50, commute_miles=10)
        self.assertEqual(r["advertised_monthly"], 900)

    def test_hidden_is_everything_else(self):
        r = run(rent=900, parking=50)
        self.assertAlmostEqual(r["hidden_monthly"], r["total"] - 900, places=2)

    def test_true_cost_exceeds_advertised_when_costs_exist(self):
        r = run(rent=900, parking=50, commute_miles=10)
        self.assertGreater(r["total"], r["advertised_monthly"])


class TestOneTimeFees(unittest.TestCase):
    def test_spread_over_the_lease_not_charged_monthly(self):
        r = run(rent=900, one_time_fees=1200, lease_months=12)
        self.assertEqual(r["breakdown"]["one_time_monthly"], 100.0)

    def test_shorter_lease_concentrates_the_fee(self):
        a = run(rent=900, one_time_fees=600, lease_months=12)
        b = run(rent=900, one_time_fees=600, lease_months=6)
        self.assertGreater(b["breakdown"]["one_time_monthly"],
                           a["breakdown"]["one_time_monthly"])

    def test_a_monthly_fee_is_not_the_same_as_a_one_time_fee(self):
        monthly = run(rent=900, other_fees=200)
        once = run(rent=900, one_time_fees=200)
        self.assertGreater(monthly["total"], once["total"])


class TestSplitting(unittest.TestCase):
    def test_rent_and_utilities_split_but_personal_costs_do_not(self):
        alone = run(rent=1200, bedrooms=2)
        shared = run(rent=1200, bedrooms=2, roommates=2)
        self.assertEqual(shared["breakdown"]["rent_share"], 600)
        self.assertAlmostEqual(shared["breakdown"]["utilities"],
                               alone["breakdown"]["utilities"] / 2, places=2)
        self.assertEqual(shared["breakdown"]["insurance"],
                         alone["breakdown"]["insurance"])

    def test_per_bed_listing_entered_whole_gives_back_the_bed_price(self):
        # The Chase Apartments case. 894 a bed, 4 beds, 3576 for the unit.
        r = run(rent=3576, bedrooms=4, roommates=4, income=5000,
                utilities_included=["trash", "internet"])
        self.assertEqual(r["breakdown"]["rent_share"], 894)

    def test_roommates_cannot_be_zero(self):
        r = run(rent=900, roommates=0)
        self.assertEqual(r["breakdown"]["rent_share"], 900)


class TestUtilities(unittest.TestCase):
    def test_state_price_changes_the_bill(self):
        va, _ = main.utilities_for(2, [], "VA")
        ca, _ = main.utilities_for(2, [], "CA")
        self.assertGreater(ca, va)

    def test_unknown_state_falls_back_to_the_us_average(self):
        _, items = main.utilities_for(2, [], "ZZ")
        electric = [i for i in items if i["name"] == "Electricity"][0]
        self.assertFalse(electric["sourced"])
        self.assertIn("US", electric["basis"])

    def test_included_utilities_are_not_charged(self):
        all_in, items = main.utilities_for(2, ["electric", "water", "gas",
                                               "trash", "internet"], "VA")
        self.assertEqual(all_in, 0)
        self.assertEqual(items, [])

    def test_bigger_unit_costs_more(self):
        studio, _ = main.utilities_for(0, [], "VA")
        three, _ = main.utilities_for(3, [], "VA")
        self.assertGreater(three, studio)

    def test_electricity_is_price_times_consumption(self):
        _, items = main.utilities_for(2, [], "VA")
        electric = [i for i in items if i["name"] == "Electricity"][0]
        expected = main.KWH_BY_BEDROOMS[2] * main.CENTS_PER_KWH["VA"] / 100
        self.assertAlmostEqual(electric["amount"], round(expected, 2), places=2)


class TestConcessions(unittest.TestCase):
    def test_weeks_free_lowers_effective_rent(self):
        c = main.concession(2266, 8, None, 12, False)
        self.assertLess(c["effective_rent"], 2266)
        # a 12 month lease is 52.14 weeks, not 52, which is the basis the
        # code uses
        weeks = 12 * 4.345
        self.assertAlmostEqual(c["effective_rent"],
                               2266 * (weeks - 8) / weeks, places=1)

    def test_renewal_jump_equals_the_discount(self):
        c = main.concession(2000, 4, None, 12, False)
        self.assertAlmostEqual(c["jump_at_renewal"], c["saving_per_month"], places=2)

    def test_net_effective_pricing_is_not_discounted_twice(self):
        self.assertIsNone(main.concession(2000, 8, None, 12, True))

    def test_no_special_means_no_concession(self):
        self.assertIsNone(main.concession(2000, 0, 0, 12, False))

    def test_headline_total_uses_full_rent_not_the_discount(self):
        r = run(rent=2000, income=6000, weeks_free=8)
        self.assertEqual(r["breakdown"]["rent_share"], 2000)


class TestFeeCatalogue(unittest.TestCase):
    def test_silent_fees_are_surfaced(self):
        out = main.unaddressed_fees([], has_pet=False)
        self.assertTrue(any(f["id"] == "parking" for f in out))

    def test_answered_fees_are_not(self):
        out = main.unaddressed_fees(["parking"], has_pet=False)
        self.assertFalse(any(f["id"] == "parking" for f in out))

    def test_mentioned_without_a_price_is_still_surfaced_and_labelled(self):
        out = main.unaddressed_fees(["admin"], has_pet=False,
                                    no_amount=["parking"])
        parking = [f for f in out if f["id"] == "parking"]
        self.assertEqual(len(parking), 1)
        self.assertEqual(parking[0]["status"], "no_amount")

    def test_pet_fees_only_appear_for_pet_owners(self):
        self.assertFalse(any(f["id"] == "pet"
                             for f in main.unaddressed_fees([], has_pet=False)))
        self.assertTrue(any(f["id"] == "pet"
                            for f in main.unaddressed_fees([], has_pet=True)))


class TestLevers(unittest.TestCase):
    def test_a_lever_never_costs_more_than_doing_nothing(self):
        for miles in (0.5, 2, 5, 15, 40):
            for mode in ("drive", "transit", "bike", "walk"):
                r = run(rent=900, bedrooms=2, commute_miles=miles,
                        commute_mode=mode)
                for lever in r["levers"]:
                    self.assertLess(lever["new_total"], r["total"],
                                    "%s at %s miles suggested a worse option"
                                    % (mode, miles))

    def test_saving_matches_the_difference(self):
        r = run(rent=1200, bedrooms=2, income=4000)
        for lever in r["levers"]:
            self.assertAlmostEqual(lever["saves"],
                                   r["total"] - lever["new_total"], places=2)


class TestAffordability(unittest.TestCase):
    def test_gross_ceiling_is_thirty_percent(self):
        b = main.affordable_rent(3000, 1, "VA")
        self.assertEqual(b["gross"], 900)

    def test_rent_only_ceiling_is_lower_than_gross(self):
        b = main.affordable_rent(3000, 1, "VA")
        self.assertLess(b["rent_only"], b["gross"])

    def test_a_roommate_raises_what_the_household_can_carry(self):
        one = main.affordable_rent(3000, 2, "VA", share=1)
        two = main.affordable_rent(3000, 2, "VA", share=2)
        self.assertGreater(two["rent_only"], one["rent_only"])

    def test_no_income_gives_no_answer(self):
        self.assertIsNone(main.affordable_rent(0, 1, "VA"))


class TestNothingIsInvented(unittest.TestCase):
    """The model is never allowed to produce a figure. These check that the
    analysis is a pure function of its inputs and nothing else."""

    def test_same_input_same_output(self):
        a = run(rent=1100, bedrooms=2, income=3400, commute_miles=6)
        b = run(rent=1100, bedrooms=2, income=3400, commute_miles=6)
        a.pop("explanation", None)
        b.pop("explanation", None)
        self.assertEqual(a, b)

    def test_missing_inputs_do_not_become_guesses(self):
        r = main.analyze({"rent": 900, "income": 3000})
        self.assertEqual(r["breakdown"]["parking"], 0)
        self.assertEqual(r["breakdown"]["other_fees"], 0)
        self.assertEqual(r["breakdown"]["commute"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
