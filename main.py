"""
True Cost of Rent - backend.

Design notes for judges:
  * ALL arithmetic happens here in plain Python. The language model never
    computes a number, so cost figures cannot be hallucinated.
  * The model is used only for two things it is actually needed for:
      1. Reading messy, unstructured listing text and pulling out fields.
      2. Explaining a result that was already computed in code.
  * Income is never written to the database. See /api/analyze.
"""

import os
import re
import json
import base64
import hashlib
import traceback

import requests
from flask import Flask, jsonify, request, send_from_directory
from google.cloud import firestore

app = Flask(__name__, static_folder="static")
db = firestore.Client()

# --- model client is optional -------------------------------------------
# The app is fully usable without it. If the model is unavailable we fall
# back to a deterministic parser and a templated explanation.
MODEL_NAME = os.environ.get("MODEL_NAME", "")
LOCATION = os.environ.get("LOCATION", "us-central1")
PROJECT_ID = os.environ.get("PROJECT_ID", "")

client = None
model_status = "disabled"
try:
    if MODEL_NAME and PROJECT_ID:
        from google import genai

        client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)
        model_status = "ready"
except Exception as e:  # pragma: no cover
    model_status = "error: %s" % e


# --- cost model ----------------------------------------------------------

# --- electricity ---------------------------------------------------------
# Average residential price in cents per kWh, by state.
#
# SOURCE: U.S. Energy Information Administration, "Electric Sales, Revenue,
# and Average Price", Table 5A, 2024 data.
# https://www.eia.gov/electricity/sales_revenue_price/pdf/table_5a.pdf
#
# This half of the estimate is real, cited, and the same for everyone. The
# other half, how much electricity a unit of a given size actually uses, is
# an assumption and is kept separate on purpose so a reader can see which is
# which and change the assumption without touching the data.
EIA_YEAR = 2024
EIA_SOURCE = ("U.S. Energy Information Administration, Table 5A, 2024. "
              "https://www.eia.gov/electricity/sales_revenue_price/pdf/table_5a.pdf")

CENTS_PER_KWH = {
    "AL": 15.18, "AK": 24.82, "AZ": 14.91, "AR": 12.32, "CA": 31.97,
    "CO": 14.92, "CT": 28.75, "DE": 16.57, "DC": 17.71, "FL": 14.14,
    "GA": 14.08, "HI": 42.86, "ID": 11.52, "IL": 15.87, "IN": 14.77,
    "IA": 13.40, "KS": 14.15, "KY": 12.79, "LA": 11.73, "ME": 24.29,
    "MD": 17.86, "MA": 29.35, "MI": 19.30, "MN": 15.45, "MS": 13.39,
    "MO": 12.91, "MT": 12.66, "NE": 11.53, "NV": 15.00, "NH": 23.40,
    "NJ": 19.34, "NM": 14.20, "NY": 24.43, "NC": 14.13, "ND": 11.51,
    "OH": 15.99, "OK": 12.24, "OR": 14.70, "PA": 17.77, "RI": 28.65,
    "SC": 14.23, "SD": 12.86, "TN": 12.42, "TX": 14.94, "UT": 12.22,
    "VT": 21.90, "VA": 14.41, "WA": 11.90, "WV": 15.07, "WI": 17.18,
    "WY": 12.47,
}
US_AVG_CENTS = 16.48          # EIA 2024 US total, used when state is unknown

# ASSUMPTION, not data. Monthly electricity use for an apartment, by bedroom
# count. EIA's own state averages cover all homes including large detached
# houses, so using them directly would badly overstate a studio. These are
# apartment-scale figures and the UI labels them an estimate.
KWH_BY_BEDROOMS = {0: 380, 1: 500, 2: 700, 3: 880, 4: 1020}

# Features the model can SEE in listing photos that change the electricity
# bill, and by how much. These are adjustments to our own assumption, not
# data, and the UI says so beside the figure.
#
# The direction of each is well established even though the size is not:
# resistance heating converts electricity to heat at roughly 1:1 while a heat
# pump moves several units of heat per unit of electricity, so all-electric
# resistance heat is the big one. Window units are less efficient than a
# properly sized central system. Single pane windows lose more heat.
#
# Kept deliberately conservative. The point is to stop pretending every
# apartment of a given size uses the same power, not to claim precision.
FEATURE_EFFECT = {
    "electric_baseboard_heat": (1.35, "electric resistance heating, which "
                                      "costs far more to run than gas or a heat pump"),
    "all_electric":            (1.15, "no gas supply, so everything runs on electricity"),
    "window_ac":               (1.10, "window air conditioning, less efficient "
                                      "than a central system"),
    "old_windows":             (1.08, "older single pane windows lose more heat"),
    "in_unit_laundry":         (1.05, "a washer and dryer in the unit"),
    "gas_heat":                (0.88, "gas heating, so less of the bill is electric"),
    "heat_pump":               (0.90, "a heat pump, which is efficient to run"),
    "central_air":             (1.00, ""),
    "gas_stove":               (0.98, "a gas stove rather than electric"),
    "no_laundry":              (1.00, ""),
}


# Listings routinely name a category without naming the type. "Heating" and
# "Air Conditioning" tell you nothing about the bill, because the bill
# depends entirely on which kind. The honest response is neither to guess nor
# to ignore it, but to price the uncertainty and tell the person to ask.
AMBIGUOUS_FEATURES = {
    "heating_type_unknown": {
        "label": "What kind of heating it has",
        "why": "The listing says heating but not which kind. Electric "
               "baseboard costs far more to run than gas or a heat pump, and "
               "this is the biggest single swing in the whole estimate.",
        "how": "Ask whether it is gas, a heat pump, or electric baseboard. "
               "Baseboard heaters are long low metal panels along the wall.",
        "cheapest": "gas_heat",
        "dearest": "electric_baseboard_heat",
    },
    "cooling_type_unknown": {
        "label": "What kind of air conditioning it has",
        "why": "A window unit costs more to run than a properly sized "
               "central system, and the listing does not say which this is.",
        "how": "Ask, or look for a boxy unit in a window in the photos.",
        "cheapest": "central_air",
        "dearest": "window_ac",
    },
    "laundry_type_unknown": {
        "label": "Whether the washer and dryer are in your unit",
        "why": "A shared laundry room costs per load instead of showing up "
               "on your power bill.",
        "how": "Ask whether it is in the apartment or in a shared room.",
        "cheapest": "no_laundry",
        "dearest": "in_unit_laundry",
    },
}


def ambiguity_cost(kind, bedrooms, state):
    """What not knowing is worth, per month, in dollars."""
    spec = AMBIGUOUS_FEATURES.get(kind)
    if not spec:
        return None
    b = min(max(int(bedrooms or 1), 0), 4)
    kwh = KWH_BY_BEDROOMS[b]
    cents = CENTS_PER_KWH.get((state or "").upper()[:2], US_AVG_CENTS)
    base = kwh * cents / 100.0
    lo = FEATURE_EFFECT[spec["cheapest"]][0]
    hi = FEATURE_EFFECT[spec["dearest"]][0]
    return round(base * (hi - lo), 2)


