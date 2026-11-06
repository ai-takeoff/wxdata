"""Build site/airports.json from OurAirports (public domain) for location search.

Usage: python tools/build_airports.py site/airports.json
Keeps airports, heliports and seaplane bases inside the viewer domain.
"""
import csv
import io
import json
import sys

import requests

sys.path.insert(0, ".")
from pipeline.config import DOMAIN  # noqa: E402

URL = "https://raw.githubusercontent.com/davidmegginson/ourairports-data/main/airports.csv"
TYPES = {"large_airport": "L", "medium_airport": "M", "small_airport": "S",
         "heliport": "H", "seaplane_base": "W"}


def main(out):
    r = requests.get(URL, timeout=60)
    r.raise_for_status()
    rows = []
    for a in csv.DictReader(io.StringIO(r.text)):
        t = TYPES.get(a["type"])
        if not t:
            continue
        try:
            lat, lon = float(a["latitude_deg"]), float(a["longitude_deg"])
        except ValueError:
            continue
        if not (DOMAIN["lat0"] <= lat <= DOMAIN["lat1"] and DOMAIN["lon0"] <= lon <= DOMAIN["lon1"]):
            continue
        ident = a["ident"].strip()
        icao = (a.get("icao_code") or a.get("gps_code") or "").strip()
        local = (a.get("local_code") or "").strip()
        iata = (a.get("iata_code") or "").strip()
        codes = []
        for c in (icao, ident, local, iata):
            if c and c not in codes:
                codes.append(c)
        rows.append([codes[0] if codes else ident, a["name"].strip(), round(lat, 4), round(lon, 4), t,
                     " ".join(codes[1:])])
    # larger airports first so they win short searches
    order = "LMSHW"
    rows.sort(key=lambda x: (order.index(x[4]), x[0]))
    with open(out, "w") as f:
        json.dump(rows, f, separators=(",", ":"), ensure_ascii=False)
    print(f"wrote {len(rows)} locations to {out}")


if __name__ == "__main__":
    main(sys.argv[1])
