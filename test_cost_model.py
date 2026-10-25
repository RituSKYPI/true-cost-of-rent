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


class TestPhotoFeatures(unittest.TestCase):
    """Features the model reports seeing in listing photos adjust the
    electricity estimate. The direction of each has to be right and the
    combined effect has to stay bounded."""

    def test_no_features_means_no_adjustment(self):
        factor, reasons = main.feature_adjustment([])
        self.assertEqual(factor, 1.0)
        self.assertEqual(reasons, [])

    def test_resistance_heat_raises_the_estimate(self):
        plain, _ = main.utilities_for(2, [], "VA")
        heated, _ = main.utilities_for(2, [], "VA", ["electric_baseboard_heat"])
        self.assertGreater(heated, plain)

    def test_gas_heat_lowers_the_electric_estimate(self):
        plain, _ = main.utilities_for(2, [], "VA")
        gas, _ = main.utilities_for(2, [], "VA", ["gas_heat"])
        self.assertLess(gas, plain)

    def test_unknown_feature_is_ignored_not_guessed_at(self):
        factor, _ = main.feature_adjustment(["marble_countertops", "balcony"])
        self.assertEqual(factor, 1.0)

    def test_adjustment_is_bounded_both_ways(self):
        everything = list(main.FEATURE_EFFECT.keys())
        factor, _ = main.feature_adjustment(everything)
        self.assertGreaterEqual(factor, 0.75)
        self.assertLessEqual(factor, 1.6)

    def test_adjustment_is_reported_not_hidden(self):
        _, items = main.utilities_for(2, [], "VA", ["window_ac"])
        electric = [i for i in items if i["name"] == "Electricity"][0]
        self.assertIn("adjusted", electric)
        self.assertTrue(electric["adjusted"]["reasons"])

    def test_no_adjustment_key_when_nothing_was_seen(self):
        _, items = main.utilities_for(2, [], "VA", [])
        electric = [i for i in items if i["name"] == "Electricity"][0]
        self.assertNotIn("adjusted", electric)

    def test_features_do_not_apply_when_the_landlord_pays(self):
        a, _ = main.utilities_for(2, ["electric"], "VA", ["electric_baseboard_heat"])
        b, _ = main.utilities_for(2, ["electric"], "VA", [])
        self.assertEqual(a, b)


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


class TestWhatWouldSharpen(unittest.TestCase):
    """The panel that says how uncertain the estimate still is."""

    def test_gating_inputs_come_first(self):
        out = main.what_would_sharpen({"bedrooms": 2}, {})
        fields = [i["field"] for i in out["items"]]
        self.assertIn("income", fields[:2])
        self.assertIn("rent", fields[:2])

    def test_everything_known_means_nothing_to_suggest(self):
        parsed = {"rent": 1100, "bedrooms": 2, "state": "VA",
                  "utilities_stated": True, "unit_features": ["window_ac"],
                  "fees_without_amounts": []}
        form = {"income": 3000, "rent": 1100, "state": "VA", "commute_miles": 4}
        out = main.what_would_sharpen(parsed, form)
        self.assertEqual(out["items"], [])
        self.assertEqual(out["possible_swing"], 0)

    def test_filling_something_in_removes_it(self):
        parsed = {"rent": 1100, "bedrooms": 2, "utilities_stated": True,
                  "unit_features": ["window_ac"]}
        before = main.what_would_sharpen(parsed, {"income": 3000, "rent": 1100})
        after = main.what_would_sharpen(
            parsed, {"income": 3000, "rent": 1100, "state": "VA",
                     "commute_miles": 4})
        self.assertGreater(before["possible_swing"], after["possible_swing"])

    def test_a_fee_with_no_price_is_suggested_with_its_range(self):
        out = main.what_would_sharpen(
            {"rent": 1000, "bedrooms": 1, "state": "VA",
             "utilities_stated": True, "unit_features": ["window_ac"],
             "fees_without_amounts": ["parking"]},
            {"income": 3000, "rent": 1000, "state": "VA", "commute_miles": 2})
        parking = [i for i in out["items"] if i["field"] == "fee:parking"]
        self.assertEqual(len(parking), 1)
        self.assertGreater(parking[0]["high"], parking[0]["low"])

    def test_swing_matches_the_sum_of_the_non_gating_items(self):
        out = main.what_would_sharpen({"bedrooms": 2}, {})
        rest = [i for i in out["items"] if i["field"] not in ("income", "rent")]
        self.assertAlmostEqual(out["possible_swing"],
                               sum(i["high"] - i["low"] for i in rest), delta=1)

    def test_suggestions_are_capped_so_the_panel_stays_readable(self):
        out = main.what_would_sharpen(
            {"bedrooms": 4,
             "fees_without_amounts": ["parking", "pet", "amenity", "valet_trash"]},
            {})
        self.assertLessEqual(len(out["items"]), 6)