def feature_adjustment(features):
    """Combined multiplier on electricity use, plus why. Capped so a long
    feature list cannot run away with the estimate."""
    factor, reasons = 1.0, []
    for f in (features or []):
        eff = FEATURE_EFFECT.get(f)
        if not eff:
            continue
        mult, why = eff
        factor *= mult
        if why:
            reasons.append(why)
    factor = max(0.75, min(factor, 1.6))
    return round(factor, 3), reasons

# Non-electric utilities an apartment typically pays, monthly dollars. Also
# an assumption. Water and sewer vary enormously by municipality.
OTHER_UTILITY_COST = {
    "water": {0: 25, 1: 32, 2: 45, 3: 58, 4: 70},
    "gas":   {0: 18, 1: 24, 2: 34, 3: 44, 4: 54},
    "trash": {0: 12, 1: 14, 2: 18, 3: 22, 4: 26},
    "internet": {0: 55, 1: 60, 2: 65, 3: 70, 4: 75},
}

# --- the fee catalogue ---------------------------------------------------
# This is the heart of the product. A first time renter does not know what
# they do not know. A listing that never mentions valet trash still bills
# you for it. So we hold a list of charges that are routine in US rentals,
# ask the model only which ones THIS listing addressed, and then surface
# everything it stayed silent about.
#
# The ranges live here in code, not in the model, so they are consistent and
# auditable. They are broad national typicals and the UI says so. Replace
# them with sourced local figures before you rely on them.
FEE_CATALOG = [
    {"id": "application", "name": "Application fee",
     "low": 25, "high": 100, "unit": "once, per adult",
     "why": "Charged to run your credit check. Usually not refundable, even "
            "if you are turned down."},
    {"id": "admin", "name": "Administrative fee",
     "low": 100, "high": 500, "unit": "once",
     "why": "A paperwork charge at signing. Separate from the deposit and "
            "you do not get it back."},
    {"id": "deposit", "name": "Security deposit",
     "low": 300, "high": 2000, "unit": "once, usually refundable",
     "why": "You get this back if you leave the place in good shape, but you "
            "need the cash up front. Photograph everything on move in day."},
    {"id": "parking", "name": "Parking",
     "low": 25, "high": 150, "unit": "a month",
     "why": "Often not included even at places with a garage or lot."},
    {"id": "valet_trash", "name": "Trash or valet trash",
     "low": 15, "high": 40, "unit": "a month",
     "why": "A mandatory service fee at many complexes. You cannot opt out."},
    {"id": "water_sewer", "name": "Water and sewer billed back",
     "low": 30, "high": 90, "unit": "a month",
     "why": "Many places split the building's water bill across tenants by "
            "unit size rather than metering you. Often called RUBS."},
    {"id": "amenity", "name": "Amenity or common area fee",
     "low": 10, "high": 60, "unit": "a month",
     "why": "Charged whether or not you use the gym or pool."},
    {"id": "pest", "name": "Pest control fee",
     "low": 3, "high": 15, "unit": "a month",
     "why": "Small, mandatory, and almost never in the headline price."},
    {"id": "insurance", "name": "Renters insurance",
     "low": 10, "high": 30, "unit": "a month",
     "why": "Most leases require proof of it before you get keys."},
    {"id": "pet", "name": "Pet rent and pet fee",
     "low": 25, "high": 75, "unit": "a month, plus a one time fee",
     "why": "Only if you have a pet, but the one time fee is often several "
            "hundred dollars on top."},
    {"id": "utility_setup", "name": "Utility connection charges",
     "low": 25, "high": 150, "unit": "once",
     "why": "Electric and internet providers charge to start service."},
    {"id": "early_termination", "name": "Early termination fee",
     "low": 0, "high": 0, "unit": "often two months rent",
     "why": "What it costs to leave early. This is the one that ruins people "
            "who have to move for a job or a family reason."},
    {"id": "renewal", "name": "Lease renewal or month to month premium",
     "low": 50, "high": 400, "unit": "a month, after the first year",
     "why": "Staying on month to month after the lease ends usually costs a "
            "lot more than renewing."},
]

CATALOG_BY_ID = {f["id"]: f for f in FEE_CATALOG}

# Cost per mile of commuting, one way, counted twice a day, 22 workdays.
CENTS_PER_MILE = 0.21
COMMUTE_DAYS = 22

BURDEN_OK = 0.30
BURDEN_SEVERE = 0.50


def utilities_for(bedrooms, included, state=None, features=None):
    """Monthly utility estimate, built up line by line rather than as one
    opaque number.

    Electricity uses the real EIA price for the state multiplied by an
    assumed consumption for the unit size. Everything else is a flat
    assumption. Returns the total and the itemised parts so the UI can show
    its working and say which figures are sourced.
    """
    b = min(max(int(bedrooms or 0), 0), 4)
    included = {u.lower().strip() for u in (included or [])}
    state = (state or "").upper().strip()[:2]

    cents = CENTS_PER_KWH.get(state, US_AVG_CENTS)
    base_kwh = KWH_BY_BEDROOMS[b]
    factor, reasons = feature_adjustment(features)
    kwh = int(round(base_kwh * factor))
    electric = round(kwh * cents / 100.0, 2)

    items = []
    if "electric" not in included:
        basis = ("%s kWh a month at %.2f cents, the %s average for %s"
                 % (kwh, cents, EIA_YEAR,
                    state if state in CENTS_PER_KWH else "the US"))
        item = {
            "name": "Electricity",
            "amount": electric,
            "basis": basis,
            "sourced": state in CENTS_PER_KWH,
        }
        if reasons:
            item["adjusted"] = {
                "base_kwh": base_kwh,
                "factor": factor,
                "reasons": reasons,
            }
        items.append(item)
    for key, label in (("water", "Water and sewer"), ("gas", "Gas"),
                       ("trash", "Trash"), ("internet", "Internet")):
        if key in included:
            continue
        amt = OTHER_UTILITY_COST[key][b]
        items.append({"name": label, "amount": float(amt),
                      "basis": "typical for this unit size", "sourced": False})

    total = round(sum(i["amount"] for i in items), 2)
    return total, items


def commute_cost(miles, mode):
    """Monthly commute cost. Walking and biking are free."""
    mode = (mode or "drive").lower()
    if mode in ("walk", "bike", "bicycle"):
        return 0
    if mode == "transit":
        return 60  # typical monthly pass, override if you have local data
    return round(miles * 2 * COMMUTE_DAYS * CENTS_PER_MILE)


