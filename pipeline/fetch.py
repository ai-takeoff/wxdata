"""Downloading: GRIB .idx parsing and byte-range fetches with retries."""
import re
import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests

log = logging.getLogger("fetch")

_session = None


def session():
    global _session
    if _session is None:
        s = requests.Session()
        s.headers["User-Agent"] = "wxdata-pipeline/1.0 (personal forecast viewer)"
        adapter = requests.adapters.HTTPAdapter(pool_connections=16, pool_maxsize=16)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        _session = s
    return _session


# NOMADS blocks clients making more than ~120 requests/minute; stay under it.
_RATE = {"nomads.ncep.noaa.gov": 0.6}
_last = {}
_lock = threading.Lock()


def _throttle(url):
    host = urlparse(url).hostname or ""
    gap = _RATE.get(host)
    if not gap:
        return
    with _lock:
        now = time.time()
        wait = _last.get(host, 0) + gap - now
        _last[host] = max(now, _last.get(host, 0) + gap)
    if wait > 0:
        time.sleep(wait)


def _get(url, headers=None, timeout=60, tries=4):
    """GET with retries. Returns Response, or None for a definitive 404/403."""
    delay = 2
    for attempt in range(tries):
        try:
            _throttle(url)
            r = session().get(url, headers=headers, timeout=timeout)
            if r.status_code in (404, 403):
                return None
            if r.status_code in (200, 206):
                return r
            log.warning("HTTP %s for %s", r.status_code, url)
        except requests.RequestException as e:
            log.warning("request error %s: %s", url, e)
        if attempt < tries - 1:
            time.sleep(delay)
            delay *= 2
    raise IOError(f"failed after {tries} tries: {url}")


def fetch_idx(url):
    """Fetch and parse a wgrib2-style .idx file. Returns list of entries or None."""
    r = _get(url + ".idx", timeout=30)
    if r is None:
        return None
    return parse_idx(r.text)


def parse_idx(text):
    entries = []
    for line in text.strip().splitlines():
        parts = line.split(":")
        if len(parts) < 6:
            continue
        try:
            num, off = int(parts[0].split(".")[0]), int(parts[1])
        except ValueError:
            continue
        entries.append(dict(num=num, start=off, var=parts[3], level=parts[4],
                            ftime=parts[5], line=line))
    # byte end = next record start - 1 (sub-messages "3.1","3.2" share an offset)
    starts = sorted(set(e["start"] for e in entries))
    nxt = {s: (starts[i + 1] - 1 if i + 1 < len(starts) else None) for i, s in enumerate(starts)}
    for e in entries:
        e["end"] = nxt[e["start"]]
    return entries


def match(entries, var, level_regex, ftime_filter=None):
    rx = re.compile(r"^" + level_regex + r"$")
    for e in entries:
        if e["var"] != var or not rx.match(e["level"]):
            continue
        ft = e["ftime"]
        # skip averages / accumulations / probability records for instantaneous fields
        if ftime_filter is None and re.search(r"(ave|acc|max|min|prob)", ft):
            continue
        if ftime_filter is not None and not ftime_filter(ft):
            continue
        return e
    return None


APCP_RX = re.compile(r"(\d+)-(\d+) (hour|day) acc")


def apcp_records(entries):
    """All APCP:surface accumulation records as (start_h, end_h, entry)."""
    out = []
    for e in entries:
        if e["var"] == "APCP" and e["level"] == "surface":
            m = APCP_RX.search(e["ftime"])
            if m:
                k = 24 if m.group(3) == "day" else 1
                out.append((int(m.group(1)) * k, int(m.group(2)) * k, e))
    return out


def download_records(url, recs, gap=262144, workers=8):
    """Download the given idx records via merged byte ranges.

    Returns list of bytes, one per record, in the same order as recs.
    """
    if not recs:
        return []
    order = sorted(range(len(recs)), key=lambda i: recs[i]["start"])
    # merge into ranges
    groups = []
    for i in order:
        e = recs[i]
        if groups and groups[-1]["end"] is not None and e["start"] - groups[-1]["end"] <= gap:
            g = groups[-1]
            g["items"].append(i)
            g["end"] = None if e["end"] is None else max(g["end"], e["end"])
        else:
            groups.append(dict(start=e["start"], end=e["end"], items=[i]))

    def get_group(g):
        rng = f"bytes={g['start']}-" + ("" if g["end"] is None else str(g["end"]))
        r = _get(url, headers={"Range": rng}, timeout=120)
        if r is None:
            raise IOError(f"missing GRIB file {url}")
        data = r.content
        if r.status_code == 200 and g["start"] > 0:   # server ignored Range
            data = data[g["start"]:] if g["end"] is None else data[g["start"]:g["end"] + 1]
        return g, data

    out = [None] * len(recs)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for g, data in ex.map(get_group, groups):
            for i in g["items"]:
                e = recs[i]
                a = e["start"] - g["start"]
                b = None if e["end"] is None else e["end"] - g["start"] + 1
                out[i] = data[a:b]
    return out


def head_ok(url):
    """True if url.idx exists (cheap availability probe)."""
    try:
        _throttle(url)
        r = session().head(url + ".idx", timeout=20, allow_redirects=True)
        return r.status_code == 200
    except requests.RequestException:
        return False