class TestAmbiguousFeatures(unittest.TestCase):
    """A listing that says "Heating" has told you nothing about the bill.
    The app prices the not knowing rather than guessing or ignoring it."""

    def test_heating_is_the_biggest_unknown(self):
        heat = main.ambiguity_cost("heating_type_unknown", 2, "VA")
        cool = main.ambiguity_cost("cooling_type_unknown", 2, "VA")
        laundry = main.ambiguity_cost("laundry_type_unknown", 2, "VA")
        self.assertGreater(heat, cool)
        self.assertGreater(cool, laundry)

    def test_cost_of_not_knowing_scales_with_the_state_price(self):
        cheap = main.ambiguity_cost("heating_type_unknown", 2, "WA")
        dear = main.ambiguity_cost("heating_type_unknown", 2, "HI")
        self.assertGreater(dear, cheap)

    def test_unknown_kind_is_ignored(self):
        self.assertIsNone(main.ambiguity_cost("colour_of_the_door", 2, "VA"))

    def test_ambiguity_becomes_a_question(self):
        out = main.what_would_sharpen(
            {"rent": 1000, "bedrooms": 2, "state": "VA",
             "utilities_stated": True, "unit_features": ["window_ac"],
             "ambiguous_features": ["heating_type_unknown"]},
            {"income": 3000, "rent": 1000, "state": "VA", "commute_miles": 2})
        fields = [i["field"] for i in out["items"]]
        self.assertIn("ambiguous:heating_type_unknown", fields)

    def test_a_photo_that_settles_it_removes_the_question(self):
        base = {"rent": 1000, "bedrooms": 2, "state": "VA",
                "utilities_stated": True,
                "ambiguous_features": ["cooling_type_unknown"]}
        form = {"income": 3000, "rent": 1000, "state": "VA", "commute_miles": 2}

        unresolved = main.what_would_sharpen(dict(base, unit_features=[]), form)
        resolved = main.what_would_sharpen(
            dict(base, unit_features=["window_ac"]), form)

        self.assertIn("ambiguous:cooling_type_unknown",
                      [i["field"] for i in unresolved["items"]])
        self.assertNotIn("ambiguous:cooling_type_unknown",
                         [i["field"] for i in resolved["items"]])


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

    def test_a_commute_lowers_what_you_can_pay_in_rent(self):
        near = main.affordable_rent(3000, 1, "VA", [], main.commute_cost(2, "drive"))
        far = main.affordable_rent(3000, 1, "VA", [], main.commute_cost(20, "drive"))
        self.assertLess(far["rent_only"], near["rent_only"])

    def test_the_commute_does_not_change_the_gross_ceiling(self):
        # 30 percent of income is 30 percent of income. The commute comes out
        # of what is left for rent, not out of the guideline itself.
        a = main.affordable_rent(3000, 1, "VA", [], 0)
        b = main.affordable_rent(3000, 1, "VA", [], 200)
        self.assertEqual(a["gross"], b["gross"])
        self.assertLess(b["rent_only"], a["rent_only"])

    def test_the_rent_ceiling_drops_by_the_cost_of_the_commute(self):
        a = main.affordable_rent(3000, 1, "VA", [], 0)
        b = main.affordable_rent(3000, 1, "VA", [], 150)
        self.assertAlmostEqual(a["rent_only"] - b["rent_only"], 150, places=2)


