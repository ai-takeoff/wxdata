"""Main pipeline: find new model output, process it, publish site data.

Layout written under DATA_DIR (published with the website):
  index.json                               models, runs, availability, status
  {model}/{run}/meta.json                  fields, grids, hours for that run
  {model}/{run}/maps/f{FFF}/{field}.png    8-bit encoded map grids
  {model}/{run}/points/{ty}_{tx}.bin.gz    point data tiles (all hours, all fields)

Working state under WORK_DIR (kept between runs via the CI cache):
  {model}/{run}/state.json, points.npy, apcp/f{FFF}.npy, grids/ (regrid weights)
"""
import gzip
import json
import logging
import os
import shutil
import time
from datetime import datetime, timedelta, timezone

import numpy as np

from . import config as C
from . import fetch
from .gridding import Message, Regridder

log = logging.getLogger("process")

T0 = time.time()
TILE_FMT = 2          # bump to force point tiles to be rewritten


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def quantize(a, fdef):
    lo, hi = fdef["min"], fdef["max"]
    x = np.clip((a - lo) / (hi - lo), 0.0, 1.0)
    if fdef.get("scale") == "sqrt":
        x = np.sqrt(x)
    q = np.rint(x * 254.0)
    q[~np.isfinite(a)] = 255
    return q.astype(np.uint8)


def encode_map(q):
    """Gradient predictor (left + up - upleft, mod 256) before gzip; viewer inverts it."""
    qi = q.astype(np.int16)
    pred = np.zeros_like(qi)
    pred[:, 1:] += qi[:, :-1]
    pred[1:, :] += qi[:-1, :]
    pred[1:, 1:] -= qi[:-1, :-1]
    return ((qi - pred) % 256).astype(np.uint8)


def decode_map(d):
    q = np.zeros(d.shape, np.int32)
    ny, nx = d.shape
    for j in range(ny):
        for i in range(nx):
            p = (q[j, i - 1] if i else 0) + (q[j - 1, i] if j else 0) - (q[j - 1, i - 1] if i and j else 0)
            q[j, i] = (int(d[j, i]) + p) % 256
    return q.astype(np.uint8)


def dequantize(q, fdef):
    x = q.astype(np.float64) / 254.0
    if fdef.get("scale") == "sqrt":
        x = x * x
    v = fdef["min"] + x * (fdef["max"] - fdef["min"])
    v[q == 255] = np.nan
    return v


def dewpoint_c(t_c, rh):
    rh = np.clip(rh, 0.5, 100.0)
    a, b = 17.625, 243.04
    g = np.log(rh / 100.0) + a * t_c / (b + t_c)
    return b * g / (a - g)


def run_id(dt):
    return dt.strftime("%Y%m%d%H")


def fmt_url(tmpl, dt, h):
    return tmpl.format(ymd=dt.strftime("%Y%m%d"), cc=dt.strftime("%H"),
                       fff=f"{h:03d}", ff=f"{h:02d}")


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, separators=(",", ":"))
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def out_of_time():
    return time.time() - T0 > C.TIME_BUDGET