# --- real commute times --------------------------------------------------
# Optional. If a Maps key is configured we look up the actual drive and
# transit time between the listing and wherever the person has to be. If it
# is not, the app falls back to the distance the user types in and nothing
# breaks.
MAPS_API_KEY = os.environ.get("MAPS_API_KEY", "")
ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
MODE_MAP = {"drive": "DRIVE", "transit": "TRANSIT",
            "bike": "BICYCLE", "walk": "WALK"}


def route_between(origin, destination, mode="drive"):
    """One route lookup. Returns miles and minutes, or an error string."""
    if not MAPS_API_KEY:
        return None, "no_key"
    if not origin or not destination:
        return None, "missing_address"
    try:
        r = requests.post(
            ROUTES_URL, timeout=10,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": MAPS_API_KEY,
                "X-Goog-FieldMask": "routes.duration,routes.distanceMeters",
            },
            json={
                "origin": {"address": origin},
                "destination": {"address": destination},
                "travelMode": MODE_MAP.get(mode, "DRIVE"),
            },
        )
        if r.status_code != 200:
            return None, "http_%s" % r.status_code
        routes = (r.json() or {}).get("routes") or []
        if not routes:
            return None, "no_route"
        meters = routes[0].get("distanceMeters") or 0
        seconds = int(str(routes[0].get("duration", "0s")).rstrip("s") or 0)
        return {"miles": round(meters / 1609.34, 1),
                "minutes": round(seconds / 60)}, None
    except Exception:
        return None, "error"


def commute_options(listing_address, destination):
    """Drive, transit, bike and walk between the two places, with the monthly
    cost of each. Lets someone see that the cheap place twelve miles out
    costs them nine hours a week as well as the petrol."""
    out = {}
    for mode in ("drive", "transit", "bike", "walk"):
        data, err = route_between(listing_address, destination, mode)
        if not data:
            continue
        hours_per_month = round(data["minutes"] * 2 * COMMUTE_DAYS / 60, 1)
        out[mode] = {
            "miles": data["miles"],
            "minutes": data["minutes"],
            "hours_per_month": hours_per_month,
            "cost": commute_cost(data["miles"], mode),
        }
    return out


def unaddressed_fees(addressed, has_pet=False, no_amount=None):
    """Everything in the catalogue this listing did not actually answer.

    Two different kinds of silence, and both matter. A listing that never
    mentions valet trash is one thing. A listing with a parking tab that
    never states a price is another, and it is arguably worse, because it
    looks like it told you something. Both end up on the list, labelled
    differently.

    Generated from the catalogue in code rather than asked of the model, so
    the list is the same every time and nothing can be invented. The model's
    only job was saying what the listing mentioned.
    """
    addressed = set(addressed or [])
    vague = set(no_amount or [])
    out = []
    for fee in FEE_CATALOG:
        if fee["id"] == "pet" and not has_pet:
            continue
        if fee["id"] in vague:
            item = dict(fee)
            item["status"] = "no_amount"
            item["status_label"] = "Mentioned, but no price given"
            out.append(item)
        elif fee["id"] not in addressed:
            item = dict(fee)
            item["status"] = "silent"
            item["status_label"] = "Not mentioned at all"
            out.append(item)
    return out


def concession(rent, weeks_free, months_free, lease_months, net_effective):
    """Work out what a move in special is actually worth, and what happens
    when it ends.

    A special is almost always first year only. People sign based on the
    discounted number and get caught by the jump at renewal, so we show both.
    """
    if not rent or net_effective:
        return None
    weeks = float(weeks_free or 0) + float(months_free or 0) * 4.345
    if weeks <= 0:
        return None
    lease_weeks = lease_months * 4.345
    if weeks >= lease_weeks:
        weeks = lease_weeks
    paid_fraction = (lease_weeks - weeks) / lease_weeks
    effective = round(rent * paid_fraction, 2)
    return {
        "weeks_free": round(weeks, 1),
        "effective_rent": effective,
        "saving_per_month": round(rent - effective, 2),
        "total_saving": round((rent - effective) * lease_months, 2),
        "rent_after": rent,
        "jump_at_renewal": round(rent - effective, 2),
    }


def analyze(payload):
    """Pure function. Takes the user's numbers, returns the full breakdown.

    No I/O, no model call, no database write. This is the part that must be
    correct, so it is kept small and testable.
    """
    rent = float(payload.get("rent") or 0)
    roommates = max(int(payload.get("roommates") or 1), 1)
    income = float(payload.get("income") or 0)
    bedrooms = int(payload.get("bedrooms") or 1)
    included = payload.get("utilities_included") or []
    parking = float(payload.get("parking") or 0)
    other_fees = float(payload.get("other_fees") or 0)
    one_time = float(payload.get("one_time_fees") or 0)
    lease_months = max(int(payload.get("lease_months") or 12), 1)
    miles = float(payload.get("commute_miles") or 0)
    mode = payload.get("commute_mode") or "drive"
    insurance = float(payload.get("insurance") or 15)

    state = payload.get("state")
    features = payload.get("unit_features")
    utils, util_items = utilities_for(bedrooms, included, state, features)
    commute = commute_cost(miles, mode)

    # The itemised utility figures are for the whole unit. The breakdown
    # shows one person's share. Carry both so the detail table can show the
    # division instead of leaving a reader to wonder why the lines do not
    # add up to the total.
    for it in util_items:
        it["share"] = round(it["amount"] / roommates, 2)

    # Move in fees are real money but they are not a monthly cost. Spreading
    # them across the lease is the honest way to compare two places, one of
    # which front loads its fees.
    one_time_monthly = round(one_time / lease_months, 2)

    # Rent and utilities split between roommates. Parking, commute and
    # insurance are personal and are not split.
    rent_share = rent / roommates
    utils_share = utils / roommates

    total = (rent_share + utils_share + parking + other_fees + commute
             + insurance + one_time_monthly)
    total = round(total, 2)

    burden = (total / income) if income > 0 else 0
    advertised_burden = (rent_share / income) if income > 0 else 0
    left_over = round(income - total, 2)

    if burden >= BURDEN_SEVERE:
        level, label = "severe", "Severely cost burdened"
    elif burden >= BURDEN_OK:
        level, label = "high", "Cost burdened"
    else:
        level, label = "ok", "Within the 30 percent guideline"

    # Three year projection at low, middle and high historical rent growth.
    # These are ranges from past rent changes, not a forecast.
    projection = {}
    for name, rate in (("low", 0.03), ("mid", 0.05), ("high", 0.07)):
        projection[name] = {
            "rate": rate,
            "year1": round(total, 2),
            "year2": round(total * (1 + rate), 2),
            "year3": round(total * (1 + rate) ** 2, 2),
        }

    # What would actually help this person. Computed, not guessed, and only
    # listed when it genuinely saves money. A suggestion that costs more than
    # the status quo is worse than no suggestion.
    levers = []

    def add_lever(action, new_total, note=None):
        if income <= 0 or new_total >= total:
            return
        # Round first, then derive the saving from the rounded figure. If the
        # saving is computed independently it can land a cent away from the
        # difference between the two numbers shown, which looks like a bug to
        # anyone who checks.
        new_total = round(new_total, 2)
        levers.append({
            "action": action,
            "new_total": new_total,
            "saves": round(total - new_total, 2),
            "new_burden": round(new_total / income, 4),
            "note": note,
        })

    if roommates == 1 and bedrooms >= 2:
        add_lever("Add one roommate", total - rent_share / 2 - utils_share / 2)

    if commute > 0:
        if mode == "drive" and miles <= 3:
            add_lever("Walk or bike instead of driving", total - commute,
                      "At this distance it is a realistic swap.")
        elif mode == "drive" and commute > 60:
            add_lever("Switch the commute to transit", total - commute + 60)

    return {
        "breakdown": {
            "rent_share": round(rent_share, 2),
            "utilities": round(utils_share, 2),
            "parking": parking,
            "other_fees": other_fees,
            "commute": commute,
            "insurance": insurance,
            "one_time_monthly": one_time_monthly,
        },
        "one_time_total": one_time,
        "lease_months": lease_months,
        "total": total,
        "advertised_monthly": round(rent_share, 2),
        "hidden_monthly": round(total - rent_share, 2),
        "burden": round(burden, 4),
        "advertised_burden": round(advertised_burden, 4),
        "level": level,
        "label": label,
        "left_over": left_over,
        "projection": projection,
        "levers": levers,
        "needs_help": burden >= BURDEN_SEVERE,
        "ask_about": unaddressed_fees(
            payload.get("addressed_fees"), bool(payload.get("has_pet")),
            payload.get("fees_without_amounts")),
        "concession": concession(
            rent, payload.get("weeks_free"), payload.get("months_free"),
            lease_months, payload.get("net_effective")),
        "concession_conditions": payload.get("concession_conditions"),
        "utility_items": util_items,
        "utility_unit_total": round(utils, 2),
        "utility_split_between": roommates,
        "utility_source": EIA_SOURCE,
        "unit_features": features or [],
        "state": state,
    }


