# Two minute video. Shooting script.

**Hard rules**

1. Open the live app five minutes before recording so Cloud Run is warm. A
   cold start on camera costs you ten seconds you do not have.
2. Model calls take ten to twenty seconds. **Never show the wait.** Record the
   click, stop, record the result, cut the two together. Nobody is fooled and
   nobody cares.
3. Have both demos set up in two browser tabs before you start.
4. Narration below is 248 words. At a clear pace that is about 100 seconds,
   leaving 20 for the moments where the picture should carry it.
5. Say "AI for Housing", "Cloud Run" and "Vertex AI" out loud. There is no
   Q and A at this hackathon, so anything you do not say is a thing the
   judges never learn.

---

## 0:00 to 0:14  The problem

**Screen:** the apartments.com Chase listing. Cursor circles the price.

> Every rental listing shows you one number. That number is not what you pay.
> Utilities, parking, trash fees, the cost of getting to work. None of it is
> in the price on the sign.
>
> I'm a freshman. Next year I sign my first lease, and I had no idea what half
> of those words meant.

---

## 0:14 to 0:20  What it is

**Screen:** cut to the app's hero. The 1,150 to 1,388 arithmetic is on screen.

> This is True Cost of Rent. AI for Housing.

*Let the hero numbers sit for two seconds. Say nothing.*

---

## 0:20 to 1:05  Demo one. Check a place

**Screen:** drag six screenshots in. Paste the listing link. Click Read
everything.

> I give it screenshots of a listing and the link. Gemini on Vertex AI reads
> all of it as one listing and fills the form in.

**CUT. Result is on screen.**

**Screen:** scroll to the per bed banner. Hold two seconds.

> It caught something I missed. This place is priced per bed. That 894 is one
> room in a four bedroom unit, not the apartment.

**Screen:** scroll to the burden figure. 40 percent, Cost burdened badge.

> The listing says 36 percent of my income. It actually costs 40.

**Screen:** scroll to "Ask about these before you sign". Point at the amber
"Mentioned, but no price given" badge on Parking.

> And it tells me what to ask. Parking has a heading on this listing with no
> price under it, which is worse than not mentioning it, because it looks like
> an answer.

**Screen:** the sharpen panel, on the heating line.

> The listing says heating but not what kind. That one unknown is worth
> sixty-nine dollars a month.

---

## 1:05 to 1:30  Demo two. Explore an area

**Screen:** second tab, Explore an area. Type into the plain English box:

```
I make 2500 a month after tax and I need a 2 bedroom in Blacksburg
near Virginia Tech
```

Click the button.

> If you don't know where to start, just say it in English.

**CUT. Parsed fields are filled in.**

**Screen:** the HUD row and the ceiling.

> It pulls what the area actually costs from HUD, works out what I can carry,
> and shows me real buildings from the Places API. It will not put a price on
> a specific building, because nobody publishes that and making one up would
> be worse than useless.

---

## 1:30 to 1:50  Why you can trust the number

**Screen:** the utility detail table open, EIA label visible. Then flash the
terminal with `Ran 96 tests ... OK`.

> Every figure is computed in Python. The model reads and explains. It never
> produces a number that reaches a calculation, which is why there are
> ninety-six tests.
>
> Across ten real listings, the advertised price understated what it costs to
> live there by twenty-three percent. Seventeen hundred dollars over a lease.

---

## 1:50 to 2:00  The limit, and the close

**Screen:** back on the result, the "estimate" label next to utilities.

> It can't see inside the walls. Where it's guessing, it says so.
>
> Next is reading the lease, not just the listing.

---

## Total narration: 248 words

## If you are over time, cut in this order

1. The second half of the Explore narration, from "It will not put a price".
2. The sharpen panel line about heating.
3. The intro's second sentence, "Utilities, parking, trash fees...".

**Do not cut:** the opening problem, the per bed catch, the 23 percent, or the
"it can't see inside the walls" line. Those four are what a judge remembers.

## What to have open before you hit record

- Tab 1: the app, warm, with the six Chase screenshots already in a folder
  you can drag from
- Tab 2: the app, on the Explore tab, empty
- Tab 3: the GitHub repo, in case you want a two second shot of it
- A terminal with the test suite already run, output on screen