# ---------------------------------------------------------------------------
# per-run working state
# ---------------------------------------------------------------------------
class RunWork:
    def __init__(self, work_dir, data_dir, model, dt):
        self.model, self.dt, self.rid = model, dt, run_id(dt)
        self.cfg = C.MODELS[model]
        self.wdir = os.path.join(work_dir, model, self.rid)
        self.ddir = os.path.join(data_dir, model, self.rid)
        self.grid_dir = os.path.join(work_dir, "grids")
        os.makedirs(os.path.join(self.wdir, "apcp"), exist_ok=True)
        self.state_path = os.path.join(self.wdir, "state.json")
        self.state = read_json(self.state_path, dict(done=[], present=[], ceil_ref=None, source=None))
        d = C.DOMAIN
        self.pny = int(round((d["lat1"] - d["lat0"]) / C.POINT_RES)) + 1
        self.pnx = int(round((d["lon1"] - d["lon0"]) / C.POINT_RES)) + 1
        self.hours = C.hours_for(model)
        shape = (len(self.hours), len(C.POINT_FIELDS), self.pny, self.pnx)
        ppath = os.path.join(self.wdir, "points.npy")
        if os.path.exists(ppath):
            self.points = np.load(ppath, mmap_mode="r+")
            if self.points.shape != shape:
                del self.points
                os.remove(ppath)
        if not os.path.exists(ppath):
            self.points = np.lib.format.open_memmap(ppath, mode="w+", dtype=np.uint8, shape=shape)
            self.points[:] = 255
        self.regridders = {}

    @staticmethod
    def is_complete(work_dir, model, rid):
        st = read_json(os.path.join(work_dir, model, rid, "state.json"), {})
        return len(st.get("done", [])) == len(C.hours_for(model))

    def save(self):
        self.points.flush()
        write_json(self.state_path, self.state)

    def regridder(self, msg):
        r = self.regridders.get(msg.grid_key)
        if r is None:
            r = Regridder(msg, self.cfg["map_res"], self.grid_dir)
            self.regridders[msg.grid_key] = r
        return r


# ---------------------------------------------------------------------------
# precipitation: build run-total accumulation from whatever buckets exist
# ---------------------------------------------------------------------------
def apcp_total(rw, h, url_for, entries_cache):
    path = os.path.join(rw.wdir, "apcp", f"f{h:03d}.npy")
    if os.path.exists(path):
        return np.load(path)
    if h == 0:
        return None   # caller substitutes zeros once the grid is known
    url = url_for(h)
    entries = entries_cache.get(h) or fetch.fetch_idx(url)
    if entries is None:
        raise IOError(f"APCP: idx missing for f{h:03d}")
    recs = [r for r in fetch.apcp_records(entries) if r[1] == h]
    if not recs:
        raise IOError(f"APCP: no accumulation ending at f{h:03d}")
    s, _, e = min(recs, key=lambda r: r[0])          # widest bucket
    raw = fetch.download_records(url, [e])[0]
    msg = Message(raw)
    try:
        rg = rw.regridder(msg)
        bucket = rg.apply(msg.values)
    finally:
        msg.release()
    if s == 0:
        tot = bucket
    else:
        prev = apcp_total(rw, s, url_for, entries_cache)
        tot = bucket + (prev if prev is not None else 0.0)
    np.save(path, tot.astype(np.float32))
    return tot


