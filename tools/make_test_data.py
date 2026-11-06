"""Generate synthetic model output in NOAA file/idx layouts for offline testing.

Fields are analytic functions of lat/lon so the pipeline output can be checked:
  earth-relative wind at every level:  u = 30*cos(3*(lat-40)deg), v = 10*sin(2*(lon+95)deg)  (m/s)
  2 m temperature: 288 - 0.8*(lat-35) K
  terrain: 2500*exp(-((lon+110)/8)^2) m
  ceiling: 800 m AGL where lat > 40, none elsewhere
  precip: 1 mm/h where lon > -90  (encoded in each model's bucket style)

Usage: python tools/make_test_data.py OUTDIR RUN(YYYYMMDDHH) [hours]
"""
import os
import sys
from datetime import datetime

import numpy as np
import eccodes as ec

LEVELS = [1000, 950, 925, 900, 850, 800, 750, 700, 600, 500, 400, 300, 250, 200]
STD = {1000: 110, 950: 540, 925: 760, 900: 990, 850: 1460, 800: 1950, 750: 2470,
       700: 3010, 600: 4210, 500: 5570, 400: 7180, 300: 9160, 250: 10360, 200: 11790}


def lambert_template(nx, ny, dx, la1, lo1, lov=265.0, latin=25.0):
    g = ec.codes_grib_new_from_samples("GRIB2")
    ec.codes_set(g, "gridDefinitionTemplateNumber", 30)
    for k, v in [("Nx", nx), ("Ny", ny), ("latitudeOfFirstGridPointInDegrees", la1),
                 ("longitudeOfFirstGridPointInDegrees", lo1), ("LaDInDegrees", latin),
                 ("LoVInDegrees", lov), ("Latin1InDegrees", latin), ("Latin2InDegrees", latin),
                 ("DxInMetres", dx), ("DyInMetres", dx), ("shapeOfTheEarth", 6)]:
        ec.codes_set(g, k, v)
    ec.codes_set_values(g, np.zeros(nx * ny))
    ec.codes_set(g, "uvRelativeToGrid", 1)
    return g


def latlon_template(res):
    g = ec.codes_grib_new_from_samples("regular_ll_sfc_grib2")
    ni, nj = int(360 / res), int(180 / res) + 1
    for k, v in [("Ni", ni), ("Nj", nj), ("latitudeOfFirstGridPointInDegrees", 90.0),
                 ("longitudeOfFirstGridPointInDegrees", 0.0),
                 ("latitudeOfLastGridPointInDegrees", -90.0),
                 ("longitudeOfLastGridPointInDegrees", 360.0 - res),
                 ("iDirectionIncrementInDegrees", res), ("jDirectionIncrementInDegrees", res)]:
        ec.codes_set(g, k, v)
    ec.codes_set_values(g, np.zeros(ni * nj))
    return g


def grid_info(tmpl):
    lat = ec.codes_get_array(tmpl, "latitudes")
    lon = ((ec.codes_get_array(tmpl, "longitudes") + 180) % 360) - 180
    rel = ec.codes_get(tmpl, "uvRelativeToGrid")
    # analytic Lambert rotation: east is rotated counter-clockwise from grid +x by n*(lon-LoV)
    if rel:
        n = np.sin(np.radians(ec.codes_get_double(tmpl, "Latin1InDegrees")))
        lov = ((ec.codes_get_double(tmpl, "LoVInDegrees") + 180) % 360) - 180
        a = n * np.radians(lon - lov)
    else:
        a = None
    return lat, lon, a


class Writer:
    def __init__(self, path, tmpl, run, h):
        self.f = open(path, "wb")
        self.idx = []
        self.tmpl, self.run, self.h = tmpl, run, h
        self.n = 0

    def add(self, var, level, values, ftime=None):
        g = ec.codes_clone(self.tmpl)
        npts = ec.codes_get(self.tmpl, "numberOfDataPoints")
        vals = np.broadcast_to(np.asarray(values, dtype=np.float64), (npts,)).copy()
        if np.isnan(vals).any():
            ec.codes_set(g, "bitmapPresent", 1)
            ec.codes_set_double(g, "missingValue", 9999.0)
            vals = np.where(np.isnan(vals), 9999.0, vals)
        ec.codes_set(g, "bitsPerValue", 16)
        ec.codes_set_values(g, vals)
        off = self.f.tell()
        ec.codes_write(g, self.f)
        ec.codes_release(g)
        self.n += 1
        ft = ftime or ("anl" if self.h == 0 else f"{self.h} hour fcst")
        self.idx.append(f"{self.n}:{off}:d={self.run}:{var}:{level}:{ft}:")

    def close(self, path):
        self.f.close()
        with open(path + ".idx", "w") as f:
            f.write("\n".join(self.idx) + "\n")