class TestIntentNormalisation(unittest.TestCase):
    """Turning what someone typed into parameters. The expensive mistake is
    a yearly salary read as a monthly one, so most of this guards that."""

    def test_a_monthly_figure_passes_through(self):
        self.assertEqual(main.normalise_intent({"income": 2400})["income"], 2400)

    def test_a_yearly_figure_is_converted(self):
        self.assertEqual(main.normalise_intent({"income": 60000})["income"], 5000)

    def test_an_impossible_figure_is_dropped_and_declared(self):
        out = main.normalise_intent({"income": 9000000})
        self.assertIsNone(out["income"])
        self.assertIn("your income", out["unclear"])

    def test_a_negative_income_is_dropped(self):
        self.assertIsNone(main.normalise_intent({"income": -500})["income"])

    def test_text_where_a_number_should_be_is_dropped(self):
        self.assertIsNone(main.normalise_intent({"income": "not sure"})["income"])

    def test_bedrooms_are_clamped(self):
        self.assertEqual(main.normalise_intent({"bedrooms": 40})["bedrooms"], 6)
        self.assertEqual(main.normalise_intent({"bedrooms": -2})["bedrooms"], 0)

    def test_roommates_cannot_be_zero(self):
        self.assertEqual(main.normalise_intent({"roommates": 0})["roommates"], 1)

    def test_a_state_that_does_not_exist_is_dropped(self):
        self.assertIsNone(main.normalise_intent({"state": "ZZ"})["state"])
        self.assertEqual(main.normalise_intent({"state": "va"})["state"], "VA")

    def test_an_invented_commute_mode_is_dropped(self):
        self.assertIsNone(main.normalise_intent({"commute_mode": "teleport"})["commute_mode"])
        self.assertEqual(main.normalise_intent({"commute_mode": "Drive"})["commute_mode"], "drive")

    def test_a_malformed_zip_is_dropped(self):
        self.assertEqual(main.normalise_intent({"zip": "24060"})["zip"], "24060")
        self.assertIsNone(main.normalise_intent({"zip": "240"})["zip"])

    def test_nulls_stay_null_rather_than_becoming_zero(self):
        out = main.normalise_intent({"income": None, "bedrooms": None,
                                     "state": None, "zip": None})
        for k in ("income", "bedrooms", "state", "zip"):
            self.assertIsNone(out[k])

    def test_an_empty_answer_is_handled(self):
        self.assertEqual(main.normalise_intent({}), {})
        self.assertEqual(main.normalise_intent(None), {})


class TestUrlNormalisation(unittest.TestCase):
    """People paste addresses in every shape there is."""

    def test_a_proper_url_is_left_alone(self):
        self.assertEqual(main.normalise_url("https://a.com/b"), "https://a.com/b")

    def test_http_is_allowed(self):
        self.assertEqual(main.normalise_url("http://a.com/b"), "http://a.com/b")

    def test_a_missing_protocol_is_added(self):
        self.assertEqual(main.normalise_url("www.apartments.com/x/"),
                         "https://www.apartments.com/x/")
        self.assertEqual(main.normalise_url("apartments.com/x/"),
                         "https://apartments.com/x/")

    def test_a_protocol_relative_url_works(self):
        self.assertEqual(main.normalise_url("//a.com/b"), "https://a.com/b")

    def test_whitespace_and_quotes_are_stripped(self):
        self.assertEqual(main.normalise_url('  "https://a.com"  '), "https://a.com")

    def test_something_that_is_not_an_address_is_rejected(self):
        self.assertIsNone(main.normalise_url("just some words"))
        self.assertIsNone(main.normalise_url(""))
        self.assertIsNone(main.normalise_url(None))