# ---------------------------------------------------------------------------
# one forecast hour
# ---------------------------------------------------------------------------
def process_hour(rw, h, url, entries, url_for):
    # pick records
    wanted = {}
    for key, alts in C.RAW_REQUESTS.items():
        for var, lev in alts:
            e = fetch.match(entries, var, lev)
            if e is not None:
                wanted[key] = e
                break
    keys = list(wanted)
    raws = fetch.download_records(url, [wanted[k] for k in keys])
    msgs = {}
    try:
        for k, raw in zip(keys, raws):
            msgs[k] = Message(raw)
        if not msgs:
            raise IOError("no usable records")
        ref = next(iter(msgs.values()))
        rg = rw.regridder(ref)

        nat = {k: m.values for k, m in msgs.items()}
        out = {}

        def put(name, arr):
            out[name] = arr

        # temperatures / moisture
        if "t2m" in nat:
            put("t2m", rg.apply(nat["t2m"] - 273.15))
        if "td2m" in nat:
            put("td2m", rg.apply(nat["td2m"] - 273.15))
        elif "rh2m" in nat and "t2m" in nat:
            put("td2m", rg.apply(dewpoint_c(nat["t2m"] - 273.15, nat["rh2m"])))
        # winds (rotate to earth-relative on the native grid first)
        kt = 1.943844

        def winds(uk, vk, outu, outv):
            if uk in msgs and vk in msgs:
                r2 = rw.regridder(msgs[uk])
                u, v = r2.earth_winds(nat[uk], nat[vk]) if msgs[uk].uv_grid_relative else (nat[uk], nat[vk])
                put(outu, r2.apply(u * kt))
                put(outv, r2.apply(v * kt))

        winds("u10", "v10", "u10", "v10")
        for p in C.POINT_LEVELS:
            winds(f"u_{p}", f"v_{p}", f"u_{p}", f"v_{p}")
            if f"hgt_{p}" in nat:
                put(f"hgt_{p}", rg.apply(nat[f"hgt_{p}"]))
            if f"t_{p}" in nat:
                put(f"t_{p}", rg.apply(nat[f"t_{p}"] - 273.15))
            if f"rh_{p}" in nat:
                put(f"rh_{p}", rg.apply(nat[f"rh_{p}"]))
        if "mslp" in nat:
            put("mslp", rg.apply(nat["mslp"] / 100.0))
        if "refc" in nat:
            put("refc", rg.apply(np.maximum(nat["refc"], -10.0)))
        if "vis" in nat:
            put("vis", rg.apply(nat["vis"] / 1609.344))
        if "frzl" in nat:
            put("frzl", rg.apply(nat["frzl"] * 3.28084))
        if "cape" in nat:
            put("cape", rg.apply(nat["cape"]))
        if "pwat" in nat:
            put("pwat", rg.apply(nat["pwat"]))
        if "hgtsfc" in nat:
            put("hgtsfc", rg.apply(nat["hgtsfc"] * 3.28084))

        # ceiling (ft AGL); "no ceiling" -> field max
        cmax_m = C.FIELDS["ceil"]["max"] / 3.28084
        ceil = None
        if "ceil_agl" in nat:
            ceil = nat["ceil_agl"].copy()
            rw.state["ceil_ref"] = "CEIL (AGL)"
        elif "ceil_hgt" in nat:
            ceil = nat["ceil_hgt"].copy()
            if "hgtsfc" in nat:
                zs = nat["hgtsfc"]
                m = (zs > 1500) & np.isfinite(ceil) & (ceil < 15000)
                is_agl = m.sum() > 50 and np.mean(ceil[m] < zs[m]) > 0.3
                if not is_agl:
                    ceil = ceil - zs
                rw.state["ceil_ref"] = "HGT (AGL)" if is_agl else "HGT (MSL-terrain)"
        if ceil is not None:
            ceil[~np.isfinite(ceil) | (ceil > cmax_m) | (ceil < -500)] = cmax_m
            put("ceil", rg.apply(np.maximum(ceil, 0.0) * 3.28084))
    finally:
        for m in msgs.values():
            m.release()

    # precipitation total
    try:
        tot = apcp_total(rw, h, url_for, {h: entries})
        if tot is None:
            tot = np.zeros(rg.shape, np.float32)
            tot[~rg.valid.reshape(rg.shape)] = np.nan
            np.save(os.path.join(rw.wdir, "apcp", f"f{h:03d}.npy"), tot)
        out["apcp"] = tot
    except Exception as e:
        log.warning("%s %s f%03d: precip unavailable (%s)", rw.model, rw.rid, h, e)

    # write maps
    mdir = os.path.join(rw.ddir, "maps", f"f{h:03d}")
    os.makedirs(mdir, exist_ok=True)
    for name in C.MAP_FIELDS:
        if name in out:
            q = np.flipud(quantize(out[name], C.FIELDS[name]))      # row 0 = north
            with open(os.path.join(mdir, name + ".bin.gz"), "wb") as f:
                f.write(gzip.compress(encode_map(q).tobytes(), compresslevel=9, mtime=0))
    # point data
    step = int(round(C.POINT_RES / rw.cfg["map_res"]))
    hi = rw.hours.index(h)
    for fi, name in enumerate(C.POINT_FIELDS):
        if name in out:
            rw.points[hi, fi] = quantize(out[name][::step, ::step], C.FIELDS[name])
    present = set(rw.state["present"]) | set(out)
    rw.state["present"] = sorted(present)
    if h not in rw.state["done"]:
        rw.state["done"] = sorted(rw.state["done"] + [h])