# --- listing parsing -----------------------------------------------------

PARSE_PROMPT = """You are reading a rental listing. Pull out only what is
explicitly stated. Return strict JSON with these keys and nothing else:

{"rent": number or null,
 "rent_min": number or null,
 "rent_max": number or null,
 "bedrooms": number or null,
 "state": "two letter US state code or null",
 "city": "string or null",
 "address": "full street address as printed, or null",
 "parking": number or null,
 "other_fees": number or null,
 "one_time_fees": number or null,
 "lease_months": number or null,
 "utilities_included": ["electric","water","gas","trash","internet"],
 "utilities_stated": true or false,
 "per_bed": true or false or null,
 "weeks_free": number or null,
 "months_free": number or null,
 "net_effective": true or false or null,
 "concession_conditions": "string or null",
 "addressed_fees": ["ids from the list below where the listing gives a
                     definite answer, including a stated price or an explicit
                     zero or waiver"],
 "fees_without_amounts": ["ids the listing refers to but never puts a number
                           on, such as a parking section with no price"],
 "unit_features": ["ids from the feature list below that you can SEE in the
                   photos or that the listing states in words"],
 "ambiguous_features": ["ids from the ambiguous list below, for things the
                         listing NAMES but does not specify the type of"],
 "red_flags": [{"phrase": "the wording from the listing, under 12 words",
                "means": "what it actually means for the renter, one line"}],
 "notes": "short string naming anything that was NOT stated"}

The unit_feature ids you may use, and nothing else:
window_ac, central_air, electric_baseboard_heat, gas_heat, heat_pump,
old_windows, in_unit_laundry, no_laundry, all_electric, gas_stove

Rules for unit_features:
- Only list what you can actually see in a photo or what the listing states
  in words. A window air conditioning unit is visible. Electric baseboard
  heaters are the long low metal units along the bottom of a wall. Old
  windows have thin aluminium or wooden frames and often a single pane.
- Never infer a feature from the building's age, the rent, or the area.
- If you cannot tell, leave it out. An empty list is a correct answer.

The ambiguous_feature ids you may use, and nothing else:
heating_type_unknown, cooling_type_unknown, laundry_type_unknown

Rules for ambiguous_features:
- Use these when the listing names a category but not the type, which is very
  common. An amenities list saying "Heating" with no further detail is
  heating_type_unknown. "Air Conditioning" with no indication of central or
  window is cooling_type_unknown. "Washer/Dryer" or "Laundry Facilities"
  without saying whether it is in the apartment is laundry_type_unknown.
- If you already put the specific version in unit_features, do NOT also list
  the ambiguous one. Seeing a window unit in a photo settles the question.
- If the listing says nothing at all about a category, it goes in neither
  list.

Rules for red_flags:
- Only quote wording that is actually in the listing. Keep each phrase short.
- Flag pressure and vagueness, not ordinary description. Examples of the kind
  of thing that counts: a discount conditional on signing within a time
  limit, "starting at" or "from" before a price, "prices subject to change",
  "up to" before a concession, "call for pricing", a rate tied to a lease
  length longer than the one advertised.
- Do not flag something just because it is a fee. Fees are handled elsewhere.
- Return an empty list if the listing is straightforward. Most are not, but
  some are.

The fee ids you may use in addressed_fees, and nothing else:
application, admin, deposit, parking, valet_trash, water_sewer, amenity,
pest, insurance, pet, utility_setup, early_termination, renewal

Concession rules:
- Many listings advertise a move in special such as "6 weeks free" or "one
  month free". Put the figure in weeks_free or months_free.
- net_effective is true only if the listing says the price already has the
  special subtracted. Words like "net effective" or "price reflects
  concession" mean true. If the special is advertised separately from the
  price, it is false.
- concession_conditions holds any string attached to the offer, such as
  "when you lease within 24 hours of touring" or "on 14 month leases".
  Copy it close to word for word. Use null if there are no conditions.

Rules you must follow:
- Never invent a number. If the listing does not state it, use null.
- If ONE rent figure is given, put it in rent and leave rent_min and rent_max
  null. If a RANGE is given, such as "$1,150 - $1,800" or "from $1,150",
  leave rent null and fill rent_min and rent_max instead. Never average a
  range into a single number.
- If several floor plans are shown, use the smallest one and say so in notes.
- per_bed is true when the listing prices a BED rather than the whole unit.
  Student housing often works this way. Signs of it: the phrase "by the bed",
  "per bed", "per person", "individual leases", or a price range whose top
  equals the bottom multiplied by the bedroom count. When per_bed is true the
  rent figure you return is the price of ONE bed, not the whole apartment.
  Say so in notes.
- state is the two letter code from the property address, such as VA or NY.
  Take it only from an address. Do not guess it from a city name you
  recognise, and do not guess it at all if no address is shown.
- utilities_stated is true only if the listing says something about which
  utilities are or are not included. If it is silent on utilities, set it
  false and return an empty utilities_included list.
- addressed_fees is only for fees the listing actually ANSWERS. A stated
  price counts. So does an explicit "$0", "none" or "waived".
- fees_without_amounts is for fees the listing gestures at without a number.
  A "Parking" heading or tab with no price goes here, not in addressed_fees.
  So does "pet friendly" with no pet rent stated. This distinction matters:
  a renter who sees a parking section assumes they have been told something,
  and they have not.
- A fee the listing never raises at all goes in neither list.
- other_fees is RECURRING monthly money only, charged every month.
- one_time_fees is money paid ONCE, such as an application fee, admin fee,
  move in fee or redecorating fee. A fee described as "at signing", "one
  time", "upon move in" or "application" is one_time_fees, never other_fees.
- A refundable security deposit is NOT a fee. Leave it out of both.
- utilities_included may only contain utilities the listing explicitly says
  are included. If it says nothing, return an empty list.
- Do not estimate. Do not explain. Return JSON only.

LISTING:
"""


