"""Daily cheapest-fare check: Copenhagen -> New York and back (7-day trips).

Uses SerpApi's Google Flights engine. No third-party packages needed.
Each run searches a rotating slice of the date window to stay inside the free tier.
"""
import csv
import json
import os
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

API_KEY = os.environ["SERPAPI_KEY"]

# --- Settings -------------------------------------------------------------
YEAR = 2027                      # change if the trip is in another year
FIRST_DEPARTURE = date(YEAR, 4, 21)
LAST_RETURN = date(YEAR, 5, 30)
TRIP_DAYS = 7
ORIGIN = "CPH"
DESTINATION = "JFK,EWR,LGA"      # all three New York airports
CURRENCY = "DKK"
MIN_LAYOVER = 60                 # minutes (avoids tight connections)
MAX_LAYOVER = 180                # minutes (your 3-hour limit)
PAIRS_PER_DAY = 6                # searches per run
MAX_VERIFY = 2                   # extra calls to check the return leg
# Total calls per run is at most PAIRS_PER_DAY + MAX_VERIFY.
# ---------------------------------------------------------------------------

LOG_DIR = Path("logs")
DAILY_LOG = LOG_DIR / "daily_cheapest.csv"
PAIR_LOG = LOG_DIR / "pair_prices.csv"


def all_pairs():
    pairs, d = [], FIRST_DEPARTURE
    while d + timedelta(days=TRIP_DAYS) <= LAST_RETURN:
        pairs.append((d, d + timedelta(days=TRIP_DAYS)))
        d += timedelta(days=1)
    return pairs


def todays_pairs():
    """A different contiguous slice each day, wrapping around the window."""
    pairs = all_pairs()
    start = (date.today().toordinal() * PAIRS_PER_DAY) % len(pairs)
    return [pairs[(start + i) % len(pairs)] for i in range(PAIRS_PER_DAY)]


def search(params):
    query = urllib.parse.urlencode({
        "engine": "google_flights",
        "api_key": API_KEY,
        "hl": "en",
        "currency": CURRENCY,
        **params,
    })
    with urllib.request.urlopen("https://serpapi.com/search.json?" + query, timeout=60) as r:
        return json.load(r)


def options(data):
    return data.get("best_flights", []) + data.get("other_flights", [])


def acceptable(opt):
    """Keep only priced options with 1-3h layovers and no separate-ticket flag."""
    if "price" not in opt:
        return False
    text = json.dumps(opt).lower()
    if "separate ticket" in text or "self-transfer" in text or "self transfer" in text:
        return False
    return all(MIN_LAYOVER <= l.get("duration", 0) <= MAX_LAYOVER
               for l in opt.get("layovers", []))


def airlines(opt):
    return "/".join(sorted({f.get("airline", "?") for f in opt.get("flights", [])}))


def layovers(opt):
    return ", ".join(f'{l.get("id", l.get("name", "?"))} {l.get("duration", 0)}m'
                     for l in opt.get("layovers", [])) or "nonstop"


def flights_link(data, out, ret):
    """Link to the Google Flights search for these dates."""
    url = data.get("search_metadata", {}).get("google_flights_url")
    if url:
        return url
    q = f"Flights from CPH to New York on {out} through {ret}"
    return "https://www.google.com/travel/flights?q=" + urllib.parse.quote(q)


def append_row(path, header, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerow(row)


def verify_return(base, outbound_opt):
    """Second call: look at return flights for the chosen outbound."""
    token = outbound_opt.get("departure_token")
    if not token:
        return None
    data = search({**base, "departure_token": token})
    good = [o for o in options(data) if acceptable(o)]
    return min(good, key=lambda o: o["price"]) if good else None


def main():
    today = date.today().isoformat()
    candidates = []

    for out, ret in todays_pairs():
        base = {
            "departure_id": ORIGIN,
            "arrival_id": DESTINATION,
            "outbound_date": out.isoformat(),
            "return_date": ret.isoformat(),
            "type": 1,  # round trip
            "layover_duration": f"{MIN_LAYOVER},{MAX_LAYOVER}",
        }
        try:
            data = search(base)
        except Exception as e:
            print(f"Search failed for {out}: {e}")
            continue
        good = [o for o in options(data) if acceptable(o)]
        if not good:
            print(f"No acceptable options for {out} -> {ret}")
            continue
        best = min(good, key=lambda o: o["price"])
        link = flights_link(data, out, ret)
        candidates.append((best["price"], out, ret, best, base, link))
        append_row(PAIR_LOG,
                   ["checked_on", "outbound", "return", "price", "airlines",
                    "outbound_layovers", "link"],
                   [today, out, ret, best["price"], airlines(best), layovers(best), link])

    if not candidates:
        print("Nothing found today.")
        return

    candidates.sort(key=lambda c: c[0])
    header = ["checked_on", "price", "currency", "outbound", "return",
              "airlines", "outbound_layovers", "return_layovers", "return_verified", "link"]

    for price, out, ret, best, base, link in candidates[:MAX_VERIFY]:
        try:
            ret_opt = verify_return(base, best)
        except Exception as e:
            print(f"Return check failed for {out}: {e}")
            continue
        if ret_opt:
            append_row(DAILY_LOG, header,
                       [today, ret_opt["price"], CURRENCY, out, ret,
                        airlines(best), layovers(best), layovers(ret_opt), "yes", link])
            return

    # No candidate passed the return-leg check; log the cheapest, flagged.
    price, out, ret, best, _, link = candidates[0]
    append_row(DAILY_LOG, header,
               [today, price, CURRENCY, out, ret,
                airlines(best), layovers(best), "", "no", link])


if __name__ == "__main__":
    main()
