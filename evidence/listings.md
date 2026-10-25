# Worked examples

Real listings run through the app. Collected for the README impact section.
Every row uses the same monthly after tax income so the burden figures are
comparable with each other.

| # | listing | state | beds | advertised | true cost | gap | adv burden | true burden |
|---|---------|-------|------|-----------:|----------:|----:|-----------:|------------:|

## 1. Chase Apartments, Blacksburg VA
url: https://www.apartments.com/chase-apartments-blacksburg-va/1cxxjw6/
address: 421-461 Roanoke St E, Blacksburg VA
read from: 6 screenshots + the link, merged
pricing: per bed. $894 a bed in a 4 bedroom unit, $3,576 whole unit
income used: $2,500 a month after tax
commute: drive, 0.7 miles to Virginia Tech

  rent, their share     894
  utilities              70
  parking                10
  commute                 6
  renters insurance      15
  true monthly cost     995

advertised                894
gap                       101 a month      1,212 a year
burden advertised          36 percent
burden true                40 percent
verdict                    cost burdened

the band where the listing lies
  advertised crosses 30 pct at   894 / 0.30 = 2,980
  true cost crosses 30 pct at    995 / 0.30 = 3,317
  so between 2,980 and 3,317 a month it passes on the advertised
  price and fails on what it costs. 337 a month of income wide.

caught, none of which the listing stated outright
  priced per bed, not per apartment
  parking has a section with no price in it
  heating type never stated, worth up to $69 a month
  air conditioning type never stated, worth up to $15 a month
  "subject to change without notice" in the lease wording
  total unresolved spread, up to $394 a month

---

## Market sweep, nine real Blacksburg listings

Rents retrieved from public listing sites on 25 Oct 2026. Each run through
analyze() with only what a listing states: rent, bedrooms, state. No
utilities marked as included, because none of them said so. Income 2,500 a
month after tax, roommates equal to bedrooms.

| listing                        | adv  | share | true | gap | adv % | true % |
|--------------------------------|-----:|------:|-----:|----:|------:|-------:|
| 1018 Montgomery St NW Unit B   | 1000 |   500 |  646 | 146 |   20  |   26   |
| 318 Willard Dr SE Unit 02      | 1050 |   525 |  671 | 146 |   21  |   27   |
| 300 Heights Dr Unit B-12       | 1150 |   575 |  721 | 146 |   23  |   29   |
| 109 Northview Dr Unit 2        | 1150 |   575 |  721 | 146 |   23  |   29   |
| 302 Broce Dr                   | 1200 |   600 |  746 | 146 |   24  |   30   |
| Park37, 1222 Patrick Henry Dr  | 1225 |   612 |  759 | 146 |   24  |   30   |
| 613 Dehart St                  | 1250 |   625 |  771 | 146 |   25  |   31   |
| 207 Wharton St                 | 1350 |   675 |  821 | 146 |   27  |   33   |
| Windsor Hills, 200 Hampton Ct  | 1581 |   790 |  937 | 146 |   32  |   37   |

three of nine pass the 30 percent test on advertised price and fail on true
average gap $146 a month, $1,757 a year

The gap is the same figure nine times, and that is the finding rather than a
fault. With only rent and bedrooms stated, the hidden cost is utilities and
insurance, which turn on bedroom count and state, not on rent. Nine listings,
nine different prices, and not one of them says anything about utilities.

## The electricity data is load bearing

Same 2 bedroom, same $1,200 rent, same everything. Only the state changes.
Cents per kWh is the real EIA Table 5A 2024 figure.

| state | c/kWh | utilities | true cost | burden |
|-------|------:|----------:|----------:|-------:|
| WA    | 11.90 |       123 |       738 |   30 % |
| VA    | 14.41 |       131 |       746 |   30 % |
| NY    | 24.43 |       166 |       782 |   31 % |
| CA    | 31.97 |       193 |       808 |   32 % |
| HI    | 42.86 |       231 |       846 |   34 % |

The same listing costs $108 a month more in Hawaii than Washington, $1,300 a
year, from the price of electricity alone.