def realistic(lat, lon, h, p=None):
    """Moving cyclone + jet for visual testing (WX_TEST_STYLE=realistic)."""
    clat, clon = 41.0 + 0.08 * h, -88.0 + 0.6 * h          # low tracks NE
    dy, dx = (lat - clat), (lon - clon) * np.cos(np.radians(lat))
    r2 = dx ** 2 + dy ** 2
    low = np.exp(-r2 / 60.0)
    # front: warm sector SE of low
    front = 1 / (1 + np.exp(-((dx * 0.8 - dy) - 1.0) / 1.2))
    d = {}
    d["mslp"] = 101800 - 2600 * low + 300 * np.sin(np.radians(lon * 4)) - 40 * (lat - 40)
    d["t2m"] = 273.15 + 24 - 0.75 * (lat - 30) + 9 * front * (lat < 47) - 4 * np.exp(-((lon + 110) / 8) ** 2)
    d["td2m"] = d["t2m"] - 4 - 8 * (1 - front) - 6 * (lon < -100)
    sp = 12 * np.sqrt(low) + 4
    d["u10"], d["v10"] = -sp * dy / np.sqrt(r2 + 4) * 1.0 + 3, sp * dx / np.sqrt(r2 + 4)
    band = np.exp(-(((dx * 0.8 - dy) - 1.0) ** 2) / 3.0) * (lat > clat - 6) + 0.8 * low
    d["rate"] = np.clip(band * 3.0 - 0.6, 0, None)
    d["refc"] = np.where(d["rate"] > 0.05, 15 + 12 * np.log1p(d["rate"] * 6), -20)
    d["ceil"] = np.where(band > 0.35, 2500 * (1.2 - band), np.nan)
    d["vis"] = np.where(band > 0.5, 16000 * (1.1 - band), 30000.0)
    if p is not None:
        trough = np.exp(-(((lon - (clon - 6)) * np.cos(np.radians(lat))) ** 2 + (lat - 46) ** 2) / 180.0)
        scale = {1000: 0.6, 925: 0.7, 850: 0.8, 700: 1.0, 500: 1.3, 400: 1.4, 300: 1.6, 250: 1.7, 200: 1.6}.get(p, 1.0)
        d["hgt"] = STD[p] - 8.0 * scale * (lat - 40) * (1 + 0.3 * p / 1000) - 120 * scale * trough
        jet = 70 * np.exp(-((lat - 40 - 4 * np.sin(np.radians((lon + 95) * 6))) / 5) ** 2) * (scale / 1.7) ** 1.5
        d["u"] = jet + 8 + 6 * scale
        d["v"] = 18 * scale * np.sin(np.radians((lon - clon + 6) * 8)) * np.exp(-((lat - 44) / 10) ** 2)
        d["t"] = 288 - 6.5 * STD[p] / 1000 - 0.6 * (lat - 35) + 6 * front * (p >= 700)
        d["rh"] = np.clip(40 + 55 * band + 10 * low, 5, 100)
    return d


def write_model(model, path, tmpl, run, h):
    lat, lon, a = grid_info(tmpl)
    if os.environ.get("WX_TEST_STYLE") == "realistic":
        return write_realistic(model, path, tmpl, run, h, lat, lon, a)
    ue = 30 * np.cos(np.radians(3 * (lat - 40)))
    ve = 10 * np.sin(np.radians(2 * (lon + 95)))
    if a is not None:
        ug = np.cos(a) * ue - np.sin(a) * ve
        vg = np.sin(a) * ue + np.cos(a) * ve
    else:
        ug, vg = ue, ve
    zs = 2500 * np.exp(-((lon + 110) / 8) ** 2)
    w = Writer(path, tmpl, run, h)
    w.add("TMP", "2 m above ground", 288 - 0.8 * (lat - 35))
    if model == "gfs":
        w.add("RH", "2 m above ground", np.full_like(lat, 50.0))
    else:
        w.add("DPT", "2 m above ground", 278 - 0.8 * (lat - 35))
    w.add("UGRD", "10 m above ground", ug * 0.3)
    w.add("VGRD", "10 m above ground", vg * 0.3)
    mslname = {"rrfs": "MSLET", "gfs": "PRMSL", "rap": "MSLMA"}[model]
    w.add(mslname, "mean sea level", 101325 + 1500 * np.sin(np.radians(lon * 3)))
    w.add("HGT", "surface", zs)
    w.add("REFC", "entire atmosphere", np.where(lon > -90, 35.0, -20.0))
    w.add("VIS", "surface", np.where(lat > 45, 3000.0, 24000.0))
    w.add("HGT", "0C isotherm", 2000 + 100 * (45 - lat))
    w.add("CAPE", "surface", np.maximum(0, 3000 * (35 - lat) / 15))
    w.add("PWAT", "entire atmosphere (considered as a single layer)", np.full_like(lat, 25.0))
    ceil = np.where(lat > 40, 800.0, np.nan)
    if model == "rrfs":
        w.add("CEIL", "cloud ceiling", ceil)
        w.add("HGT", "cloud ceiling", ceil + zs)
    elif model == "gfs":
        w.add("HGT", "cloud ceiling", ceil + zs)          # MSL
    else:
        w.add("HGT", "cloud ceiling", ceil)               # AGL
    for p in LEVELS:
        w.add("HGT", f"{p} mb", STD[p] + 50 * np.sin(np.radians(lon * 2)))
        w.add("TMP", f"{p} mb", 288 - 6.5 * STD[p] / 1000)
        w.add("RH", f"{p} mb", np.full_like(lat, 60.0))
        w.add("UGRD", f"{p} mb", ug)
        w.add("VGRD", f"{p} mb", vg)
    rate = np.where(lon > -90, 1.0, 0.0)
    if h > 0:
        if model == "gfs":   # 6-hourly reset buckets: 0-3, 0-6, 6-9, 6-12 ...
            s = (h - 1) // 6 * 6
            w.add("APCP", "surface", rate * (h - s), f"{s}-{h} hour acc fcst")
        else:
            w.add("APCP", "surface", rate * 1.0, f"{h-1}-{h} hour acc fcst")
            w.add("APCP", "surface", rate * h, f"0-{h} hour acc fcst")
    w.close(path)