# --- response cache ------------------------------------------------------
# Reading the same listing twice should not cost two model calls. People
# reload, retry, and demo the same screenshot repeatedly, and every one of
# those is an identical request. Keyed on a hash of the exact input, so a
# hit is only ever a hit on the same bytes.
CACHE_COLLECTION = "parse_cache"
cache_stats = {"hits": 0, "misses": 0}


def _cache_key(text, images):
    h = hashlib.sha256()
    h.update((text or "").encode("utf-8"))
    for img in images or []:
        h.update((img.get("data") or "")[:200000].encode("utf-8"))
    h.update(MODEL_NAME.encode("utf-8"))      # a new model invalidates old reads
    h.update(PARSE_PROMPT.encode("utf-8"))    # so does a new prompt
    return h.hexdigest()


def cache_get(key):
    try:
        doc = db.collection(CACHE_COLLECTION).document(key).get()
        if doc.exists:
            cache_stats["hits"] += 1
            return doc.to_dict().get("parsed")
    except Exception:
        pass
    cache_stats["misses"] += 1
    return None


def cache_put(key, parsed):
    try:
        db.collection(CACHE_COLLECTION).document(key).set({
            "parsed": parsed,
            "at": firestore.SERVER_TIMESTAMP,
        })
    except Exception:
        pass


def _json_from_model(raw):
    """Models sometimes wrap JSON in a code fence or add a stray line. Pull
    out the first JSON object rather than trusting the whole string."""
    raw = (raw or "").strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        if len(parts) > 1:
            raw = parts[1]
        if raw.lstrip().startswith("json"):
            raw = raw.lstrip()[4:]
    m = re.search(r"\{.*\}", raw, re.S)
    return json.loads(m.group(0) if m else raw)


def parse_listing_from_url(url):
    """Let the model fetch the page itself.

    Our own fetch gets a 403 from the big listing sites. Google's URL context
    tool goes through their index first and only falls back to a live fetch,
    so it often gets content we cannot. The trade is freshness: Google's own
    documentation warns the index may be stale, which for an article is fine
    and for a rent figure is not. So anything that comes back this way is
    flagged, and the UI says the price may be cached.
    """
    if client is None:
        return None, "no_model"
    try:
        from google.genai import types

        cfg = types.GenerateContentConfig(
            tools=[types.Tool(url_context=types.UrlContext())])
        resp = client.models.generate_content(
            model=MODEL_NAME,
            contents=[PARSE_PROMPT,
                      "Read this rental listing and apply the rules above:\n" + url],
            config=cfg)
        data = _json_from_model(resp.text)

        # Did it actually retrieve anything, or is it answering from thin air?
        got = False
        try:
            meta = resp.candidates[0].url_context_metadata
            for entry in (getattr(meta, "url_metadata", None) or []):
                if "SUCCESS" in str(getattr(entry, "url_retrieval_status", "")):
                    got = True
        except Exception:
            got = bool(data)          # no metadata to check, judge by result

        if not got or not any(data.get(k) is not None
                              for k in ("rent", "rent_min", "bedrooms")):
            return None, "nothing_useful"
        return data, None
    except Exception as e:
        return None, str(e)[:120]


def parse_listing(text=None, images=None):
    """Model reads a listing from text, from one or more screenshots, or
    both. Several screenshots matter here: the price is at the top of a
    listing page and the fees are usually in a separate section much further
    down, so one image rarely holds the whole story."""
    if client is None:
        return {"parsed": {}, "source": "unavailable",
                "notes": "Model not configured. Enter the numbers yourself."}
    images = images or []
    key = _cache_key(text, images)
    cached = cache_get(key)
    if cached is not None:
        return {"parsed": cached, "source": "model", "cached": True,
                "notes": cached.get("notes", "")}
    try:
        from google.genai import types

        parts = [PARSE_PROMPT]
        if text:
            parts.append("LISTING TEXT:\n" + text[:6000])
        for i, img in enumerate(images[:6], 1):
            parts.append(types.Part.from_bytes(
                data=base64.b64decode(img.get("data")),
                mime_type=img.get("mime") or "image/jpeg"))
        if images:
            parts.append(
                "The %d image(s) above are screenshots of ONE rental listing, "
                "taken from different parts of the same page. Treat them as a "
                "single listing. Read every number in them, including fee "
                "tables, pricing panels and move in specials. Apply the rules."
                % len(images[:6]))
        resp = client.models.generate_content(model=MODEL_NAME, contents=parts)
        data = _json_from_model(resp.text)
        cache_put(key, data)
        return {"parsed": data, "source": "model", "cached": False,
                "notes": data.get("notes", "")}
    except Exception as e:
        return {"parsed": {}, "source": "error", "notes": str(e)[:300]}


# Many listing sites block automated readers. We try once, politely, and fall
# back to asking for a screenshot rather than pretending we got the page.
BLOCKED_HINTS = ("captcha", "are you a human", "access denied",
                 "enable javascript", "request blocked", "unusual traffic")


