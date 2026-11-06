"""GRIB decoding, wind rotation and regridding onto the output lat/lon grids."""
import hashlib
import os

import numpy as np
import eccodes as ec
from scipy.spatial import cKDTree

from .config import DOMAIN

R_EARTH_KM = 6371.0


class Message:
    """Decoded GRIB2 message: values on the native grid + grid description."""

    def __init__(self, raw):
        gid = ec.codes_new_from_message(raw)
        try:
            self.grid_type = ec.codes_get(gid, "gridType")
            if self.grid_type in ("regular_ll", "regular_gg", "rotated_ll"):
                self.ni, self.nj = ec.codes_get(gid, "Ni"), ec.codes_get(gid, "Nj")
            else:
                self.ni, self.nj = ec.codes_get(gid, "Nx"), ec.codes_get(gid, "Ny")
            vals = ec.codes_get_values(gid).astype(np.float64)
            if ec.codes_get(gid, "bitmapPresent"):
                miss = ec.codes_get_double(gid, "missingValue")
                vals[vals == miss] = np.nan
            self.values = vals
            try:
                self.uv_grid_relative = bool(ec.codes_get(gid, "uvRelativeToGrid"))
            except ec.KeyValueNotFoundError:
                self.uv_grid_relative = False
            try:
                self.j_consecutive = bool(ec.codes_get(gid, "jPointsAreConsecutive"))
            except ec.KeyValueNotFoundError:
                self.j_consecutive = False
            try:
                self.i_negative = bool(ec.codes_get(gid, "iScansNegatively"))
            except ec.KeyValueNotFoundError:
                self.i_negative = False
            self.grid_key = self._grid_key(gid)
            self._gid = gid
            self._latlon = None
        except Exception:
            ec.codes_release(gid)
            raise

    def _grid_key(self, gid):
        keys = [self.grid_type, self.ni, self.nj]
        for k in ("latitudeOfFirstGridPointInDegrees", "longitudeOfFirstGridPointInDegrees",
                  "iDirectionIncrementInDegrees", "DxInMetres", "LoVInDegrees",
                  "latitudeOfSouthernPoleInDegrees", "scanningMode"):
            try:
                keys.append(round(float(ec.codes_get_double(gid, k)), 5))
            except Exception:
                keys.append(None)
        return hashlib.sha1(repr(keys).encode()).hexdigest()[:16]

    def latlon(self):
        if self._latlon is None:
            la = ec.codes_get_array(self._gid, "latitudes")
            lo = ec.codes_get_array(self._gid, "longitudes")
            lo = ((lo + 180.0) % 360.0) - 180.0
            self._latlon = (la, lo)
        return self._latlon

    def release(self):
        if self._gid is not None:
            ec.codes_release(self._gid)
            self._gid = None


def _xyz(lat, lon):
    la, lo = np.radians(lat), np.radians(lon)
    return np.column_stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)])


class Regridder:
    """Inverse-distance (k=4) interpolation from a native grid to the output grid.

    Weights are computed once per native grid and cached on disk.
    """

    def __init__(self, msg, res, cache_dir):
        self.res = res
        d = DOMAIN
        self.lats = np.round(np.arange(d["lat0"], d["lat1"] + res / 2, res), 6)
        self.lons = np.round(np.arange(d["lon0"], d["lon1"] + res / 2, res), 6)
        self.shape = (len(self.lats), len(self.lons))
        os.makedirs(cache_dir, exist_ok=True)
        path = os.path.join(cache_dir, f"w_{msg.grid_key}_{res}.npz")
        if os.path.exists(path):
            z = np.load(path)
            self.idx, self.w, self.valid = z["idx"], z["w"], z["valid"]
            self.rot = z["rot"] if z["rot"].size else None
            return
        lat, lon = msg.latlon()
        # restrict source points to the domain plus margin for speed
        m = 3.0
        sel = np.where((lat >= d["lat0"] - m) & (lat <= d["lat1"] + m) &
                       (lon >= d["lon0"] - m) & (lon <= d["lon1"] + m))[0]
        tree = cKDTree(_xyz(lat[sel], lon[sel]))
        glon, glat = np.meshgrid(self.lons, self.lats)
        dist, ii = tree.query(_xyz(glat.ravel(), glon.ravel()), k=4)
        dist_km = dist * R_EARTH_KM
        # native spacing estimate: median nearest-neighbour distance between source points
        sp = np.median(tree.query(tree.data[::max(1, len(sel) // 5000)], k=2)[0][:, 1]) * R_EARTH_KM
        valid = dist_km[:, 0] <= 1.5 * sp
        w = 1.0 / np.maximum(dist_km, 1e-3) ** 2
        w /= w.sum(axis=1, keepdims=True)
        self.idx = sel[ii].astype(np.int64)
        self.w = w.astype(np.float32)
        self.valid = valid
        self.rot = self._rotation(msg) if msg.uv_grid_relative else None
        np.savez(path, idx=self.idx, w=self.w, valid=self.valid,
                 rot=self.rot if self.rot is not None else np.zeros(0))

    @staticmethod
    def _rotation(msg):
        """Angle (radians, per native point) of the grid x-axis from east."""
        lat, lon = msg.latlon()
        if msg.j_consecutive:
            la, lo = lat.reshape(msg.ni, msg.nj).T, lon.reshape(msg.ni, msg.nj).T
        else:
            la, lo = lat.reshape(msg.nj, msg.ni), lon.reshape(msg.nj, msg.ni)
        dlon = np.gradient(np.unwrap(np.radians(lo), axis=1), axis=1)
        dlat = np.gradient(np.radians(la), axis=1)
        ang = np.arctan2(dlat, dlon * np.cos(np.radians(la)))
        if msg.i_negative:          # array index runs opposite to the grid x-axis
            ang = ang + np.pi
        if msg.j_consecutive:
            ang = ang.T
        return ang.ravel().astype(np.float32)

    def earth_winds(self, u, v):
        """Rotate grid-relative winds (native arrays) to earth-relative."""
        if self.rot is None:
            return u, v
        c, s = np.cos(self.rot), np.sin(self.rot)
        return u * c - v * s, u * s + v * c

    def apply(self, values):
        vv = values[self.idx]                       # (N, 4)
        ok = np.isfinite(vv)
        w = np.where(ok, self.w, 0.0)
        ws = w.sum(axis=1)
        out = np.where(ws > 0, (np.where(ok, vv, 0.0) * w).sum(axis=1) / np.maximum(ws, 1e-12), np.nan)
        out[~self.valid] = np.nan
        return out.reshape(self.shape).astype(np.float32)
