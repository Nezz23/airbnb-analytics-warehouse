"""Synthetic Inside Airbnb-shaped snapshots for CI: small, deterministic, no personal data.

Scenario across four snapshot dates:
  - 2026-01-01  base: 1,200 listings
  - 2026-02-01  listing 1 changes room type, listing 2 gets a registration, listing 3 disappears
  - 2026-02-08  partial extract (100 listings) -> must be quarantined by the volume contract
  - 2026-03-01  listing 3 returns, listing 5 (unlicensed) drops its minimum stay below 30 nights
"""

from __future__ import annotations

import csv
import gzip
import random
from datetime import date, timedelta
from pathlib import Path

BOROUGHS = {
    "Manhattan": ["Harlem", "Chelsea"],
    "Brooklyn": ["Williamsburg", "Bushwick"],
    "Queens": ["Astoria"],
    "Bronx": ["Mott Haven"],
    "Staten Island": ["St. George"],
}
LISTING_COLS = [
    "id",
    "last_scraped",
    "name",
    "host_id",
    "host_since",
    "host_is_superhost",
    "calculated_host_listings_count",
    "neighbourhood_cleansed",
    "neighbourhood_group_cleansed",
    "latitude",
    "longitude",
    "property_type",
    "room_type",
    "accommodates",
    "bedrooms",
    "beds",
    "price",
    "price_quote_checkin_date",
    "price_quote_checkout_date",
    "minimum_nights",
    "maximum_nights",
    "availability_30",
    "availability_90",
    "availability_365",
    "number_of_reviews",
    "number_of_reviews_ltm",
    "review_scores_rating",
    "estimated_occupancy_l365d",
    "instant_bookable",
    "license",
]
N_LISTINGS = 1200


def _listing(i: int, snap: date, rng: random.Random) -> dict:
    borough = list(BOROUGHS)[i % len(BOROUGHS)]
    license_ = ["", "", "", "Exempt", f"OSE-STRREG-{i:07d}"][i % 5]
    return {
        "id": i,
        "last_scraped": snap,
        "name": f"Listing {i}",
        "host_id": 1000 + i % 400,
        "host_since": "2020-01-01",
        "host_is_superhost": "t" if i % 7 == 0 else "f",
        "calculated_host_listings_count": 1,
        "neighbourhood_cleansed": BOROUGHS[borough][i % len(BOROUGHS[borough])],
        "neighbourhood_group_cleansed": borough,
        "latitude": f"{40.6 + (i % 30) / 100:.5f}",
        "longitude": f"{-73.95 - (i % 20) / 100:.5f}",
        "property_type": "Entire rental unit",
        "room_type": "Entire home/apt" if i % 3 else "Private room",
        "accommodates": 2 + i % 4,
        "bedrooms": 1 + i % 3,
        "beds": 1 + i % 3,
        "price": f"${rng.randint(60, 900):,}.00" if i % 4 else "",
        "price_quote_checkin_date": snap,
        "price_quote_checkout_date": snap + timedelta(days=30),
        "minimum_nights": 30 if license_ == "" else 2,
        "maximum_nights": 365,
        "availability_30": rng.randint(0, 30),
        "availability_90": rng.randint(0, 90),
        "availability_365": rng.randint(0, 365),
        "number_of_reviews": i % 50,
        "number_of_reviews_ltm": i % 12,
        "review_scores_rating": "4.80",
        "estimated_occupancy_l365d": 0,
        "instant_bookable": "t" if i % 2 else "f",
        "license": license_,
    }


def _write(path: Path, header: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        w.writerows(rows)


def generate(raw: Path) -> None:
    rng = random.Random(42)
    snaps = [date(2026, 1, 1), date(2026, 2, 1), date(2026, 2, 8), date(2026, 3, 1)]
    for snap in snaps:
        ids = range(1, N_LISTINGS + 1) if snap != date(2026, 2, 8) else range(1, 101)
        listings = [_listing(i, snap, rng) for i in ids]
        by_id = {row["id"]: row for row in listings}
        if snap >= date(2026, 2, 1) and 1 in by_id:
            by_id[1]["room_type"] = "Private room" if by_id[1]["room_type"] != "Private room" else "Entire home/apt"
            by_id[2]["license"] = "ose-strreg-0000002"  # lower case on purpose: silver normalises it
        if snap == date(2026, 2, 1):
            listings = [r for r in listings if r["id"] != 3]
        if snap == date(2026, 3, 1):
            by_id[5]["minimum_nights"] = 3  # unlicensed 30+ night listing goes short-term
        _write(raw / snap.isoformat() / "listings.csv.gz", LISTING_COLS, listings)

        calendar = [
            {
                "listing_id": r["id"],
                "date": snap + timedelta(days=d),
                "available": "t" if (r["id"] + d) % 3 else "f",
                "minimum_nights": r["minimum_nights"],
                "maximum_nights": 365,
            }
            for r in listings
            for d in range(0, 365, 3)
        ]
        _write(
            raw / snap.isoformat() / "calendar.csv.gz",
            ["listing_id", "date", "available", "minimum_nights", "maximum_nights"],
            calendar,
        )

        reviews = [
            {
                "listing_id": r["id"],
                "id": r["id"] * 100 + k,
                "date": snap - timedelta(days=k * 9),
                "reviewer_id": 7,
                "reviewer_name": "x",
                "comments": "x",
            }
            for r in listings
            for k in range(r["id"] % 30)
        ]
        _write(
            raw / snap.isoformat() / "reviews.csv.gz",
            ["listing_id", "id", "date", "reviewer_id", "reviewer_name", "comments"],
            reviews,
        )


if __name__ == "__main__":
    import sys

    generate(Path(sys.argv[1]))