def fetch_listing(url):
    if not re.match(r"^https?://", url or ""):
        return None, "That does not look like a web address."
    try:
        r = requests.get(
            url, timeout=12,
            headers={"User-Agent": "Mozilla/5.0 (compatible; TrueCostOfRent/1.0)"},
        )
        if r.status_code != 200:
            return None, ("The site returned %s and would not let us read the "
                          "page. Take a screenshot instead." % r.status_code)
        html = r.text
        # Strip scripts, styles and tags. Crude on purpose, the model handles
        # the mess from here.
        html = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", html)
        text = re.sub(r"(?s)<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        low = text[:4000].lower()
        if any(h in low for h in BLOCKED_HINTS) or len(text) < 400:
            return None, ("That site blocks automated readers. Take a "
                          "screenshot of the listing instead.")
        return text[:6000], None
    except Exception:
        return None, ("Could not reach that page. Take a screenshot of the "
                      "listing instead.")


EXPLAIN_PROMPT = """You are talking to someone renting on their own for the
first time. A cost breakdown has ALREADY been calculated for them and the
numbers below are final and correct.

Write 2 or 3 short sentences. The reader can already see the table, so do NOT
list the figures back to them. Tell them the thing the table does not say:
what the gap between the advertised rent and the real cost means for them,
and what the biggest hidden line actually is.

Hard rules:
- Never state a number that is not in the data below, and write every number
  as whole dollars, never with cents.
- Never recalculate anything.
- Do not restate the breakdown line by line.
- Do not give legal or financial advice, and do not tell them whether to
  take the place.
- Plain language. No jargon. Short sentences.

DATA:
"""


def _plain_fallback(result):
    return ("Once everything is counted this place costs $%d a month, not the "
            "$%d on the listing. The extra $%d a month is the part nobody "
            "advertises."
            % (round(result["total"]), round(result["advertised_monthly"]),
               round(result["hidden_monthly"])))


def explain(result):
    if client is None:
        return _plain_fallback(result)
    try:
        b = result["breakdown"]
        hidden = sorted(((k, v) for k, v in b.items() if k != "rent_share"),
                        key=lambda kv: -kv[1])
        facts = {
            "advertised_rent": round(result["advertised_monthly"]),
            "true_monthly_cost": round(result["total"]),
            "hidden_per_month": round(result["hidden_monthly"]),
            "share_of_income_percent": round(result["burden"] * 100),
            "share_of_income_if_you_only_counted_rent":
                round(result["advertised_burden"] * 100),
            "left_after_housing": round(result["left_over"]),
            "biggest_hidden_costs": [
                {"what": k, "dollars": round(v)} for k, v in hidden[:3] if v > 0
            ],
        }
        resp = client.models.generate_content(
            model=MODEL_NAME, contents=EXPLAIN_PROMPT + json.dumps(facts, indent=1))
        text = (resp.text or "").strip()
        return text or _plain_fallback(result)
    except Exception:
        return _plain_fallback(result)


# --- routes --------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


# --- what a place should cost here ---------------------------------------
# HUD publishes Fair Market Rents: the 40th percentile gross rent for a
# standard unit, for every county and metro in the country, by bedroom
# count. It is the closest thing to an official answer to "is this a normal
# price for here", and it is the benchmark the government itself uses to set
# housing voucher amounts.
#
# SOURCE: U.S. Department of Housing and Urban Development, Fair Market
# Rents API. https://www.huduser.gov/portal/dataset/fmr-api.html
HUD_TOKEN = os.environ.get("HUD_TOKEN", "")
HUD_BASE = "https://www.huduser.gov/hudapi/public"


def _hud(path, params=None):
    if not HUD_TOKEN:
        return None, "HUD lookup is not configured."
    try:
        r = requests.get("%s/%s" % (HUD_BASE, path.lstrip("/")),
                         headers={"Authorization": "Bearer " + HUD_TOKEN},
                         params=params or {}, timeout=12)
        if r.status_code != 200:
            return None, "HUD returned %s." % r.status_code
        return r.json(), None
    except Exception:
        return None, "Could not reach HUD."


def county_for(zip_code=None, city=None, state=None):
    """Resolve somewhere a person typed into the county code HUD wants."""
    if zip_code:
        data, err = _hud("usps", {"type": 2, "query": zip_code})
        if data:
            results = ((data.get("data") or {}).get("results")) or []
            if results:
                return results[0].get("geoid"), None
        if err:
            return None, err
    if state:
        data, err = _hud("fmr/listCounties/%s" % state.upper()[:2])
        if err:
            return None, err
        counties = data or []
        if city:
            want = city.strip().lower()
            # HUD county names look like "Montgomery County". Match the town
            # to a county name when we can, otherwise fall through.
            for c in counties:
                name = (c.get("county_name") or "").lower()
                if want in name or name.split(" county")[0] in want:
                    return c.get("fips_code"), None
        if counties:
            return None, ("Could not match that town to a county. Try a ZIP "
                          "code instead.")
    return None, "Need a ZIP code, or a city and state."


FMR_KEYS = [("Efficiency", 0), ("One-Bedroom", 1), ("Two-Bedroom", 2),
            ("Three-Bedroom", 3), ("Four-Bedroom", 4)]


def fair_market_rents(entity_id):
    data, err = _hud("fmr/data/%s" % entity_id)
    if err:
        return None, err
    d = (data or {}).get("data") or {}
    basic = d.get("basicdata")
    if isinstance(basic, list):          # metro areas return a list of subareas
        basic = basic[0] if basic else {}
    if not basic:
        return None, "HUD has no rent data for that area."
    out = {}
    for key, beds in FMR_KEYS:
        if basic.get(key) is not None:
            out[beds] = int(basic[key])
    return {
        "area": d.get("area_name") or d.get("county_name") or "",
        "year": d.get("year") or "",
        "by_bedrooms": out,
    }, None


# --- real places ---------------------------------------------------------
# Google Places gives us real buildings with real names and addresses. It
# does NOT give rent. We show what exists and what the area costs, and never
# invent a price for a specific building.
PLACES_URL = "https://places.googleapis.com/v1/places:searchText"


def find_places(query, limit=12):
    if not MAPS_API_KEY:
        return None, "Place search is not configured."
    try:
        r = requests.post(
            PLACES_URL, timeout=12,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": MAPS_API_KEY,
                "X-Goog-FieldMask": ",".join([
                    "places.displayName", "places.formattedAddress",
                    "places.rating", "places.userRatingCount",
                    "places.googleMapsUri", "places.websiteUri",
                ]),
            },
            json={"textQuery": query, "maxResultCount": min(limit, 20)},
        )
        if r.status_code != 200:
            return None, "Place search returned %s." % r.status_code
        places = (r.json() or {}).get("places") or []
        return [{
            "name": (p.get("displayName") or {}).get("text") or "",
            "address": p.get("formattedAddress") or "",
            "rating": p.get("rating"),
            "reviews": p.get("userRatingCount"),
            "maps_url": p.get("googleMapsUri"),
            "website": p.get("websiteUri"),
        } for p in places], None
    except Exception:
        return None, "Place search failed."


def affordable_rent(income, bedrooms, state, included=None, commute=0,
                    parking=0, share=1):
    """Two different ceilings, because two different things get compared
    against them.

    gross is 30 percent of income, full stop. HUD's Fair Market Rent is a
    GROSS rent figure, meaning it already has utilities inside it, so that is
    the number to hold an FMR against.

    rent_only takes the utilities, commute, parking and insurance off first.
    That is the number to hold a LISTING's advertised rent against, because a
    listing price has none of those in it.

    Mixing the two up understates what a person can afford by roughly the
    cost of their utilities, which is the kind of error that tells someone
    they cannot afford somewhere they can.
    """
    if not income or income <= 0:
        return None
    share = max(share, 1)
    gross = income * BURDEN_OK
    utils, _ = utilities_for(bedrooms, included or [], state)
    overhead = utils / share + commute + parking + 15  # 15 = renters insurance
    return {
        "gross": round(gross * share, 2),
        "rent_only": round(max(gross - overhead, 0) * share, 2),
        "utilities_assumed": round(utils, 2),
    }