# ---------------------------------------------------------------------------
# outputs
# ---------------------------------------------------------------------------
def write_tiles(rw):
    pdir = os.path.join(rw.ddir, "points")
    os.makedirs(pdir, exist_ok=True)
    T = C.TILE
    nty, ntx = -(-rw.pny // T), -(-rw.pnx // T)
    arr = rw.points
    for ty in range(nty):
        for tx in range(ntx):
            blk = arr[:, :, ty * T:(ty + 1) * T, tx * T:(tx + 1) * T]
            path = os.path.join(pdir, f"{ty}_{tx}.bin.gz")
            if np.all(blk == 255):
                if os.path.exists(path):
                    os.remove(path)
                continue
            full = np.full(arr.shape[:2] + (T, T), 255, np.uint8)
            full[:, :, :blk.shape[2], :blk.shape[3]] = blk
            # hour-to-hour difference (mod 256) compresses far better; viewer re-accumulates
            enc = full.copy()
            enc[1:] = (full[1:].astype(np.int16) - full[:-1].astype(np.int16)) % 256
            with open(path + ".tmp", "wb") as f:
                f.write(gzip.compress(enc.tobytes(), compresslevel=9, mtime=0))
            os.replace(path + ".tmp", path)
    return nty, ntx


def write_meta(rw, nty, ntx):
    d = C.DOMAIN
    present = [f for f in C.POINT_FIELDS if f in rw.state["present"]]
    meta = dict(
        model=rw.model, run=rw.rid, init=rw.dt.strftime("%Y-%m-%dT%H:00:00Z"),
        hours=rw.hours, available=rw.state["done"],
        complete=len(rw.state["done"]) == len(rw.hours),
        fields={f: C.FIELDS[f] for f in C.POINT_FIELDS},
        present=present,
        map_fields=[f for f in C.MAP_FIELDS if f in present],
        point_fields=C.POINT_FIELDS,
        map_grid=dict(lat0=d["lat0"], lat1=d["lat1"], lon0=d["lon0"], lon1=d["lon1"],
                      res=rw.cfg["map_res"]),
        point_grid=dict(lat0=d["lat0"], lon0=d["lon0"], res=C.POINT_RES, ny=rw.pny, nx=rw.pnx,
                        tile=C.TILE, nty=nty, ntx=ntx),
        levels=C.POINT_LEVELS, map_levels=C.MAP_LEVELS, tile_encoding="hour-delta",
        ceil_ref=rw.state.get("ceil_ref"), source=rw.state.get("source"),
        generated=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    os.makedirs(rw.ddir, exist_ok=True)
    write_json(os.path.join(rw.ddir, "meta.json"), meta)


def dir_size(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def write_index(data_dir, work_dir, status):
    models = {}
    for m in C.MODEL_ORDER:
        runs = []
        mdir = os.path.join(data_dir, m)
        if os.path.isdir(mdir):
            for rid in sorted(os.listdir(mdir), reverse=True):
                meta = read_json(os.path.join(mdir, rid, "meta.json"))
                if not meta or not meta["available"]:
                    continue
                runs.append(dict(run=rid, init=meta["init"], available=meta["available"],
                                 nhours=len(meta["hours"]), complete=meta["complete"]))
        models[m] = dict(label=C.MODELS[m]["label"], short=C.MODELS[m]["short"], runs=runs,
                         status=status.get(m, {}))
    write_json(os.path.join(data_dir, "index.json"),
               dict(generated=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    order=C.MODEL_ORDER, models=models))


# ---------------------------------------------------------------------------
# per model
# ---------------------------------------------------------------------------
def discover(model, now):
    cfg = C.MODELS[model]
    step = cfg["cycle_step"]
    start = now.replace(minute=0, second=0, microsecond=0)
    start -= timedelta(hours=start.hour % step)
    found = []
    t = start
    while t >= now - timedelta(hours=cfg["lookback"]) and len(found) < 2:
        for tmpl in C.sources_for(model):
            if fetch.head_ok(fmt_url(tmpl, t, C.hours_for(model)[0])):
                found.append((t, tmpl))
                break
        t -= timedelta(hours=step)
    return found


def process_model(model, data_dir, work_dir, now, status):
    cfg = C.MODELS[model]
    st = status.setdefault(model, {})
    st["last_check"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    runs = discover(model, now)
    if not runs:
        st["error"] = "no recent runs found on any source"
        log.warning("%s: no runs found", model)
        return False
    # Retention: the newest run, plus the previous run only while the newest is
    # still coming in. Keeps the published site small.
    keep_ids = set()
    changed = False
    for dt, tmpl in runs:                       # newest first
        if keep_ids and all(RunWork.is_complete(work_dir, model, rid) for rid in keep_ids):
            break
        keep_ids.add(run_id(dt))
        if out_of_time():
            continue
        rw = RunWork(work_dir, data_dir, model, dt)
        todo = [h for h in rw.hours if h not in rw.state["done"]]
        did = failed = 0
        for h in todo:
            if out_of_time():
                log.info("time budget reached")
                break
            url = None
            entries = None
            for t2 in [tmpl] + [s for s in C.sources_for(model) if s != tmpl]:
                u = fmt_url(t2, dt, h)
                entries = fetch.fetch_idx(u)
                if entries:
                    url, src = u, t2
                    break
            if not entries:
                continue                         # not out yet; later hours may be
            try:
                t1 = time.time()
                process_hour(rw, h, url, entries, lambda hh, s=src: fmt_url(s, dt, hh))
                rw.state["source"] = src.split("/")[2]
                rw.save()
                did += 1
                log.info("%s %s f%03d done in %.1fs", model, rw.rid, h, time.time() - t1)
            except Exception as e:
                log.exception("%s %s f%03d failed: %s", model, rw.rid, h, e)
                st["error"] = f"{rw.rid} f{h:03d}: {e}"
                failed += 1
        stale_fmt = rw.state.get("tile_fmt") != TILE_FMT
        if did or stale_fmt or not os.path.exists(os.path.join(rw.ddir, "meta.json")):
            if rw.state["done"]:
                rw.state["tile_fmt"] = TILE_FMT
                changed = changed or stale_fmt
                nty, ntx = write_tiles(rw)
                write_meta(rw, nty, ntx)
                changed = changed or did > 0
                if did:
                    st["last_new_data"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                if did and not failed:
                    st.pop("error", None)
        rw.save()
    # prune runs no longer kept (data + work)
    for base in (os.path.join(data_dir, model), os.path.join(work_dir, model)):
        if os.path.isdir(base):
            for rid in os.listdir(base):
                if rid not in keep_ids:
                    shutil.rmtree(os.path.join(base, rid), ignore_errors=True)
                    changed = True
    return changed


def enforce_size(data_dir, work_dir):
    while dir_size(data_dir) > C.SIZE_LIMIT:
        cands = []
        for m in C.MODEL_ORDER:
            mdir = os.path.join(data_dir, m)
            if os.path.isdir(mdir):
                rids = sorted(os.listdir(mdir))
                if len(rids) > 1:
                    cands.append((rids[0], m))
        if not cands:
            log.warning("data exceeds size limit but nothing left to prune")
            return
        rid, m = min(cands)
        log.warning("size limit: dropping %s %s", m, rid)
        shutil.rmtree(os.path.join(data_dir, m, rid), ignore_errors=True)
        shutil.rmtree(os.path.join(work_dir, m, rid), ignore_errors=True)


def main(data_dir, work_dir, models=None, now=None):
    now = now or datetime.now(timezone.utc)
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    status_path = os.path.join(work_dir, "status.json")
    status = read_json(status_path, {})
    changed = False
    for m in (models or C.PROCESS_ORDER):
        try:
            changed |= process_model(m, data_dir, work_dir, now, status)
        except Exception as e:
            log.exception("%s failed: %s", m, e)
            status.setdefault(m, {})["error"] = str(e)
        write_json(status_path, status)
    enforce_size(data_dir, work_dir)
    write_index(data_dir, work_dir, status)
    log.info("data size %.1f MB, changed=%s", dir_size(data_dir) / 1e6, changed)
    return changed