class TestChatFieldWriting(unittest.TestCase):
    """The assistant can fill in the form. Nothing it returns reaches a
    field without passing this, because a model writing directly into the
    inputs that drive the calculation is the one place a bad value would do
    real damage."""

    def test_a_monthly_income_passes(self):
        self.assertEqual(main.clean_chat_fields({"income": 2400}),
                         {"income": 2400})

    def test_a_yearly_income_is_converted(self):
        self.assertEqual(main.clean_chat_fields({"income": 60000}),
                         {"income": 5000})

    def test_a_field_name_we_do_not_allow_is_dropped(self):
        self.assertEqual(main.clean_chat_fields({"delete_everything": 1}), {})
        self.assertEqual(main.clean_chat_fields({"__proto__": "x"}), {})

    def test_out_of_range_values_are_dropped_not_clamped(self):
        # Clamping would quietly turn a misread into a plausible wrong
        # number. Dropping it leaves the field blank, which is visible.
        self.assertEqual(main.clean_chat_fields({"bedrooms": 99}), {})
        self.assertEqual(main.clean_chat_fields({"rent": -5}), {})

    def test_text_where_a_number_belongs_is_dropped(self):
        self.assertEqual(main.clean_chat_fields({"income": "lots"}), {})

    def test_state_must_be_a_real_code(self):
        self.assertEqual(main.clean_chat_fields({"state": "va"}), {"state": "VA"})
        self.assertEqual(main.clean_chat_fields({"state": "Virginia"}), {})

    def test_commute_mode_must_be_one_we_handle(self):
        self.assertEqual(main.clean_chat_fields({"commute_mode": "Drive"}),
                         {"commute_mode": "drive"})
        self.assertEqual(main.clean_chat_fields({"commute_mode": "hoverboard"}), {})

    def test_zip_must_be_five_digits(self):
        self.assertEqual(main.clean_chat_fields({"x_zip": "24060"}),
                         {"x_zip": "24060"})
        self.assertEqual(main.clean_chat_fields({"x_zip": "abc"}), {})

    def test_explore_fields_are_allowed_too(self):
        out = main.clean_chat_fields({"x_city": "Blacksburg", "x_bedrooms": 2})
        self.assertEqual(out, {"x_city": "Blacksburg", "x_bedrooms": 2})

    def test_text_is_length_limited(self):
        out = main.clean_chat_fields({"destination": "x" * 500})
        self.assertLessEqual(len(out["destination"]), 120)

    def test_empty_and_null_are_ignored(self):
        self.assertEqual(main.clean_chat_fields({"rent": None, "state": ""}), {})
        self.assertEqual(main.clean_chat_fields(None), {})


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




class SalvageReply(unittest.TestCase):
    """A broken model response must never show the person raw JSON."""

    def test_truncated_json_yields_the_sentence_not_the_braces(self):
        raw = ('```json\n{\n "reply": "I updated the tool to 2 people '
               'splitting the rent.",\n "set_fields": {\n  "roommates": 2\n },'
               '\n "')
        out = main._salvage_reply(raw)
        self.assertEqual(out, "I updated the tool to 2 people splitting the rent.")
        for junk in ('{', '}', 'set_fields', 'json'):
            self.assertNotIn(junk, out)

    def test_reply_cut_off_mid_word_still_returns_prose(self):
        out = main._salvage_reply('{"reply": "The heat is the big unknown here')
        self.assertEqual(out, "The heat is the big unknown here")

    def test_plain_prose_passes_through(self):
        out = main._salvage_reply("Press analyse and I will work it out.")
        self.assertEqual(out, "Press analyse and I will work it out.")

    def test_unsalvageable_returns_none_so_caller_uses_its_own_words(self):
        for junk in ("{{{{", "", "   ", '{"set_fields": {"rent": 900}}'):
            self.assertIsNone(main._salvage_reply(junk))

    def test_no_output_ever_contains_a_field_name(self):
        samples = ['{"reply": "ok", "set_fields": {"income": 5000}}',
                   '{"set_fields": {"income": 5000}}',
                   '```json\n{"reply": "fine"}\n```']
        for raw in samples:
            out = main._salvage_reply(raw) or ""
            self.assertNotIn("set_fields", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