@app.route("/healthz")
def healthz():
    return jsonify(status="ok", model=model_status, model_name=MODEL_NAME,
                   location=LOCATION, maps=bool(MAPS_API_KEY),
                   hud=bool(HUD_TOKEN), cache=dict(cache_stats))


@app.route("/api/commute", methods=["POST"])
def api_commute():
    body = request.json or {}
    listing = (body.get("listing_address") or "").strip()
    dest = (body.get("destination") or "").strip()
    if not MAPS_API_KEY:
        return jsonify(available=False,
                       reason="Commute lookup is not configured. Enter the "
                              "distance yourself.")
    if not listing or not dest:
        return jsonify(available=False,
                       reason="Need both the listing address and where you "
                              "have to get to.")
    opts = commute_options(listing, dest)
    if not opts:
        return jsonify(available=False,
                       reason="Could not find a route between those two "
                              "addresses. Check the spelling, or enter the "
                              "distance yourself.")
    return jsonify(available=True, options=opts)


@app.route("/api/explore", methods=["POST"])
def api_explore():
    """One call behind the area search. Returns three separate things and
    keeps them separate: what the area typically costs (HUD), what is
    actually there (Google), and what this person can carry (our own
    arithmetic on their income). We never merge the first two into a price
    for a specific building."""
    b = request.json or {}
    zip_code = (b.get("zip") or "").strip()
    city = (b.get("city") or "").strip()
    state = (b.get("state") or "").strip().upper()[:2]
    bedrooms = int(b.get("bedrooms") or 1)
    income = float(b.get("income") or 0)
    share = max(int(b.get("roommates") or 1), 1)
    baths = b.get("baths")
    max_rent = b.get("max_rent")

    out = {"market": None, "places": [], "budget": None, "notes": []}

    # 1. What the area costs, per HUD.
    entity, err = county_for(zip_code, city, state)
    if entity:
        market, merr = fair_market_rents(entity)
        if market:
            out["market"] = market
        elif merr:
            out["notes"].append(merr)
    elif err:
        out["notes"].append(err)

    # 2. What getting out of this area costs, if they said where they need
    #    to be. A town is cheaper for a reason, and the reason is usually
    #    that it is further away. Leaving the commute out of the ceiling
    #    overstates what someone can afford in precisely the places they
    #    choose because the rent looked manageable.
    where = zip_code or (("%s, %s" % (city, state)).strip(", "))
    destination = (b.get("destination") or "").strip()
    mode = (b.get("commute_mode") or "drive").lower()
    commute_monthly = float(b.get("commute") or 0)
    if destination and where:
        data, rerr = route_between(where, destination, mode)
        if data:
            commute_monthly = commute_cost(data["miles"], mode)
            out["commute"] = {
                "from": where,
                "to": destination,
                "mode": mode,
                "miles": data["miles"],
                "minutes": data["minutes"],
                "hours_per_month": round(data["minutes"] * 2 * COMMUTE_DAYS / 60, 1),
                "cost": commute_monthly,
            }
        elif rerr and rerr != "no_key":
            out["notes"].append(
                "Could not work out the commute from there. Check the spelling.")

    # 3. What this person can carry, once that is taken off.
    budget = affordable_rent(income, bedrooms, state, b.get("utilities_included"),
                             commute_monthly,
                             float(b.get("parking") or 0), share)
    if budget is not None:
        out["budget"] = {
            "gross": budget["gross"],
            "max_rent": budget["rent_only"],
            "per_person": round(budget["rent_only"] / share, 2),
            "utilities_assumed": budget["utilities_assumed"],
            "basis": "30 percent of income once utilities, commute and "
                     "insurance are taken off, because a listing price "
                     "contains none of those",
        }

    # 4. What is actually there.
    where = zip_code or (("%s, %s" % (city, state)).strip(", "))
    if where:
        bed_word = {0: "studio apartments", 1: "1 bedroom apartments",
                    2: "2 bedroom apartments", 3: "3 bedroom apartments"}.get(
                        bedrooms, "apartments")
        query = "%s in %s" % (bed_word, where)
        if baths:
            query += " %s bath" % baths
        places, perr = find_places(query)
        if places is not None:
            out["places"] = places
        elif perr:
            out["notes"].append(perr)

    # Verdict, computed here rather than guessed anywhere.
    if out["market"] and out["budget"]:
        typical = out["market"]["by_bedrooms"].get(bedrooms)
        if typical:
            # FMR is gross rent, so it goes against the gross ceiling.
            gap = round(out["budget"]["gross"] - typical, 2)
            out["verdict"] = {
                "typical": typical,
                "gap": gap,
                "fits": gap >= 0,
                "ceiling": out["budget"]["gross"],
                "bedrooms": bedrooms,
            }
    if max_rent:
        out["max_rent"] = float(max_rent)
    return jsonify(out)


