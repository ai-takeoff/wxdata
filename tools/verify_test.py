"""Check pipeline output (from make_test_data.py input) against the analytic fields."""
import gzip
import json
import sys

import numpy as np

sys.path.insert(0, ".")
from pipeline.process import dequantize, decode_map  # noqa: E402

data = sys.argv[1]
run = sys.argv[2]
KT = 1.943844
ok = True


def _fast_decode(d):
    # vectorised inverse of the gradient predictor: 2-D cumulative sum mod 256
    return (np.cumsum(np.cumsum(d.astype(np.int64), axis=0), axis=1) % 256).astype(np.uint8)


def check(name, got, exp, tol, mask=None):
    global ok
    m = np.isfinite(got) & np.isfinite(exp)
    if mask is not None:
        m &= mask
    if m.sum() == 0:
        print(f"  FAIL {name}: no valid points"); ok = False; return
    err = np.abs(got[m] - exp[m])
    status = "ok  " if np.percentile(err, 99) <= tol else "FAIL"
    if status == "FAIL":
        ok = False
    print(f"  {status} {name}: p99 err {np.percentile(err, 99):.3f} max {err.max():.3f} (tol {tol}) n={m.sum()}")


for model in ("rrfs", "gfs", "rap"):
    meta = json.load(open(f"{data}/{model}/{run}/meta.json"))
    g = meta["map_grid"]
    lats = np.arange(g["lat0"], g["lat1"] + 1e-9, g["res"])[::-1]   # PNG row 0 = north
    lons = np.arange(g["lon0"], g["lon1"] + 1e-9, g["res"])
    LON, LAT = np.meshgrid(lons, lats)
    F = meta["fields"]
    print(f"{model}: available {meta['available']} ceil_ref={meta['ceil_ref']} present={len(meta['present'])} fields")

    def load(f, h):
        raw = gzip.open(f"{data}/{model}/{run}/maps/f{h:03d}/{f}.bin.gz").read()
        d = np.frombuffer(raw, np.uint8).reshape(len(lats), len(lons))
        q = decode_map(d) if f == "t2m" else _fast_decode(d)
        return dequantize(q, F[f])

    h = 9
    check("t2m", load("t2m", h), 288 - 0.8 * (LAT - 35) - 273.15, 0.5)
    check("u_500", load("u_500", h), 30 * np.cos(np.radians(3 * (LAT - 40))) * KT, 2.5)
    check("v_500", load("v_500", h), 10 * np.sin(np.radians(2 * (LON + 95))) * KT, 2.5)
    check("u10", load("u10", h), 0.3 * 30 * np.cos(np.radians(3 * (LAT - 40))) * KT, 1.5)
    check("mslp", load("mslp", h), (101325 + 1500 * np.sin(np.radians(LON * 3))) / 100, 1.5)
    check("ceil(lat>41)", load("ceil", h), np.full_like(LAT, 800 * 3.28084), 200, LAT > 41)
    check("ceil none(lat<39)", load("ceil", h), np.full_like(LAT, 20000.0), 1, LAT < 39)
    away = np.abs(LON + 90) > 1
    check("apcp f009", load("apcp", h), np.where(LON > -90, 9.0, 0.0), 0.6, away)
    check("apcp f006", load("apcp", 6), np.where(LON > -90, 6.0, 0.0), 0.6, away)
    check("vis", load("vis", h), np.where(LAT > 45, 3000 / 1609.344, 10.0), 0.3, np.abs(LAT - 45) > 1)

    # point tile: nearest point to Morristown NJ (40.80, -74.42) at f009
    pg = meta["point_grid"]
    iy = round((40.80 - pg["lat0"]) / pg["res"]); ix = round((-74.42 - pg["lon0"]) / pg["res"])
    ty, tx, T = iy // pg["tile"], ix // pg["tile"], pg["tile"]
    raw = gzip.open(f"{data}/{model}/{run}/points/{ty}_{tx}.bin.gz").read()
    arr = np.frombuffer(raw, np.uint8).reshape(len(meta["hours"]), len(meta["point_fields"]), T, T)
    hi = meta["hours"].index(9)
    lat_p, lon_p = pg["lat0"] + iy * pg["res"], pg["lon0"] + ix * pg["res"]
    fi = meta["point_fields"].index("t2m")
    v = dequantize(arr[hi, fi, iy % T, ix % T:ix % T + 1], F["t2m"])[0]
    exp = 288 - 0.8 * (lat_p - 35) - 273.15
    good = abs(v - exp) < 0.5
    ok &= good
    print(f"  {'ok  ' if good else 'FAIL'} point t2m at {lat_p},{lon_p}: {v:.2f} vs {exp:.2f}")

print("ALL OK" if ok else "SOME CHECKS FAILED")
