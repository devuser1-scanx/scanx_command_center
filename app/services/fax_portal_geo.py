from __future__ import annotations

import csv
import math
import re
from functools import lru_cache
from pathlib import Path

from app.schemas.fax_portal import ClinicDistance

# Which clinic is nearest to a patient, from the two addresses' ZIP codes.
#
# Everything happens inside this server: each ZIP code is looked up in the US
# Census Bureau's public ZIP-code location table (app/data/us_zip_centroids.csv)
# and the straight-line distance between the two points is worked out. The
# patient's address is never sent to a map or geocoding service.
#
# A ZIP code's centre is a few miles off the real address, which is plenty to
# tell clinics apart but not to promise driving distance. Never log addresses.

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "us_zip_centroids.csv"

EARTH_RADIUS_MILES = 3958.8

# Farther than this from every clinic: no suggestion (the patient is probably
# outside the area we serve, so a person should choose).
MAX_SUGGEST_MILES = 75.0

# A ZIP code counts only at the end of the address ("... Dallas, TX 75209",
# "... 75209-1234", optionally followed by "USA"). A bare 5-digit number
# elsewhere is a street number, not a ZIP code.
_ZIP_AT_END = re.compile(
    r"(?:^|[\s,])(\d{5})(?:-\d{4})?(?:[\s,]+(?:USA|US|United States))?\s*$", re.IGNORECASE
)


@lru_cache(maxsize=1)
def _centroids() -> dict[str, tuple[float, float]]:
    with DATA_FILE.open(encoding="utf-8", newline="") as f:
        return {row["zip"]: (float(row["lat"]), float(row["lon"])) for row in csv.DictReader(f)}


def zip_of(address: str | None) -> str | None:
    """The 5-digit ZIP code an address ends with, or None (masked, missing)."""
    match = _ZIP_AT_END.search((address or "").strip())

    return match.group(1) if match else None


def locate(address: str | None) -> tuple[float, float] | None:
    """(latitude, longitude) of the centre of the address's ZIP code."""
    zip_code = zip_of(address)

    return _centroids().get(zip_code) if zip_code else None


def miles_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Straight-line distance in miles (haversine)."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )

    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(h))


def rank_clinics(
    patient_address: str | None,
    clinics: list[tuple[int, str, str | None]],
) -> list[ClinicDistance] | None:
    """Every clinic with its distance from the patient, nearest first.

    `clinics` is (calendar id, name, clinic address). Returns None unless the
    patient's ZIP code and every clinic's ZIP code can be located: ranking only
    some of the clinics could name the wrong one as nearest.
    """
    patient = locate(patient_address)

    if patient is None or not clinics:
        return None

    ranked: list[ClinicDistance] = []

    for calendar_id, name, address in clinics:
        point = locate(address)

        if point is None:
            return None

        ranked.append(
            ClinicDistance(
                acuity_calendar_id=calendar_id,
                name=name,
                miles=round(miles_between(patient, point), 1),
            )
        )

    return sorted(ranked, key=lambda c: (c.miles, c.name))