def what_would_sharpen(parsed, form=None):
    """What is still missing, ranked by how much it could move the answer.

    An estimate built on six unknowns is not the same as one built on one,
    and a person deserves to know which. Each entry carries the dollar range
    it could swing the monthly total by, drawn from the same fee catalogue
    and utility tables the calculation uses, so the figures here and the
    figures there cannot drift apart.
    """
    p = parsed or {}
    f = form or {}
    beds = int(p.get("bedrooms") or f.get("bedrooms") or 1)
    b = min(max(beds, 0), 4)
    out = []

    def add(field, label, why, low, high, how):
        out.append({"field": field, "label": label, "why": why,
                    "low": round(low), "high": round(high), "how": how})

    # The two that gate the calculation entirely.
    if not f.get("income"):
        add("income", "Your monthly income after tax",
            "Without it there is no percentage, only a dollar figure.",
            0, 0, "Type it in. It stays in your browser.")
    if not (p.get("rent") or p.get("rent_min") or f.get("rent")):
        add("rent", "The rent",
            "Nothing can be worked out without it.",
            0, 0, "Screenshot the pricing section, or type it in.")

    # State drives the electricity price, and the spread across states is wide.
    if not (p.get("state") or f.get("state")):
        cheap = min(CENTS_PER_KWH.values())
        dear = max(CENTS_PER_KWH.values())
        kwh = KWH_BY_BEDROOMS[b]
        add("state", "Which state this is in",
            "Electricity costs %.0f cents a kWh in the cheapest state and "
            "%.0f in the dearest. That is a real spread on the same apartment."
            % (cheap, dear),
            kwh * cheap / 100, kwh * dear / 100,
            "Two letters. It is usually in the listing address.")

    # Utilities. Silence here is the single biggest unknown.
    if not p.get("utilities_stated"):
        full, _ = utilities_for(b, [], (p.get("state") or f.get("state")))
        add("utilities", "Which utilities the landlord covers",
            "The listing said nothing, so we have assumed you pay all of them.",
            0, full,
            "Ask the leasing office, then tick the boxes.")

    # Commute. People consistently forget this one entirely.
    if not f.get("commute_miles"):
        add("commute", "How far you need to travel",
            "Driving fifteen miles each way costs more than many of the fees "
            "people do worry about.",
            0, round(20 * 2 * COMMUTE_DAYS * CENTS_PER_MILE),
            "Put in the listing address and where you need to be, and it "
            "looks up the real time and distance.")

    # Fees the listing gestured at without a number.
    for fid in (p.get("fees_without_amounts") or []):
        fee = CATALOG_BY_ID.get(fid)
        if not fee:
            continue
        monthly = "month" in (fee.get("unit") or "")
        add("fee:" + fid, fee["name"],
            "The listing has a section for this but never gives a price.",
            fee["low"] if monthly else 0,
            fee["high"] if monthly else round(fee["high"] / 12),
            "Ask for the figure in writing before you apply.")

    # Named but unspecified. The listing told you a category and withheld
    # the only part that affects the bill.
    seen = set(p.get("unit_features") or [])
    for kind in (p.get("ambiguous_features") or []):
        spec = AMBIGUOUS_FEATURES.get(kind)
        if not spec:
            continue
        if spec["cheapest"] in seen or spec["dearest"] in seen:
            continue        # a photo already settled it
        worth = ambiguity_cost(kind, beds, p.get("state") or f.get("state"))
        if not worth or worth < 3:
            continue
        add("ambiguous:" + kind, spec["label"], spec["why"], 0, worth,
            spec["how"])

    # Photos. Only worth suggesting if they gave us none of the unit.
    if not (p.get("unit_features") or []):
        kwh = KWH_BY_BEDROOMS[b]
        cents = CENTS_PER_KWH.get((p.get("state") or f.get("state") or ""), US_AVG_CENTS)
        base = kwh * cents / 100
        add("photos", "A screenshot of the apartment photos",
            "Window air conditioning, electric baseboard heaters and old "
            "windows all change the power bill, and they are visible.",
            round(base * 0.88 - base), round(base * 1.35 - base),
            "Add a screenshot showing the rooms, not just the price.")

    # Biggest possible swing first, but the two gating items stay on top.
    gating = [o for o in out if o["field"] in ("income", "rent")]
    rest = sorted((o for o in out if o["field"] not in ("income", "rent")),
                  key=lambda o: -(o["high"] - o["low"]))
    ranked = gating + rest
    swing = sum(o["high"] - o["low"] for o in rest)
    return {"items": ranked[:6], "possible_swing": round(swing)}


@app.route("/api/sharpen", methods=["POST"])
def api_sharpen():
    b = request.json or {}
    return jsonify(what_would_sharpen(b.get("parsed"), b.get("form")))


SHORTLIST_PROMPT = """Someone is choosing between places to live. Below are
their options with figures that have ALREADY been computed and are final.

Write 3 or 4 short sentences naming the trade-off they are actually making.
Not a summary of the table, which they can read. The thing the table does not
say out loud.

Look for: a place that is cheapest on the listing but not cheapest to live
in, a place whose gap between advertised and true cost is unusually wide, and
whether any option is actually within the 30 percent guideline.

Hard rules:
- Never state a number that is not in the data below, and use whole dollars.
- Never recalculate anything.
- Do not tell them which to pick. Name the trade-off and stop.
- Plain language, short sentences, no jargon.

OPTIONS:
"""


@app.route("/api/shortlist", methods=["POST"])
def api_shortlist():
    """Reasoning across several places at once.

    This is the one thing here that a general chat assistant cannot do well,
    and the reason is not the writing: it is that the figures were produced
    by the same code applied identically to every option. Without that, any
    comparison is comparing differently-made numbers.

    The shortlist lives in the browser. It is only sent here when the person
    asks for this, and it carries no income, no address and no identifier.
    """
    places = (request.json or {}).get("places") or []
    if len(places) < 2:
        return jsonify(error="Save at least two places first."), 400
    if client is None:
        return jsonify(verdict=None, reason="Model not configured.")

    safe = []
    for p in places[:8]:
        safe.append({
            "name": str(p.get("name") or "Unnamed")[:60],
            "advertised_rent": round(float(p.get("advertised") or 0)),
            "true_monthly_cost": round(float(p.get("total") or 0)),
            "hidden_per_month": round(float(p.get("hidden") or 0)),
            "share_of_income_percent": round(float(p.get("burden") or 0) * 100),
        })
    try:
        resp = client.models.generate_content(
            model=MODEL_NAME,
            contents=SHORTLIST_PROMPT + json.dumps(safe, indent=1))
        return jsonify(verdict=(resp.text or "").strip() or None)
    except Exception as e:
        return jsonify(verdict=None, reason=str(e)[:200])


@app.route("/api/parse", methods=["POST"])
def api_parse():
    body = request.json or {}
    text = (body.get("text") or "").strip()
    url = (body.get("url") or "").strip()
    images = body.get("images") or []
    if body.get("image"):  # older clients sent a single image
        images = [{"data": body["image"], "mime": body.get("mime", "image/png")}]

    note = None
    if url and not images:
        # Let the model fetch it first. It reaches pages we cannot.
        data, err = parse_listing_from_url(url)
        if data:
            data["from_url"] = True
            cache_put(_cache_key("url:" + url, []), data)
            return jsonify(
                parsed=data, source="model", from_url=True,
                notes=((data.get("notes") or "") +
                       " Read from the link rather than a screenshot, so the "
                       "price may be a cached version. Check it against the "
                       "live page.").strip())

    if url:
        fetched, err = fetch_listing(url)
        if fetched:
            text = ((text or "") + "\n" + fetched).strip()
        else:
            note = err
            if not text and not images:
                return jsonify(parsed={}, source="blocked", notes=err)

    if not text and not images:
        return jsonify(error="Give us a screenshot, a link, or the text."), 400

    out = parse_listing(text=text or None, images=images)
    if note:
        out["notes"] = (note + " " + (out.get("notes") or "")).strip()
    return jsonify(out)


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    try:
        payload = request.json or {}
        result = analyze(payload)
        result["explanation"] = explain(result)

        # Privacy: we log that an analysis happened and the anonymous shape of
        # the result, never the income, never the rent, never anything that
        # could identify the user or the unit.
        db.collection("usage").add({
            "at": firestore.SERVER_TIMESTAMP,
            "burden_bucket": result["level"],
            "model_used": model_status == "ready",
        })
        return jsonify(result)
    except Exception as e:
        return jsonify(error=str(e), trace=traceback.format_exc()[-500:]), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
