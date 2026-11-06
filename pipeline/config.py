"""Configuration: domain, models, data sources and the field catalog.

Everything the viewer needs to know about fields (units, ranges, encoding)
is written into each run's meta.json from here, so this file is the single
source of truth.
"""
import os

# ---------------------------------------------------------------------------
# Output domain: CONUS plus southern Canada (Toronto, Montreal, Ottawa).
# Data is regridded onto regular lat/lon grids over this box.
# ---------------------------------------------------------------------------
DOMAIN = dict(lat0=20.0, lat1=56.0, lon0=-130.0, lon1=-60.0)

POINT_RES = 0.25      # grid spacing (deg) of the click-anywhere point data
TILE = 16             # point tiles are TILE x TILE points (4 x 4 degrees)

# Pressure levels kept for soundings / winds aloft (point data)
POINT_LEVELS = [1000, 950, 925, 900, 850, 800, 750, 700, 600, 500, 400, 300, 250, 200]
# Pressure levels rendered as maps
MAP_LEVELS = [925, 850, 700, 500, 300, 250]

# ---------------------------------------------------------------------------
# Models
#   sources: URL templates tried in order (AWS first, NOMADS fallback).
#            Override with env var WX_SOURCES_<MODEL> (comma separated).
#   hours:   forecast hours to process
#   cycle_step: hours between runs we use; lookback: how far back to look
#   keep:    how many recent runs to keep online
# ---------------------------------------------------------------------------
MODELS = {
    "rrfs": dict(
        label="RRFS (NAM replacement)",
        short="RRFS",
        sources=[
            "https://noaa-rrfs-ops-pds.s3.amazonaws.com/rrfs.{ymd}/{cc}/rrfs.t{cc}z.prslev.13km.f{fff}.na.grib2",
            "https://nomads.ncep.noaa.gov/pub/data/nccf/com/rrfs/prod/rrfs.{ymd}/{cc}/rrfs.t{cc}z.prslev.13km.f{fff}.na.grib2",
        ],
        hours=list(range(0, 25)) + list(range(27, 85, 3)),
        cycle_step=6, lookback=30, keep=2,
        map_res=0.125,
    ),
    "gfs": dict(
        label="GFS (long range)",
        short="GFS",
        sources=[
            "https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.{ymd}/{cc}/atmos/gfs.t{cc}z.pgrb2.0p25.f{fff}",
            "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/gfs.{ymd}/{cc}/atmos/gfs.t{cc}z.pgrb2.0p25.f{fff}",
        ],
        hours=list(range(0, 121, 3)) + list(range(126, 241, 6)) + list(range(252, 385, 12)),
        cycle_step=6, lookback=30, keep=2,
        map_res=0.25,
    ),
    "rap": dict(
        label="RAP (short range)",
        short="RAP",
        sources=[
            "https://noaa-rap-pds.s3.amazonaws.com/rap.{ymd}/rap.t{cc}z.awp130pgrbf{ff}.grib2",
            "https://nomads.ncep.noaa.gov/pub/data/nccf/com/rap/prod/rap.{ymd}/rap.t{cc}z.awp130pgrbf{ff}.grib2",
        ],
        hours=list(range(0, 22)),
        cycle_step=1, lookback=6, keep=3,
        map_res=0.125,
    ),
}

MODEL_ORDER = ["rrfs", "gfs", "rap"]          # display order
PROCESS_ORDER = ["rrfs", "rap", "gfs"]        # processing priority (GFS is the largest)


def hours_for(model):
    env = os.environ.get("WX_HOURS_" + model.upper())
    if env:
        return [int(x) for x in env.split(",") if x.strip()]
    return MODELS[model]["hours"]


def sources_for(model):
    env = os.environ.get("WX_SOURCES_" + model.upper())
    if env:
        return [s.strip() for s in env.split(",") if s.strip()]
    return MODELS[model]["sources"]


# ---------------------------------------------------------------------------
# Field catalog.
#
# Each stored field is quantized to one byte:
#   x = (v - min) / (max - min), clipped to 0..1; x = sqrt(x) if scale == "sqrt"
#   q = round(x * 254); 255 = missing / outside model domain
# Units are the display units (conversion from GRIB units happens in process.py).
# ---------------------------------------------------------------------------
STD_HGT = {1000: 110, 950: 540, 925: 760, 900: 990, 850: 1460, 800: 1950, 750: 2470,
           700: 3010, 600: 4210, 500: 5570, 400: 7180, 300: 9160, 250: 10360, 200: 11790}


