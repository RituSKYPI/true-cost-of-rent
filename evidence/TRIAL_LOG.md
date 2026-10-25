# Trial log

Every number below is produced by calling the application's own `analyze()` function. Nothing is estimated by hand. The script that generates this file is `evidence/build_log.py`, so any figure here can be reproduced with `python3 evidence/build_log.py`.

Listings retrieved 25 October 2026. Rents change, so a figure checked later may differ from the one recorded here.


## Run 1. A listing read end to end by the app

The only run where the app did the reading as well as the arithmetic. Six screenshots and the listing URL were given to it and merged into one set of figures.

**Chase Apartments, 421-461 Roanoke St E, Blacksburg VA**  
Source: https://www.apartments.com/chase-apartments-blacksburg-va/1cxxjw6/  
Read from: 6 screenshots plus the link, merged.

Inputs the app arrived at after reading the listing:

```json
{
  "rent": 3576,
  "bedrooms": 4,
  "roommates": 4,
  "state": "VA",
  "income": 2500,
  "lease_months": 12,
  "parking": 10,
  "commute_miles": 0.7,
  "commute_mode": "drive",
  "utilities_included": [
    "trash",
    "internet"
  ]
}
```

The figures below are what the deployed application returned on 25 October 2026 with those six screenshots attached. Calling `analyze()` on the inputs alone returns $68 of utilities rather than $70, because the signals read out of the photographs only exist when real screenshots are present. The observed run is the evidence, so the observed figures are the ones recorded here.

| line | per month |
|------|----------:|
| rent share | $894 |
| utilities | $70 |
| parking | $10 |
| commute | $6 |
| insurance | $15 |
| **true monthly cost** | **$995** |

- advertised, their share: **$894**
- true monthly cost: **$995**
- difference: **$101 a month**, $1,212 a year
- **11.3 percent more than the listing price**
- burden: 36 percent advertised, 40 percent true

What the app caught that the listing did not state outright:

- priced per bed rather than per apartment, so the advertised $894 is one room in a four bedroom unit
- parking has a section on the listing with no price in it
- heating type never stated, worth up to $69 a month
- air conditioning type never stated, worth up to $15 a month
- the phrase "subject to change without notice" in the lease wording
- total unresolved spread, up to $394 a month

The band where the listing misleads:

```
advertised crosses 30 pct at  894 / 0.30 = 2,980
true cost crosses 30 pct at   995 / 0.30 = 3,317
```
Anyone earning between **$2,980 and $3,317** a month passes the 30 percent test on this listing's advertised price and fails it on what the place actually costs.


## Runs 2 to 10. Market sweep

Nine further Blacksburg listings. The app was given only what a listing states: advertised rent, bedroom count and state. No utilities were marked as covered, because none of these listings said any were. Income held at $2,500 a month after tax for every run, and roommates set equal to bedrooms. This is the floor, not the full picture: parking, fees and commute are all zero here because none were stated.

| # | listing | source | advertised | share | true cost | difference | percent |
|---|---------|--------|-----------:|------:|----------:|-----------:|--------:|
| 2 | [1018 Montgomery St NW Unit B, Blacksburg VA](https://www.redfin.com/city/2010/VA/Blacksburg/cheap-2-bedroom-apartment) | Redfin | $1,000 | $500 | $646 | $146 | 29.3% |
| 3 | [318 Willard Dr SE Unit 02, Blacksburg VA](https://www.redfin.com/neighborhood/761410/VA/Blacksburg/Downtown-Eastside/2-bedroom-apartments-for-rent) | Redfin | $1,050 | $525 | $671 | $146 | 27.9% |
| 4 | [300 Heights Dr Unit B-12, Blacksburg VA](https://www.redfin.com/neighborhood/761406/VA/Blacksburg/Apperson-Dickerson/2-bedroom-apartments-for-rent) | Redfin | $1,150 | $575 | $721 | $146 | 25.5% |
| 5 | [109 Northview Dr Unit 2, Blacksburg VA](https://www.redfin.com/neighborhood/761407/VA/Blacksburg/Kabrich-Crescent/2-bedroom-apartments-for-rent) | Redfin | $1,150 | $575 | $721 | $146 | 25.5% |
| 6 | [302 Broce Dr, Blacksburg VA](https://www.redfin.com/neighborhood/761407/VA/Blacksburg/Kabrich-Crescent/2-bedroom-apartments-for-rent) | Redfin | $1,200 | $600 | $746 | $146 | 24.4% |
| 7 | [Park37, 1222 Patrick Henry Dr, Blacksburg VA](https://www.redfin.com/neighborhood/761407/VA/Blacksburg/Kabrich-Crescent/2-bedroom-apartments-for-rent) | Redfin | $1,225 | $612 | $759 | $146 | 23.9% |
| 8 | [613 Dehart St, Blacksburg VA](https://edge.apartmentiq.io/blacksburg-va?page=3) | ApartmentIQ | $1,250 | $625 | $771 | $146 | 23.4% |
| 9 | [207 Wharton St, Blacksburg VA](https://edge.apartmentiq.io/blacksburg-va?page=3) | ApartmentIQ | $1,350 | $675 | $821 | $146 | 21.7% |
| 10 | [Windsor Hills, 200 Hampton Ct, Blacksburg VA](https://www.redfin.com/city/2010/VA/Blacksburg/cheap-2-bedroom-apartment/page-2) | Redfin | $1,581 | $790 | $937 | $146 | 18.5% |

3 of 9 pass the 30 percent test on their advertised price and fail it on true cost:

- Park37, 1222 Patrick Henry Dr, Blacksburg VA: 24 percent advertised, 30 percent true
- 613 Dehart St, Blacksburg VA: 25 percent advertised, 31 percent true
- 207 Wharton St, Blacksburg VA: 27 percent advertised, 33 percent true

The difference is the same dollar figure in all nine. That is the finding rather than a fault. When a listing states nothing beyond rent and bedrooms, the only costs left to work out are utilities and renters insurance, and those turn on bedroom count and state, not on rent. Nine listings, nine different prices, and not one of them says anything about utilities.


## Run 11. Does the electricity data actually move the answer

The same unit at the same rent, changed only by state. Cents per kWh is the published EIA Table 5A figure for 2024, which is what the application uses.

| state | cents per kWh | utilities | true cost | burden |
|-------|--------------:|----------:|----------:|-------:|
| WA | 11.90 | $123 | $738 | 30% |
| VA | 14.41 | $131 | $746 | 30% |
| NY | 24.43 | $166 | $782 | 31% |
| CA | 31.97 | $193 | $808 | 32% |
| HI | 42.86 | $231 | $846 | 34% |

The same listing costs **$108 a month more in HI than in WA**, $1,300 over a year, from the price of electricity alone. The state data is load bearing, not decorative.


## Overall

Across **10 listings**, comparing what the application works out against what the listing advertises:

| measure | value |
|---------|------:|
| mean difference | **23.1 percent** above the advertised price |
| median difference | 24.2 percent |
| smallest | 11.3 percent |
| largest | 29.3 percent |
| mean in dollars | $142 a month, $1,703 a year |

**A listing in this sample understates what it costs to live there by 23.1 percent on average.** On the figures above that is $142 a month, or $1,703 over a twelve month lease, that nobody put in the advertised price.

That average is a floor rather than a ceiling. Nine of the ten listings had no parking charge, no monthly fees, no move in fee and no commute, because those listings stated none. Where a listing did state them, in run 1, the application also found up to $394 a month of cost it could not resolve at all, because the listing never said what kind of heating the place has.