def write_realistic(model, path, tmpl, run, h, lat, lon, a):
    def rot(ue, ve):
        if a is None:
            return ue, ve
        return np.cos(a) * ue - np.sin(a) * ve, np.sin(a) * ue + np.cos(a) * ve
    zs = 2400 * np.exp(-((lon + 110) / 7) ** 2) * (lat < 50) + 300 * np.exp(-((lon + 79) / 3) ** 2)
    d = realistic(lat, lon, h)
    w = Writer(path, tmpl, run, h)
    w.add("TMP", "2 m above ground", d["t2m"])
    w.add("DPT", "2 m above ground", d["td2m"])
    u, v = rot(d["u10"], d["v10"])
    w.add("UGRD", "10 m above ground", u)
    w.add("VGRD", "10 m above ground", v)
    w.add({"rrfs": "MSLET", "gfs": "PRMSL", "rap": "MSLMA"}[model], "mean sea level", d["mslp"])
    w.add("HGT", "surface", zs)
    w.add("REFC", "entire atmosphere", d["refc"])
    w.add("VIS", "surface", d["vis"])
    w.add("HGT", "0C isotherm", np.maximum(zs, (d["t2m"] - 273.15) / 6.5 * 1000))
    w.add("CAPE", "surface", np.maximum(0, (d["t2m"] - 290) * 300))
    w.add("PWAT", "entire atmosphere (considered as a single layer)", np.clip((d["td2m"] - 250) * 1.2, 2, 70))
    if model == "rrfs":
        w.add("CEIL", "cloud ceiling", d["ceil"])
    else:
        w.add("HGT", "cloud ceiling", d["ceil"] + zs if model == "gfs" else d["ceil"])
    for p in LEVELS:
        dp = realistic(lat, lon, h, p)
        w.add("HGT", f"{p} mb", dp["hgt"])
        w.add("TMP", f"{p} mb", dp["t"])
        w.add("RH", f"{p} mb", dp["rh"])
        u, v = rot(dp["u"], dp["v"])
        w.add("UGRD", f"{p} mb", u)
        w.add("VGRD", f"{p} mb", v)
    if h > 0:
        tot = sum(realistic(lat, lon, k)["rate"] for k in range(1, h + 1))
        last = realistic(lat, lon, h)["rate"]
        w.add("APCP", "surface", last, f"{h-1}-{h} hour acc fcst")
        w.add("APCP", "surface", tot, f"0-{h} hour acc fcst")
    w.close(path)


def main():
    out, run = sys.argv[1], sys.argv[2]
    hours = [int(x) for x in (sys.argv[3] if len(sys.argv) > 3 else "0,1,2,3").split(",")]
    dt = datetime.strptime(run, "%Y%m%d%H")
    ymd, cc = dt.strftime("%Y%m%d"), dt.strftime("%H")
    rrfs_t = lambert_template(330, 170, 26000, 18.0, 233.0)
    rap_t = lambert_template(451, 337, 13545, 16.281, 233.862)
    gfs_t = latlon_template(0.25)
    for model, tmpl in (("rrfs", rrfs_t), ("rap", rap_t), ("gfs", gfs_t)):
        for h in hours:
            if model == "rrfs":
                d = f"{out}/rrfs/rrfs.{ymd}/{cc}"; fn = f"rrfs.t{cc}z.prslev.13km.f{h:03d}.na.grib2"
            elif model == "gfs":
                d = f"{out}/gfs/gfs.{ymd}/{cc}/atmos"; fn = f"gfs.t{cc}z.pgrb2.0p25.f{h:03d}"
            else:
                d = f"{out}/rap/rap.{ymd}"; fn = f"rap.t{cc}z.awp130pgrbf{h:02d}.grib2"
            os.makedirs(d, exist_ok=True)
            write_model(model, f"{d}/{fn}", tmpl, run, h)
            print("wrote", f"{d}/{fn}")


if __name__ == "__main__":
    main()