def _t_range(p):
    if p >= 900: return (-50, 45)
    if p >= 750: return (-45, 35)
    if p >= 600: return (-50, 25)
    if p >= 500: return (-60, 10)
    if p >= 400: return (-70, 0)
    return (-85, -15)


def _w_range(p):
    if p >= 700: return 110
    if p >= 500: return 160
    return 210


def _level_fields():
    out = {}
    for p in POINT_LEVELS:
        s = STD_HGT[p]
        out[f"hgt_{p}"] = dict(units="m", min=round(s - (450 + 0.09 * s)), max=round(s + (300 + 0.04 * s)))
        lo, hi = _t_range(p)
        out[f"t_{p}"] = dict(units="C", min=lo, max=hi)
        out[f"rh_{p}"] = dict(units="%", min=0, max=100)
        w = _w_range(p)
        out[f"u_{p}"] = dict(units="kt", min=-w, max=w)
        out[f"v_{p}"] = dict(units="kt", min=-w, max=w)
    return out


SFC_FIELDS = {
    "t2m":    dict(units="C", min=-50, max=50),
    "td2m":   dict(units="C", min=-55, max=35),
    "u10":    dict(units="kt", min=-80, max=80),
    "v10":    dict(units="kt", min=-80, max=80),
    "mslp":   dict(units="hPa", min=940, max=1060),
    "apcp":   dict(units="mm", min=0, max=250, scale="sqrt"),   # run-total precip
    "refc":   dict(units="dBZ", min=-10, max=75),
    "ceil":   dict(units="ft", min=0, max=20000, scale="sqrt"), # AGL; max = no ceiling
    "vis":    dict(units="sm", min=0, max=10, scale="sqrt"),
    "frzl":   dict(units="ft", min=0, max=20000),               # MSL
    "cape":   dict(units="J/kg", min=0, max=6000, scale="sqrt"),
    "pwat":   dict(units="mm", min=0, max=80),
    "hgtsfc": dict(units="ft", min=0, max=15000),               # model terrain
}

FIELDS = dict(SFC_FIELDS)
FIELDS.update(_level_fields())

POINT_FIELDS = list(SFC_FIELDS.keys()) + [
    f"{v}_{p}" for p in POINT_LEVELS for v in ("hgt", "t", "rh", "u", "v")]
MAP_FIELDS = [f for f in SFC_FIELDS if f != "hgtsfc"] + [
    f"{v}_{p}" for p in MAP_LEVELS for v in ("hgt", "t", "rh", "u", "v")]

# ---------------------------------------------------------------------------
# GRIB index matching: raw GRIB records requested from each file.
#   key -> list of (VAR, level regex) alternatives, first match wins.
# APCP is handled separately (accumulation buckets).
# ---------------------------------------------------------------------------
RAW_REQUESTS = {
    "t2m":    [("TMP", r"2 m above ground")],
    "td2m":   [("DPT", r"2 m above ground")],
    "rh2m":   [("RH", r"2 m above ground")],
    "u10":    [("UGRD", r"10 m above ground")],
    "v10":    [("VGRD", r"10 m above ground")],
    "mslp":   [("MSLET", r"mean sea level"), ("PRMSL", r"mean sea level"), ("MSLMA", r"mean sea level")],
    "refc":   [("REFC", r"entire atmosphere.*")],
    "ceil_agl": [("CEIL", r"cloud ceiling")],
    "ceil_hgt": [("HGT", r"cloud ceiling")],
    "vis":    [("VIS", r"surface")],
    "frzl":   [("HGT", r"0C isotherm")],
    "cape":   [("CAPE", r"surface")],
    "pwat":   [("PWAT", r"entire atmosphere.*")],
    "hgtsfc": [("HGT", r"surface")],
}
for _p in POINT_LEVELS:
    for _k, _v in (("hgt", "HGT"), ("t", "TMP"), ("rh", "RH"), ("u", "UGRD"), ("v", "VGRD")):
        RAW_REQUESTS[f"{_k}_{_p}"] = [(_v, rf"{_p} mb")]

# Time budget per pipeline invocation (seconds) before it stops starting new
# forecast hours and publishes what it has. Keeps CI jobs bounded.
TIME_BUDGET = int(os.environ.get("WX_TIME_BUDGET", 35 * 60))

# Total published data size guard (bytes). Oldest extra runs are dropped
# beyond this to stay under GitHub Pages' 1 GB site limit.
SIZE_LIMIT = int(os.environ.get("WX_SIZE_LIMIT", 900 * 1024 * 1024))
