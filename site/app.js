/* WxData viewer — RRFS / GFS / RAP model data, rendered client-side.
 * Data format: see pipeline/process.py. Map grids are 8-bit, gradient-predicted,
 * gzipped; point tiles are 8-bit [hour][field][16][16] gzipped. */
"use strict";

const DATA = "data";
const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------------------
// small utils
// ---------------------------------------------------------------------------
const pad = (n, w = 2) => String(n).padStart(w, "0");
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const store = {
  get(k, d) { try { const v = localStorage.getItem("wx." + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem("wx." + k, JSON.stringify(v)); } catch (e) { /* ignore */ } },
};

async function fetchJSON(url) {
  const r = await fetch(url, { cache: "no-cache" });
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return r.json();
}

async function fetchGz(url) {
  const r = await fetch(url);
  if (!r.ok) { const e = new Error(`${r.status} ${url}`); e.status = r.status; throw e; }
  const buf = await r.arrayBuffer();
  const b = new Uint8Array(buf);
  if (b[0] !== 0x1f || b[1] !== 0x8b) return b;   // already decoded by the server
  const ds = new Response(new Blob([buf]).stream().pipeThrough(new DecompressionStream("gzip")));
  return new Uint8Array(await ds.arrayBuffer());
}

class LRU {
  constructor(n) { this.n = n; this.m = new Map(); }
  get(k) { const v = this.m.get(k); if (v !== undefined) { this.m.delete(k); this.m.set(k, v); } return v; }
  set(k, v) { this.m.set(k, v); while (this.m.size > this.n) this.m.delete(this.m.keys().next().value); }
}

function dequantLUT(f) {
  const lut = new Float32Array(256);
  for (let q = 0; q < 255; q++) {
    let x = q / 254;
    if (f.scale === "sqrt") x = x * x;
    lut[q] = f.min + x * (f.max - f.min);
  }
  lut[255] = NaN;
  return lut;
}

const KT = 1, FT_PER_M = 3.28084, IN_PER_MM = 1 / 25.4;
const dewpoint = (t, rh) => {
  if (!isFinite(t) || !isFinite(rh)) return NaN;
  const r = Math.max(0.5, Math.min(100, rh));
  const g = Math.log(r / 100) + 17.625 * t / (243.04 + t);
  return 243.04 * g / (17.625 - g);
};
const wdir = (u, v) => (Math.atan2(-u, -v) * 180 / Math.PI + 360) % 360;
const wspd = (u, v) => Math.hypot(u, v);

// ---------------------------------------------------------------------------
// color maps: stops [value, color]; step=true for categorical bands
// ---------------------------------------------------------------------------
const CMAPS = {
  temp: { stops: [[-40, "#f1e4ff"], [-30, "#c7a6f0"], [-20, "#8a6fd9"], [-10, "#3f63d4"], [-0.01, "#8ec8f5"],
    [0, "#b9e6b0"], [10, "#5dbb5a"], [18, "#f2e34c"], [25, "#f5a33a"], [32, "#e2452f"], [40, "#a3123a"], [46, "#6b0f3b"]] },
  dewpt: { stops: [[-30, "#8c6d46"], [-15, "#b99a6b"], [-5, "#ddd2a8"], [0, "#eef2d0"], [5, "#b6e3a3"],
    [10, "#60bf5f"], [15, "#1e8f3e"], [20, "#14648f"], [24, "#3a2d91"]] },
  windlow: { stops: [[0, "rgba(255,255,255,0)"], [10, "rgba(229,245,224,0.0)"], [15, "#d3eecd"], [20, "#a1d99b"],
    [30, "#41ab5d"], [40, "#fed976"], [50, "#fd8d3c"], [60, "#e31a1c"], [80, "#800026"]] },
  windhi: { stops: [[0, "rgba(255,255,255,0)"], [40, "rgba(198,219,239,0)"], [50, "#c6dbef"], [70, "#6baed6"],
    [90, "#2171b5"], [110, "#6a51a3"], [130, "#ae017e"], [150, "#e7298a"], [170, "#fdae6b"], [200, "#fff3b0"]] },
  rh: { stops: [[0, "rgba(255,255,255,0)"], [49.9, "rgba(255,255,255,0)"], [50, "#eef7e8"], [70, "#c7e9c0"],
    [80, "#74c476"], [90, "#238b45"], [100, "#00441b"]] },
  precip: { step: true, stops: [[0, "rgba(0,0,0,0)"], [0.01, "#c7e9c0"], [0.1, "#7fcdbb"], [0.25, "#41b6c4"], [0.5, "#2c7fb8"],
    [0.75, "#253494"], [1, "#fed976"], [1.5, "#fd8d3c"], [2, "#e31a1c"], [3, "#bd0026"], [5, "#d000d0"], [8, "#7a007a"]] },
  refl: { step: true, stops: [[-50, "rgba(0,0,0,0)"], [5, "#04e9e7"], [10, "#019ff4"], [15, "#0300f4"], [20, "#02fd02"],
    [25, "#01c501"], [30, "#008e00"], [35, "#fdf802"], [40, "#e5bc00"], [45, "#fd9500"], [50, "#fd0000"],
    [55, "#d40000"], [60, "#bc0000"], [65, "#f800fd"], [70, "#9854c6"]] },
  ceil: { step: true, stops: [[0, "#7a0177"], [200, "#c51b8a"], [500, "#e31a1c"], [1000, "#2c7fb8"],
    [2000, "#7fcdbb"], [3000, "#d9f0d3"], [5000, "rgba(0,0,0,0)"]] },
  vis: { step: true, stops: [[0, "#7a0177"], [0.5, "#c51b8a"], [1, "#e31a1c"], [2, "#fc8d59"], [3, "#2c7fb8"],
    [5, "rgba(0,0,0,0)"]] },
  frzl: { stops: [[0, "#3b0f70"], [2000, "#2c3f9e"], [4000, "#2b7bba"], [6000, "#3fb3c4"], [8000, "#8fd6a5"],
    [10000, "#e7f19a"], [12000, "#fcc66a"], [14000, "#f4874b"], [17000, "#d23f3f"], [20000, "#8c1b3a"]] },
  cape: { stops: [[0, "rgba(0,0,0,0)"], [99, "rgba(255,255,204,0)"], [100, "#ffffcc"], [500, "#ffeda0"],
    [1000, "#fed976"], [1500, "#feb24c"], [2000, "#fd8d3c"], [3000, "#fc4e2a"], [4000, "#e31a1c"], [5000, "#b10026"]] },
  pwat: { stops: [[0, "#fff7d6"], [0.25, "#f6e8a8"], [0.5, "#d9f0a3"], [0.75, "#a1d99b"], [1, "#41ab5d"],
    [1.5, "#238b8e"], [2, "#2c5aa0"], [2.5, "#4a2c8f"]] },
};

function parseColor(c) {
  if (c.startsWith("#")) {
    const n = parseInt(c.slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255, 255];
  }
  const m = c.match(/rgba?\(([^)]+)\)/);
  const p = m[1].split(",").map(Number);
  return [p[0], p[1], p[2], p.length > 3 ? Math.round(p[3] * 255) : 255];
}

function buildCmap(name) {
  const cm = CMAPS[name];
  const stops = cm.stops.map(([v, c]) => [v, parseColor(c)]);
  const vmin = stops[0][0], vmax = stops[stops.length - 1][0];
  const N = 1024, lut = new Uint8ClampedArray(N * 4);
  for (let i = 0; i < N; i++) {
    const v = vmin + (vmax - vmin) * i / (N - 1);
    let k = 0;
    while (k < stops.length - 2 && v >= stops[k + 1][0]) k++;
    let c;
    if (cm.step) c = v >= stops[k + 1][0] ? stops[k + 1][1] : stops[k][1];
    else {
      const [v0, c0] = stops[k], [v1, c1] = stops[k + 1];
      const t = Math.max(0, Math.min(1, (v - v0) / (v1 - v0 || 1)));
      c = c0.map((x, j) => x + (c1[j] - x) * t);
    }
    lut.set(c, i * 4);
  }
  return { name, stops, vmin, vmax, lut, N, step: !!cm.step };
}
const CM = {};
for (const k in CMAPS) CM[k] = buildCmap(k);

// ---------------------------------------------------------------------------
// products
// ---------------------------------------------------------------------------
const PRODUCTS = [];
function P(o) { PRODUCTS.push(o); }
const hgtInt = (p) => (p >= 700 ? 30 : p >= 500 ? 60 : 120);
const dam = (v) => (v / 10).toFixed(0);

P({ id: "sfc_t", group: "Surface", label: "Temperature (°C), wind, MSLP", fill: { f: ["t2m"], cm: "temp", units: "°C" },
  contours: [{ f: "mslp", int: 4 }, { f: "t2m", levels: [0], color: "#1d4ed8", dash: [6, 4], nolabel: true }], barbs: ["u10", "v10"] });
P({ id: "sfc_td", group: "Surface", label: "Dewpoint (°C), wind, MSLP", fill: { f: ["td2m"], cm: "dewpt", units: "°C" },
  contours: [{ f: "mslp", int: 4 }], barbs: ["u10", "v10"] });
P({ id: "sfc_wind", group: "Surface", label: "Wind speed (kt), MSLP", fill: { f: ["u10", "v10"], fn: (u, v) => wspd(u, v), cm: "windlow", units: "kt" },
  contours: [{ f: "mslp", int: 4 }], barbs: ["u10", "v10"] });
P({ id: "ceil", group: "Aviation", label: "Ceiling (ft AGL)", fill: { f: ["ceil"], cm: "ceil", units: "ft AGL" }, barbs: ["u10", "v10"] });
P({ id: "vis", group: "Aviation", label: "Visibility (SM)", fill: { f: ["vis"], cm: "vis", units: "SM" }, barbs: ["u10", "v10"] });
P({ id: "frzl", group: "Aviation", label: "Freezing level (ft MSL)", fill: { f: ["frzl"], cm: "frzl", units: "ft MSL" },
  contours: [{ f: "frzl", int: 2000, color: "#333", nolabel: false, label: (v) => (v / 1000).toFixed(0) + "k" }] });
P({ id: "refc", group: "Precipitation", label: "Simulated radar (dBZ)", fill: { f: ["refc"], cm: "refl", units: "dBZ" },
  contours: [{ f: "mslp", int: 4 }] });
P({ id: "apcp_int", group: "Precipitation", label: "Precip since previous hour shown (in)",
  fill: { f: ["apcp", "apcp@prev"], fn: (a, b) => Math.max(0, a - (isFinite(b) ? b : 0)) * IN_PER_MM, cm: "precip", units: "in" },
  contours: [{ f: "mslp", int: 4 }] });
P({ id: "apcp", group: "Precipitation", label: "Total precip since run start (in)",
  fill: { f: ["apcp"], fn: (a) => a * IN_PER_MM, cm: "precip", units: "in" }, contours: [{ f: "mslp", int: 4 }] });
P({ id: "pwat", group: "Precipitation", label: "Precipitable water (in)", fill: { f: ["pwat"], fn: (a) => a * IN_PER_MM, cm: "pwat", units: "in" },
  contours: [{ f: "mslp", int: 4 }] });
P({ id: "cape", group: "Precipitation", label: "Surface CAPE (J/kg)", fill: { f: ["cape"], cm: "cape", units: "J/kg" }, barbs: ["u10", "v10"] });
for (const p of [250, 300, 500, 700, 850, 925]) {
  P({ id: `wind_${p}`, group: "Wind & height", label: `${p} mb wind (kt), height (dam)`,
    fill: { f: [`u_${p}`, `v_${p}`], fn: (u, v) => wspd(u, v), cm: p >= 700 ? "windlow" : "windhi", units: "kt" },
    contours: [{ f: `hgt_${p}`, int: hgtInt(p), label: dam }], barbs: [`u_${p}`, `v_${p}`] });
}
for (const p of [500, 700, 850, 925]) {
  P({ id: `temp_${p}`, group: "Temperature", label: `${p} mb temperature (°C), wind, height`, fill: { f: [`t_${p}`], cm: "temp", units: "°C" },
    contours: [{ f: `hgt_${p}`, int: hgtInt(p), label: dam }, { f: `t_${p}`, levels: [0], color: "#1d4ed8", dash: [6, 4], nolabel: true }],
    barbs: [`u_${p}`, `v_${p}`] });
}
for (const p of [850, 925]) {
  P({ id: `td_${p}`, group: "Moisture", label: `${p} mb dewpoint (°C), wind, height`,
    fill: { f: [`t_${p}`, `rh_${p}`], fn: dewpoint, cm: "dewpt", units: "°C" },
    contours: [{ f: `hgt_${p}`, int: hgtInt(p), label: dam }], barbs: [`u_${p}`, `v_${p}`] });
}
for (const p of [300, 500, 700, 850, 925]) {
  P({ id: `rh_${p}`, group: "Moisture", label: `${p} mb relative humidity (%), wind`, fill: { f: [`rh_${p}`], cm: "rh", units: "%" },
    contours: [{ f: `hgt_${p}`, int: hgtInt(p), label: dam }], barbs: [`u_${p}`, `v_${p}`] });
}
const PRODUCT = Object.fromEntries(PRODUCTS.map((p) => [p.id, p]));

// ---------------------------------------------------------------------------
// state
// ---------------------------------------------------------------------------
const S = {
  index: null, model: null, run: null, meta: null, hourIdx: 0, product: "sfc_t",
  grid: null, field: null, contours: [], point: null, tab: "table", playing: false,
};
const metaCache = new Map();
const fieldCache = new LRU(160);
const tileCache = new LRU(24);

function availableHours() { return S.meta ? S.meta.available : []; }
function curHour() { return availableHours()[S.hourIdx]; }

async function loadField(model, run, h, name) {
  const key = `${model}/${run}/${h}/${name}`;
  let p = fieldCache.get(key);
  if (!p) {
    p = (async () => {
      const meta = metaCache.get(`${model}/${run}`);
      if (!meta.present.includes(name)) return null;
      const g = gridOf(meta);
      const d = await fetchGz(`${DATA}/${model}/${run}/maps/f${pad(h, 3)}/${name}.bin.gz`);
      if (d.length !== g.nlat * g.nlon) throw new Error("bad size " + name);
      // invert the gradient predictor: cumulative sum along x then along y (mod 256)
      const q = new Uint8Array(d);
      const nx = g.nlon, ny = g.nlat;
      for (let j = 0; j < ny; j++) { const o = j * nx; for (let i = 1; i < nx; i++) q[o + i] += q[o + i - 1]; }
      for (let j = 1; j < ny; j++) { const o = j * nx, u = o - nx; for (let i = 0; i < nx; i++) q[o + i] += q[u + i]; }
      const lut = dequantLUT(meta.fields[name]);
      const out = new Float32Array(q.length);
      for (let i = 0; i < q.length; i++) out[i] = lut[q[i]];
      return out;
    })();
    fieldCache.set(key, p);
    p.catch(() => fieldCache.m.delete(key));
  }
  return p;
}

function gridOf(meta) {
  const g = meta.map_grid;
  return { ...g, nlat: Math.round((g.lat1 - g.lat0) / g.res) + 1, nlon: Math.round((g.lon1 - g.lon0) / g.res) + 1 };
}

// Build the product's display field (Float32Array on the map grid)
async function buildProductField(prod, model, run, hours, hi) {
  const h = hours[hi];
  const meta = metaCache.get(`${model}/${run}`);
  const names = prod.fill.f;
  const arrs = await Promise.all(names.map((n) => {
    if (n.endsWith("@prev")) {
      if (hi === 0) return Promise.resolve(null);
      return loadField(model, run, hours[hi - 1], n.replace("@prev", ""));
    }
    return loadField(model, run, h, n);
  }));
  if (arrs[0] == null) return null;
  const fn = prod.fill.fn;
  if (!fn) return arrs[0];
  const n = arrs[0].length, out = new Float32Array(n);
  const a = arrs.map((x) => x || new Float32Array(n).fill(NaN));
  if (a.length === 1) for (let i = 0; i < n; i++) out[i] = fn(a[0][i]);
  else if (a.length === 2) for (let i = 0; i < n; i++) out[i] = fn(a[0][i], a[1][i]);
  void meta;
  return out;
}

// ---------------------------------------------------------------------------
// map + layers
// ---------------------------------------------------------------------------
const REGIONS = {
  conus: [[23, -126], [53, -64]],
  ne: [[38.3, -81.5], [46.8, -67.5]],
  lakes: [[40.8, -85], [46.3, -74.5]],
};
const map = L.map("map", { zoomControl: true, minZoom: 3, maxZoom: 11, worldCopyJump: false, preferCanvas: true });
map.createPane("labels");
map.getPane("labels").style.zIndex = 460;
map.getPane("labels").style.pointerEvents = "none";
L.tileLayer("https://{s}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}{r}.png", {
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a> | Data: NOAA/NCEP',
  subdomains: "abcd", maxZoom: 19,
}).addTo(map);
L.tileLayer("https://{s}.basemaps.cartocdn.com/light_only_labels/{z}/{x}/{y}{r}.png", {
  subdomains: "abcd", maxZoom: 19, pane: "labels",
}).addTo(map);

function sample(arr, g, lat, lon) {
  const fy = (g.lat1 - lat) / g.res, fx = (lon - g.lon0) / g.res;
  if (fy < 0 || fx < 0 || fy > g.nlat - 1 || fx > g.nlon - 1) return NaN;
  const j = Math.min(Math.floor(fy), g.nlat - 2), i = Math.min(Math.floor(fx), g.nlon - 2);
  const ty = fy - j, tx = fx - i, o = j * g.nlon + i;
  const a = arr[o], b = arr[o + 1], c = arr[o + g.nlon], d = arr[o + g.nlon + 1];
  if (isFinite(a) && isFinite(b) && isFinite(c) && isFinite(d))
    return (a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty;
  const n = (ty < 0.5 ? (tx < 0.5 ? a : b) : (tx < 0.5 ? c : d));
  return n;
}

const FillLayer = L.GridLayer.extend({
  createTile(coords) {
    const tile = document.createElement("canvas");
    const sz = this.getTileSize();
    tile.width = sz.x; tile.height = sz.y;
    const arr = S.field, g = S.grid, cm = S.cmap;
    if (!arr || !g || !cm) return tile;
    const ctx = tile.getContext("2d");
    const img = ctx.createImageData(sz.x, sz.y);
    const scale = 256 * Math.pow(2, coords.z);
    const lons = new Float64Array(sz.x);
    for (let x = 0; x < sz.x; x++) lons[x] = ((coords.x * sz.x + x + 0.5) / scale) * 360 - 180;
    const N1 = cm.N - 1, span = cm.vmax - cm.vmin, lut = cm.lut, px = img.data;
    for (let y = 0; y < sz.y; y++) {
      const my = Math.PI * (1 - 2 * (coords.y * sz.y + y + 0.5) / scale);
      const lat = (180 / Math.PI) * Math.atan(Math.sinh(my));
      for (let x = 0; x < sz.x; x++) {
        const v = sample(arr, g, lat, lons[x]);
        if (!isFinite(v)) continue;
        let k = Math.round(((v - cm.vmin) / span) * N1);
        k = k < 0 ? 0 : k > N1 ? N1 : k;
        const o = (y * sz.x + x) * 4, l = k * 4;
        px[o] = lut[l]; px[o + 1] = lut[l + 1]; px[o + 2] = lut[l + 2]; px[o + 3] = lut[l + 3];
      }
    }
    ctx.putImageData(img, 0, 0);
    return tile;
  },
});
map.getContainer().appendChild($("legend"));
const fill = new FillLayer({ opacity: 0.72, updateWhenZooming: false, keepBuffer: 1 }).addTo(map);

// ---- contours: marching squares on the grid, segments in Web-Mercator unit coords ----
function mercY(lat) {
  const s = Math.sin(lat * Math.PI / 180);
  return 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI);
}
function contourSegments(arr, g, levels) {
  const nx = g.nlon, ny = g.nlat, out = new Map();
  for (const lv of levels) out.set(lv, []);
  const lvSorted = [...levels].sort((a, b) => a - b);
  const mx = new Float64Array(nx), my = new Float64Array(ny);
  for (let i = 0; i < nx; i++) mx[i] = (g.lon0 + i * g.res + 180) / 360;
  for (let j = 0; j < ny; j++) my[j] = mercY(g.lat1 - j * g.res);
  for (let j = 0; j < ny - 1; j++) {
    for (let i = 0; i < nx - 1; i++) {
      const o = j * nx + i;
      const a = arr[o], b = arr[o + 1], c = arr[o + nx + 1], d = arr[o + nx];  // TL TR BR BL
      if (!(isFinite(a) && isFinite(b) && isFinite(c) && isFinite(d))) continue;
      const lo = Math.min(a, b, c, d), hi = Math.max(a, b, c, d);
      for (const lv of lvSorted) {
        if (lv <= lo) continue;
        if (lv > hi) break;
        const pts = [];
        const edge = (v0, v1, x0, y0, x1, y1) => {
          if ((v0 < lv) !== (v1 < lv)) {
            const t = (lv - v0) / (v1 - v0);
            pts.push(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t);
          }
        };
        edge(a, b, mx[i], my[j], mx[i + 1], my[j]);
        edge(b, c, mx[i + 1], my[j], mx[i + 1], my[j + 1]);
        edge(c, d, mx[i + 1], my[j + 1], mx[i], my[j + 1]);
        edge(d, a, mx[i], my[j + 1], mx[i], my[j]);
        const seg = out.get(lv);
        if (pts.length === 4) seg.push(...pts);
        else if (pts.length === 8) seg.push(...pts);   // saddle: pair edges in order
      }
    }
  }
  const res = [];
  for (const [lv, s] of out) if (s.length) res.push({ level: lv, segs: new Float32Array(s) });
  return res;
}
// light NaN-aware 3x3 smoothing so 8-bit packed fields give clean contours
function smooth(arr, g, passes = 2) {
  const nx = g.nlon, ny = g.nlat;
  let a = arr, b = new Float32Array(arr.length);
  for (let p = 0; p < passes; p++) {
    for (let j = 0; j < ny; j++) {
      for (let i = 0; i < nx; i++) {
        let s = 0, n = 0;
        for (let dj = -1; dj <= 1; dj++) {
          const jj = j + dj; if (jj < 0 || jj >= ny) continue;
          for (let di = -1; di <= 1; di++) {
            const ii = i + di; if (ii < 0 || ii >= nx) continue;
            const v = a[jj * nx + ii]; if (v === v) { s += v; n++; }
          }
        }
        const o = j * nx + i;
        b[o] = a[o] === a[o] && n ? s / n : NaN;
      }
    }
    const t = a === arr ? new Float32Array(arr.length) : a; a = b; b = t;
  }
  return a;
}
function levelsFor(spec, arr) {
  if (spec.levels) return spec.levels;
  let lo = Infinity, hi = -Infinity;
  for (let i = 0; i < arr.length; i += 7) { const v = arr[i]; if (isFinite(v)) { if (v < lo) lo = v; if (v > hi) hi = v; } }
  if (!isFinite(lo)) return [];
  const out = [];
  for (let v = Math.ceil(lo / spec.int) * spec.int; v <= hi; v += spec.int) out.push(v);
  return out;
}

// ---- overlay canvas: contours, labels, wind barbs, point marker ----
const overlay = document.createElement("canvas");
overlay.className = "overlay-canvas";
map.getContainer().appendChild(overlay);
let barbU = null, barbV = null, drawPending = false;

function requestDraw() {
  if (drawPending) return;
  drawPending = true;
  requestAnimationFrame(() => { drawPending = false; drawOverlay(); });
}
function drawOverlay() {
  const size = map.getSize(), dpr = window.devicePixelRatio || 1;
  if (overlay.width !== size.x * dpr || overlay.height !== size.y * dpr) {
    overlay.width = size.x * dpr; overlay.height = size.y * dpr;
    overlay.style.width = size.x + "px"; overlay.style.height = size.y + "px";
  }
  const ctx = overlay.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, size.x, size.y);
  const z = map.getZoom(), scale = 256 * Math.pow(2, z);
  const origin = map.containerPointToLayerPoint([0, 0]).add(map.getPixelOrigin());
  const ox = origin.x, oy = origin.y;
  const labels = [];
  // contours
  for (const c of S.contours) {
    ctx.strokeStyle = c.spec.color || "#202020";
    ctx.lineWidth = c.spec.width || 1.1;
    ctx.setLineDash(c.spec.dash || []);
    ctx.beginPath();
    for (const L1 of c.lines) {
      const s = L1.segs;
      for (let k = 0; k < s.length; k += 4) {
        ctx.moveTo(s[k] * scale - ox, s[k + 1] * scale - oy);
        ctx.lineTo(s[k + 2] * scale - ox, s[k + 3] * scale - oy);
      }
    }
    ctx.stroke();
    ctx.setLineDash([]);
    if (c.spec.nolabel) continue;
    ctx.font = "600 11px system-ui, sans-serif";
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    for (const L1 of c.lines) {
      const s = L1.segs, txt = (c.spec.label || ((v) => v.toFixed(0)))(L1.level);
      const stride = Math.max(4, Math.floor(s.length / 4 / 40) * 4);
      for (let k = 0; k < s.length; k += stride) {
        const x = s[k] * scale - ox, y = s[k + 1] * scale - oy;
        if (x < 20 || y < 20 || x > size.x - 20 || y > size.y - 40) continue;
        if (labels.some(([lx, ly]) => (lx - x) ** 2 + (ly - y) ** 2 < 170 * 170)) continue;
        labels.push([x, y]);
        const w = ctx.measureText(txt).width + 6;
        ctx.fillStyle = "rgba(255,255,255,0.85)";
        ctx.fillRect(x - w / 2, y - 7, w, 14);
        ctx.fillStyle = c.spec.color || "#202020";
        ctx.fillText(txt, x, y + 0.5);
        if (labels.length > 80) break;
      }
    }
  }
  // wind barbs
  if (barbU && barbV) {
    const step = 42;
    ctx.strokeStyle = "#1b2330"; ctx.fillStyle = "#1b2330"; ctx.lineWidth = 1.2;
    for (let sy = step / 2; sy < size.y; sy += step) {
      for (let sx = step / 2; sx < size.x; sx += step) {
        const ll = map.containerPointToLatLng([sx, sy]);
        const u = sample(barbU, S.grid, ll.lat, ll.lng), v = sample(barbV, S.grid, ll.lat, ll.lng);
        if (!isFinite(u) || !isFinite(v)) continue;
        drawBarb(ctx, sx, sy, u, v, 17);
      }
    }
  }
  // selected point
  if (S.point) {
    const p = map.latLngToContainerPoint([S.point.lat, S.point.lon]);
    ctx.lineWidth = 3; ctx.strokeStyle = "#fff";
    ctx.beginPath(); ctx.arc(p.x, p.y, 7, 0, 2 * Math.PI); ctx.stroke();
    ctx.lineWidth = 2; ctx.strokeStyle = "#e11d48";
    ctx.beginPath(); ctx.arc(p.x, p.y, 7, 0, 2 * Math.PI); ctx.stroke();
  }
}

// Standard wind barb. u, v in kt (earth-relative); staff points into the wind.
function drawBarb(ctx, x, y, u, v, len) {
  const spd = Math.hypot(u, v);
  if (spd < 2.5) { ctx.beginPath(); ctx.arc(x, y, 3, 0, 2 * Math.PI); ctx.stroke(); return; }
  const dir = Math.atan2(-u, -v);                       // direction wind comes from (radians, 0 = N)
  const sx = Math.sin(dir), sy = -Math.cos(dir);        // unit vector toward the tip (screen coords)
  const fx = -sy, fy = sx;                              // feather direction (clockwise of staff)
  const tx = x + sx * len, ty = y + sy * len;
  ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(tx, ty); ctx.stroke();
  let r = Math.round(spd / 5) * 5, pos = 0;
  const gap = 3.2, flen = 8;
  while (r >= 50) {                                     // pennants
    const ax = tx - sx * pos, ay = ty - sy * pos;
    const bx = tx - sx * (pos + 5), by = ty - sy * (pos + 5);
    ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(ax + fx * flen - sx * 2.5, ay + fy * flen - sy * 2.5);
    ctx.lineTo(bx, by); ctx.closePath(); ctx.fill();
    pos += 6.5; r -= 50;
  }
  while (r >= 10) {
    const ax = tx - sx * pos, ay = ty - sy * pos;
    ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(ax + fx * flen + sx * 2, ay + fy * flen + sy * 2); ctx.stroke();
    pos += gap; r -= 10;
  }
  if (r >= 5) {
    if (pos === 0) pos = gap;
    const ax = tx - sx * pos, ay = ty - sy * pos;
    ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(ax + fx * flen * 0.5 + sx, ay + fy * flen * 0.5 + sy); ctx.stroke();
  }
}

map.on("move zoom resize viewreset", requestDraw);
map.on("zoomstart", () => { overlay.style.visibility = "hidden"; });
map.on("zoomend", () => { overlay.style.visibility = ""; requestDraw(); saveHash(); });
map.on("moveend", saveHash);
map.on("click", (e) => selectPoint(e.latlng.lat, e.latlng.lng, null));

// ---------------------------------------------------------------------------
// legend
// ---------------------------------------------------------------------------
function drawLegend(prod) {
  const el = $("legend");
  const cm = CM[prod.fill.cm];
  el.innerHTML = `<div class="t">${prod.label}</div>`;
  const W = Math.max(180, Math.min(300, map.getSize().x - 60)), H = 12, cv = document.createElement("canvas");
  const dpr = window.devicePixelRatio || 1;
  cv.width = W * dpr; cv.height = (H + 16) * dpr; cv.style.width = W + "px"; cv.style.height = (H + 16) + "px";
  const ctx = cv.getContext("2d"); ctx.scale(dpr, dpr);
  const stops = cm.stops, n = stops.length;
  ctx.font = "10px system-ui, sans-serif"; ctx.textAlign = "center"; ctx.fillStyle = "#1b2330";
  if (cm.step) {
    const w = W / n;
    stops.forEach(([v, c], i) => {
      ctx.fillStyle = `rgba(${c[0]},${c[1]},${c[2]},${Math.max(c[3], 0) / 255})`;
      ctx.fillRect(i * w, 0, w, H);
      ctx.strokeStyle = "#c9d0da"; ctx.strokeRect(i * w + 0.5, 0.5, w - 1, H - 1);
      ctx.fillStyle = "#1b2330";
      if (v > -50) ctx.fillText(fmtLegend(v), Math.max(8, i * w), H + 11);
    });
  } else {
    for (let x = 0; x < W; x++) {
      const k = Math.round((x / (W - 1)) * (cm.N - 1)) * 4;
      ctx.fillStyle = `rgba(${cm.lut[k]},${cm.lut[k + 1]},${cm.lut[k + 2]},${cm.lut[k + 3] / 255})`;
      ctx.fillRect(x, 0, 1, H);
    }
    ctx.fillStyle = "#1b2330";
    const span = cm.vmax - cm.vmin, raw = span / 6, mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const stp = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((x) => x >= raw);
    for (let v = Math.ceil(cm.vmin / stp) * stp; v <= cm.vmax + 1e-9; v += stp) {
      const x = Math.min(W - 10, Math.max(10, ((v - cm.vmin) / span) * W));
      ctx.fillRect(((v - cm.vmin) / span) * W, H - 3, 1, 3);
      ctx.fillText(fmtLegend(v), x, H + 11);
    }
  }
  el.appendChild(cv);
}
function fmtLegend(v) {
  const a = Math.abs(v);
  if (a >= 1000) return (v / 1000).toFixed(a % 1000 ? 1 : 0) + "k";
  if (a > 0 && a < 1) return v.toFixed(2).replace(/^0/, "");
  return Math.round(v).toString();
}

// ---------------------------------------------------------------------------
// rendering the current selection
// ---------------------------------------------------------------------------
let renderSeq = 0;
async function render() {
  if (!S.meta) return;
  const seq = ++renderSeq;
  const prod = PRODUCT[S.product];
  const hours = availableHours();
  if (!hours.length) return;
  const h = hours[S.hourIdx];
  const model = S.model, run = S.run;
  const g = gridOf(S.meta);
  const busy = setTimeout(() => { $("loading").hidden = false; }, 250);
  try {
    const [field, bu, bv, ...cfs] = await Promise.all([
      buildProductField(prod, model, run, hours, S.hourIdx),
      prod.barbs ? loadField(model, run, h, prod.barbs[0]) : null,
      prod.barbs ? loadField(model, run, h, prod.barbs[1]) : null,
      ...(prod.contours || []).map((c) => loadField(model, run, h, c.f)),
    ]);
    if (seq !== renderSeq) return;
    S.grid = g; S.field = field; S.cmap = CM[prod.fill.cm];
    barbU = bu; barbV = bv;
    S.contours = (prod.contours || []).map((spec, i) => {
      const a = cfs[i];
      if (!a) return null;
      const sm = smooth(a, g, S.meta.map_grid.res < 0.2 ? 2 : 1);
      return { spec, lines: contourSegments(sm, g, levelsFor(spec, sm)) };
    }).filter(Boolean);
    fill.redraw();
    drawLegend(prod);
    requestDraw();
    showBanner(field ? "" : `${prod.label} is not available from ${S.index.models[model].short}.`);
  } catch (e) {
    console.error(e);
    if (seq === renderSeq) showBanner("Could not load data for this hour: " + e.message);
  } finally {
    clearTimeout(busy);
    if (seq === renderSeq) $("loading").hidden = true;
  }
  updateValid();
  prefetch();
}

function prefetch() {
  const hours = availableHours(), prod = PRODUCT[S.product];
  const nx = hours[S.hourIdx + 1];
  if (nx == null) return;
  const names = [...prod.fill.f.filter((n) => !n.endsWith("@prev")), ...(prod.barbs || []), ...(prod.contours || []).map((c) => c.f)];
  for (const n of new Set(names)) loadField(S.model, S.run, nx, n).catch(() => {});
}

let bannerEl = null;
function showBanner(msg) {
  if (!msg) { if (bannerEl) { bannerEl.remove(); bannerEl = null; } return; }
  if (!bannerEl) { bannerEl = document.createElement("div"); bannerEl.className = "banner"; $("main").appendChild(bannerEl); }
  bannerEl.textContent = msg;
}

// ---------------------------------------------------------------------------
// time formatting
// ---------------------------------------------------------------------------
function initDate() { return new Date(S.meta.init); }
function validDate(h) { return new Date(initDate().getTime() + h * 3600e3); }
function fmtZ(d) { return `${DAYS[d.getUTCDay()]} ${pad(d.getUTCHours())}Z`; }
function fmtLocal(d) {
  let h = d.getHours(); const ap = h >= 12 ? "pm" : "am"; h = h % 12 || 12;
  return `${DAYS[d.getDay()]} ${h}${d.getMinutes() ? ":" + pad(d.getMinutes()) : ""}${ap}`;
}
function fmtRun(rid, complete, nav, n) {
  const d = new Date(Date.UTC(+rid.slice(0, 4), +rid.slice(4, 6) - 1, +rid.slice(6, 8), +rid.slice(8, 10)));
  return `${pad(d.getUTCHours())}Z ${DAYS[d.getUTCDay()]} ${pad(d.getUTCDate())} ${MONS[d.getUTCMonth()]}` +
    (complete ? "" : `  (${nav}/${n} hrs)`);
}
function updateValid() {
  const h = curHour();
  if (h == null) { $("valid").textContent = ""; return; }
  const d = validDate(h);
  $("valid").innerHTML = `F${pad(h, 3)} · ${fmtZ(d)} <small>(${fmtLocal(d)} local)</small>`;
  $("hour").value = S.hourIdx;
  if (S.point) highlightRow();
}

// ---------------------------------------------------------------------------
// model / run / product selection
// ---------------------------------------------------------------------------
function buildModelButtons() {
  const box = $("models");
  box.innerHTML = "";
  for (const m of S.index.order) {
    const info = S.index.models[m];
    const b = document.createElement("button");
    b.textContent = info.short; b.title = info.label; b.dataset.model = m;
    b.disabled = !info.runs.length;
    b.onclick = () => selectModel(m);
    box.appendChild(b);
  }
}

function pickDefaultRun(m) {
  const runs = S.index.models[m].runs;
  const complete = runs.find((r) => r.complete);
  // prefer the newest run once it has at least a third of its hours, else newest complete
  const newest = runs[0];
  if (newest && (newest.complete || newest.available.length >= newest.nhours / 3)) return newest.run;
  return (complete || newest || {}).run;
}

async function selectModel(m, run, keepValid = true) {
  const prevValid = S.meta && curHour() != null ? validDate(curHour()).getTime() : null;
  S.model = m;
  for (const b of $("models").children) b.classList.toggle("on", b.dataset.model === m);
  const runs = S.index.models[m].runs;
  const sel = $("run");
  sel.innerHTML = "";
  for (const r of runs) {
    const o = document.createElement("option");
    o.value = r.run; o.textContent = fmtRun(r.run, r.complete, r.available.length, r.nhours);
    sel.appendChild(o);
  }
  run = runs.some((r) => r.run === run) ? run : pickDefaultRun(m);
  await selectRun(run, keepValid ? prevValid : null);
  updateStatus();
}

async function selectRun(run, targetValid) {
  S.run = run;
  $("run").value = run;
  const key = `${S.model}/${run}`;
  if (!metaCache.has(key)) metaCache.set(key, await fetchJSON(`${DATA}/${S.model}/${run}/meta.json`));
  S.meta = metaCache.get(key);
  buildProductMenu();
  const hours = availableHours();
  $("hour").max = Math.max(0, hours.length - 1);
  // keep the same valid time when switching model/run, if possible
  if (targetValid != null) {
    let best = 0, bd = Infinity;
    hours.forEach((h, i) => { const dd = Math.abs(validDate(h).getTime() - targetValid); if (dd < bd) { bd = dd; best = i; } });
    S.hourIdx = best;
  } else {
    S.hourIdx = Math.min(S.hourIdx, hours.length - 1);
  }
  await render();
  if (S.point) loadPoint();
  saveHash();
}

function buildProductMenu() {
  const sel = $("product");
  const present = new Set(S.meta.present);
  sel.innerHTML = "";
  const groups = {};
  for (const p of PRODUCTS) {
    const need = p.fill.f.map((n) => n.replace("@prev", ""));
    if (!need.every((n) => present.has(n))) continue;
    (groups[p.group] = groups[p.group] || []).push(p);
  }
  for (const [g, ps] of Object.entries(groups)) {
    const og = document.createElement("optgroup"); og.label = g;
    for (const p of ps) { const o = document.createElement("option"); o.value = p.id; o.textContent = p.label; og.appendChild(o); }
    sel.appendChild(og);
  }
  if (!sel.querySelector(`option[value="${S.product}"]`)) S.product = sel.options[0] ? sel.options[0].value : S.product;
  sel.value = S.product;
}

function updateStatus() {
  const el = $("status");
  const info = S.index.models[S.model];
  const newest = info.runs[0];
  let color = "var(--ok)", txt = "", tip = [];
  if (!newest) { color = "var(--bad)"; txt = "no data"; }
  else {
    const init = new Date(Date.UTC(+newest.run.slice(0, 4), +newest.run.slice(4, 6) - 1, +newest.run.slice(6, 8), +newest.run.slice(8, 10)));
    const age = (Date.now() - init.getTime()) / 3600e3;
    const lim = S.model === "rap" ? [3, 6] : [9, 15];
    color = age <= lim[0] ? "var(--ok)" : age <= lim[1] ? "var(--warn)" : "var(--bad)";
    txt = `latest ${pad(init.getUTCHours())}Z run · ${age < 1 ? "<1" : Math.round(age)}h ago`;
    tip.push(`Newest ${info.short} run: ${newest.run} (${newest.available.length}/${newest.nhours} hours)`);
  }
  if (info.status && info.status.error) { tip.push("Last error: " + info.status.error); if (color === "var(--ok)") color = "var(--warn)"; }
  if (S.index.generated) tip.push("Site data updated " + S.index.generated.replace("T", " ").replace("Z", " UTC"));
  if (S.meta && S.meta.source) tip.push("Source: " + S.meta.source);
  el.innerHTML = `<span class="dot" style="background:${color}"></span><span>${txt}</span>`;
  el.title = tip.join("\n");
}

// ---------------------------------------------------------------------------
// point forecast
// ---------------------------------------------------------------------------
async function selectPoint(lat, lon, label) {
  const g = S.meta && S.meta.point_grid;
  if (!g) return;
  if (lat < g.lat0 || lon < g.lon0 || lat > g.lat0 + (g.ny - 1) * g.res || lon > g.lon0 + (g.nx - 1) * g.res) return;
  S.point = { lat, lon, label };
  $("panel").hidden = false;
  map.invalidateSize();
  requestDraw();
  saveHash();
  loadPoint();
}

async function loadPoint() {
  const pt = S.point, meta = S.meta, g = meta.point_grid;
  const iy = Math.round((pt.lat - g.lat0) / g.res), ix = Math.round((pt.lon - g.lon0) / g.res);
  const ty = Math.floor(iy / g.tile), tx = Math.floor(ix / g.tile);
  const key = `${S.model}/${S.run}/${ty}_${tx}/${meta.available.length}`;
  const glat = g.lat0 + iy * g.res, glon = g.lon0 + ix * g.res;
  $("ptitle").textContent = pt.label || `${Math.abs(pt.lat).toFixed(2)}°${pt.lat >= 0 ? "N" : "S"} ${Math.abs(pt.lon).toFixed(2)}°${pt.lon >= 0 ? "E" : "W"}`;
  $("psub").textContent = `${S.index.models[S.model].short} ${fmtRun(S.run, true)} · loading…`;
  let tile = tileCache.get(key);
  if (!tile) {
    tile = fetchGz(`${DATA}/${S.model}/${S.run}/points/${ty}_${tx}.bin.gz?v=${meta.available.length}`).catch((e) => (e.status === 404 ? null : Promise.reject(e)));
    tileCache.set(key, tile);
    tile.catch(() => tileCache.m.delete(key));
  }
  let bytes;
  try { bytes = await tile; } catch (e) { $("pbody").innerHTML = `<div class="note">Could not load point data: ${e.message}</div>`; return; }
  if (pt !== S.point || meta !== S.meta) return;
  if (!bytes) { $("pbody").innerHTML = `<div class="note">This model has no data at this location.</div>`; $("psub").textContent = ""; return; }
  const T = g.tile, nf = meta.point_fields.length, nh = meta.hours.length;
  const j = iy % T, i = ix % T;
  const luts = meta.point_fields.map((f) => dequantLUT(meta.fields[f]));
  const series = {};
  meta.point_fields.forEach((f, fi) => {
    const a = new Float32Array(nh);
    for (let hi = 0; hi < nh; hi++) a[hi] = luts[fi][bytes[((hi * nf + fi) * T + j) * T + i]];
    series[f] = a;
  });
  S.pdata = { series, glat, glon };
  const elev = series.hgtsfc ? series.hgtsfc.find((x) => isFinite(x)) : NaN;
  $("psub").textContent = `${S.index.models[S.model].short} ${fmtRun(S.run, true)} · grid point ${glat.toFixed(2)}, ${glon.toFixed(2)}` +
    (isFinite(elev) ? ` · model elev ${Math.round(elev)} ft` : "");
  renderPanel();
}

function hourRows() {
  const meta = S.meta;
  return meta.hours.map((h, hi) => ({ h, hi })).filter((r) => meta.available.includes(r.h));
}

function renderPanel() {
  if (!S.pdata) return;
  const body = $("pbody");
  if (S.tab === "table") body.innerHTML = surfaceTable();
  else if (S.tab === "aloft") body.innerHTML = aloftTable();
  else { body.innerHTML = `<div class="sound-wrap"><canvas id="skewt" width="520" height="560"></canvas><div class="note" id="snote"></div></div>`; drawSkewT(); }
  body.querySelectorAll("tr[data-h]").forEach((tr) => {
    tr.onclick = () => { const i = availableHours().indexOf(+tr.dataset.h); if (i >= 0) { S.hourIdx = i; render(); if (S.tab === "sounding") renderPanel(); } };
  });
  highlightRow();
}

function highlightRow() {
  const h = curHour();
  $("pbody").querySelectorAll("tr[data-h]").forEach((tr) => tr.classList.toggle("cur", +tr.dataset.h === h));
  if (S.tab === "sounding" && document.getElementById("skewt")) drawSkewT();
}

const fmt = {
  t: (v) => (isFinite(v) ? Math.round(v).toString() : "–"),
  wind: (u, v) => {
    if (!isFinite(u) || !isFinite(v)) return "–";
    const s = Math.round(wspd(u, v));
    if (s < 3) return "calm";
    return `${pad(Math.round(wdir(u, v) / 10) * 10 || 360, 3)}/${s}`;
  },
};
function catClass(ceil, vis) {
  if ((isFinite(ceil) && ceil < 500) || (isFinite(vis) && vis < 1)) return "cat-lifr";
  if ((isFinite(ceil) && ceil < 1000) || (isFinite(vis) && vis < 3)) return "cat-ifr";
  if ((isFinite(ceil) && ceil <= 3000) || (isFinite(vis) && vis <= 5)) return "cat-mvfr";
  return "";
}
function dayCell(h, lastDay) {
  const d = validDate(h);
  const day = DAYS[d.getDay()];
  let hh = d.getHours(); const ap = hh >= 12 ? "p" : "a"; hh = hh % 12 || 12;
  return { day, html: `${day === lastDay ? "" : `<span class="day">${day}</span> `}${pad(d.getUTCHours())}Z <span class="day">${hh}${ap}</span>` };
}

function surfaceTable() {
  const s = S.pdata.series;
  let rows = "", lastDay = "", prevA = 0;
  for (const { h, hi } of hourRows()) {
    const dc = dayCell(h, lastDay); lastDay = dc.day;
    const ceil = s.ceil ? s.ceil[hi] : NaN, vis = s.vis ? s.vis[hi] : NaN;
    const cat = catClass(ceil < 19000 ? ceil : NaN, vis);
    const a = s.apcp ? s.apcp[hi] : NaN;
    const pr = isFinite(a) ? Math.max(0, a - prevA) * IN_PER_MM : NaN;
    if (isFinite(a)) prevA = a;
    rows += `<tr data-h="${h}"><td>${dc.html}</td>` +
      `<td>${fmt.t(s.t2m[hi])}/${fmt.t(s.td2m ? s.td2m[hi] : NaN)}</td>` +
      `<td>${fmt.wind(s.u10[hi], s.v10[hi])}</td>` +
      `<td class="${cat}">${!isFinite(ceil) ? "–" : ceil >= 19000 ? "none" : Math.round(ceil / 100) * 100}</td>` +
      `<td class="${cat}">${!isFinite(vis) ? "–" : vis >= 9.9 ? "10+" : vis < 1 ? vis.toFixed(1) : Math.round(vis * 2) / 2}</td>` +
      `<td>${isFinite(s.mslp[hi]) ? s.mslp[hi].toFixed(0) : "–"}</td>` +
      `<td>${!isFinite(pr) ? "–" : pr < 0.005 ? "" : pr.toFixed(2)}</td>` +
      `<td>${s.refc && isFinite(s.refc[hi]) && s.refc[hi] >= 5 ? Math.round(s.refc[hi]) : ""}</td>` +
      `<td>${s.frzl && isFinite(s.frzl[hi]) ? Math.round(s.frzl[hi] / 100) * 100 : "–"}</td>` +
      `<td>${s.cape && isFinite(s.cape[hi]) && s.cape[hi] >= 50 ? Math.round(s.cape[hi] / 10) * 10 : ""}</td></tr>`;
  }
  return `<table class="pt"><thead><tr><th>Valid</th><th title="2 m temperature / dewpoint">T/Td °C</th><th title="10 m wind, degrees true / kt">Wind</th>` +
    `<th title="Ceiling, ft AGL">Ceil ft</th><th title="Visibility, statute miles">Vis SM</th><th>MSLP</th>` +
    `<th title="Precipitation since previous row, inches">Pcp in</th><th title="Simulated composite reflectivity">dBZ</th>` +
    `<th title="Freezing level, ft MSL">Frz ft</th><th>CAPE</th></tr></thead><tbody>${rows}</tbody></table>` +
    `<div class="note">Times: UTC and your local time. Ceiling/visibility colors: <span class="cat-mvfr">MVFR</span>, <span class="cat-ifr">IFR</span>, <span class="cat-lifr">LIFR</span>. ` +
    `Values are raw model output at the nearest ${S.meta.point_grid.res}° grid point${S.meta.ceil_ref ? ` (ceiling from ${S.meta.ceil_ref})` : ""}.</div>`;
}

const FD_ALTS = [3000, 6000, 9000, 12000, 18000, 24000, 30000, 34000, 39000];
// interpolate u, v, t to an altitude (ft MSL) using the pressure-level heights
function aloftAt(hi, alt) {
  const s = S.pdata.series, levels = S.meta.levels;
  const zs = s.hgtsfc ? s.hgtsfc[hi] : 0;
  const pts = [];
  for (const p of levels) {
    const z = s[`hgt_${p}`] ? s[`hgt_${p}`][hi] * FT_PER_M : NaN;
    if (!isFinite(z) || z < zs) continue;
    pts.push({ z, u: s[`u_${p}`][hi], v: s[`v_${p}`][hi], t: s[`t_${p}`][hi] });
  }
  pts.sort((a, b) => a.z - b.z);
  for (let k = 0; k < pts.length - 1; k++) {
    const a = pts[k], b = pts[k + 1];
    if (alt >= a.z && alt <= b.z) {
      const f = (alt - a.z) / (b.z - a.z);
      return { u: a.u + (b.u - a.u) * f, v: a.v + (b.v - a.v) * f, t: a.t + (b.t - a.t) * f };
    }
  }
  const top = pts[pts.length - 1];
  if (top && alt > top.z && alt - top.z < 1500) return { u: top.u, v: top.v, t: top.t };
  return null;
}
function aloftTable() {
  const s = S.pdata.series;
  let rows = "", lastDay = "";
  for (const { h, hi } of hourRows()) {
    const dc = dayCell(h, lastDay); lastDay = dc.day;
    const zs = s.hgtsfc ? s.hgtsfc[hi] : 0;
    let cells = "";
    for (const alt of FD_ALTS) {
      const r = alt - zs < 1500 ? null : aloftAt(hi, alt);
      if (!r) { cells += "<td>–</td>"; continue; }
      const spd = Math.round(wspd(r.u, r.v));
      const w = spd < 5 ? "lt var" : `${pad(Math.round(wdir(r.u, r.v) / 10) * 10 || 360, 3)}/${spd}`;
      const t = alt === 3000 ? "" : ` <span class="day">${r.t >= 0.5 ? "+" : ""}${Math.round(r.t)}</span>`;
      cells += `<td>${w}${t}</td>`;
    }
    rows += `<tr data-h="${h}"><td>${dc.html}</td>${cells}</tr>`;
  }
  return `<table class="pt"><thead><tr><th>Valid</th>${FD_ALTS.map((a) => `<th>${a >= 18000 ? "FL" + a / 100 : a.toLocaleString()}</th>`).join("")}</tr></thead>` +
    `<tbody>${rows}</tbody></table>` +
    `<div class="note">Wind direction °true / speed kt, temperature °C (not shown at 3,000 ft). Interpolated from model pressure levels; ` +
    `omitted within 1,500 ft of model terrain. "lt var" = under 5 kt.</div>`;
}

function drawSkewT() {
  const cv = document.getElementById("skewt");
  if (!cv || !S.pdata) return;
  const h = curHour(), hi = S.meta.hours.indexOf(h);
  const s = S.pdata.series, levels = S.meta.levels;
  const dpr = window.devicePixelRatio || 1, W = 520, H = 560;
  cv.width = W * dpr; cv.height = H * dpr;
  const ctx = cv.getContext("2d"); ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, W, H);
  const L0 = 52, R0 = W - 62, T0 = 10, B0 = H - 28;
  const pTop = 200, pBot = 1050, tMin = -40, tMax = 40;
  const lp = Math.log;
  const Y = (p) => B0 - (B0 - T0) * (lp(pBot) - lp(p)) / (lp(pBot) - lp(pTop));
  const skew = (R0 - L0) / (B0 - T0);
  const X = (t, p) => L0 + (t - tMin) / (tMax - tMin) * (R0 - L0) + (B0 - Y(p)) * skew;
  ctx.save();
  ctx.beginPath(); ctx.rect(L0, T0, R0 - L0, B0 - T0); ctx.clip();
  // dry adiabats
  ctx.strokeStyle = "#f0d9c2"; ctx.lineWidth = 1;
  for (let th = 250; th <= 450; th += 10) {
    ctx.beginPath();
    for (let p = pBot; p >= pTop; p -= 10) { const t = th * Math.pow(p / 1000, 0.2857) - 273.15; const x = X(t, p), y = Y(p); p === pBot ? ctx.moveTo(x, y) : ctx.lineTo(x, y); }
    ctx.stroke();
  }
  // isotherms
  for (let t = -100; t <= 40; t += 10) {
    ctx.strokeStyle = t === 0 ? "#2563eb" : "#d6dde7"; ctx.lineWidth = t === 0 ? 1.4 : 1;
    ctx.beginPath(); ctx.moveTo(X(t, pBot), Y(pBot)); ctx.lineTo(X(t, pTop), Y(pTop)); ctx.stroke();
  }
  ctx.restore();
  // isobars + height labels
  ctx.font = "10px system-ui, sans-serif"; ctx.fillStyle = "#5d6878"; ctx.textAlign = "right"; ctx.textBaseline = "middle";
  for (const p of [1000, 925, 850, 700, 500, 400, 300, 250, 200]) {
    ctx.strokeStyle = "#c9d0da"; ctx.beginPath(); ctx.moveTo(L0, Y(p)); ctx.lineTo(R0, Y(p)); ctx.stroke();
    ctx.fillText(`${p}`, L0 - 4, Y(p) - 6);
    const z = s[`hgt_${p}`] ? s[`hgt_${p}`][hi] * FT_PER_M : NaN;
    if (isFinite(z)) { ctx.fillStyle = "#94a0b0"; ctx.fillText(`${(z / 1000).toFixed(1)}k`, L0 - 4, Y(p) + 6); ctx.fillStyle = "#5d6878"; }
  }
  ctx.textAlign = "center";
  for (let t = -40; t <= 40; t += 10) ctx.fillText(`${t}`, X(t, pBot), B0 + 12);
  ctx.strokeStyle = "#8a96a8"; ctx.strokeRect(L0, T0, R0 - L0, B0 - T0);
  // profile (above ground)
  const zs = s.hgtsfc ? s.hgtsfc[hi] / FT_PER_M : 0;
  const prof = levels.map((p) => ({ p, z: s[`hgt_${p}`] ? s[`hgt_${p}`][hi] : NaN, t: s[`t_${p}`][hi], rh: s[`rh_${p}`][hi], u: s[`u_${p}`][hi], v: s[`v_${p}`][hi] }))
    .filter((r) => isFinite(r.t) && (!isFinite(r.z) || r.z >= zs));
  // surface point from 2 m values; surface pressure estimated from MSLP and terrain
  const mslp = s.mslp ? s.mslp[hi] : NaN;
  const psfc = isFinite(mslp) ? mslp * Math.pow(1 - 0.0065 * zs / 288.15, 5.255) : NaN;
  const sfc = isFinite(psfc) && isFinite(s.t2m[hi]) ? { p: psfc, t: s.t2m[hi], td: s.td2m ? s.td2m[hi] : NaN } : null;
  const line = (pts, color) => {
    ctx.save(); ctx.beginPath(); ctx.rect(L0, T0, R0 - L0, B0 - T0); ctx.clip();
    ctx.strokeStyle = color; ctx.lineWidth = 2.4; ctx.beginPath();
    pts.forEach(([t, p], k) => { const x = X(t, p), y = Y(p); k ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
    ctx.stroke(); ctx.restore();
  };
  const tp = prof.map((r) => [r.t, r.p]), dp = prof.map((r) => [dewpoint(r.t, r.rh), r.p]).filter(([t]) => isFinite(t));
  if (sfc && (!prof.length || sfc.p > prof[0].p)) { tp.unshift([sfc.t, sfc.p]); if (isFinite(sfc.td)) dp.unshift([sfc.td, sfc.p]); }
  line(tp, "#dc2626");
  line(dp, "#16a34a");
  // wind barbs on the right
  ctx.strokeStyle = "#1b2330"; ctx.fillStyle = "#1b2330"; ctx.lineWidth = 1.2;
  ctx.beginPath(); ctx.moveTo(R0 + 30, T0); ctx.lineTo(R0 + 30, B0); ctx.strokeStyle = "#d6dde7"; ctx.stroke(); ctx.strokeStyle = "#1b2330";
  for (const r of prof) if (isFinite(r.u)) drawBarb(ctx, R0 + 30, Y(r.p), r.u, r.v, 20);
  // freezing level
  const frz = s.frzl ? s.frzl[hi] : NaN;
  const note = document.getElementById("snote");
  if (note) note.textContent = `${fmtZ(validDate(h))} (F${pad(h, 3)}) · red: temperature, green: dewpoint (°C) · left: pressure (mb) and height (thousand ft MSL)` +
    (isFinite(frz) ? ` · freezing level ${Math.round(frz / 100) * 100} ft MSL` : "");
}

document.querySelectorAll(".tabs button").forEach((b) => {
  b.onclick = () => {
    S.tab = b.dataset.tab;
    document.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("on", x === b));
    renderPanel();
  };
});
$("pclose").onclick = () => { S.point = null; S.pdata = null; $("panel").hidden = true; map.invalidateSize(); requestDraw(); saveHash(); };

// ---------------------------------------------------------------------------
// search: airports (lazy-loaded) or "lat, lon"
// ---------------------------------------------------------------------------
let airports = null, searchSel = 0, searchHits = [];
async function ensureAirports() {
  if (!airports) airports = fetchJSON("airports.json").catch(() => []);
  return airports;
}
function parseLatLon(q) {
  const m = q.match(/^\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*$/);
  return m ? [+m[1], +m[2]] : null;
}
async function doSearch() {
  const q = $("search").value.trim();
  const box = $("results");
  if (!q) { box.hidden = true; return; }
  const ll = parseLatLon(q);
  if (ll) { searchHits = [{ ll, label: `${ll[0]}, ${ll[1]}`, code: "↵" }]; }
  else {
    const list = await ensureAirports();
    const Q = q.toUpperCase(), ql = q.toLowerCase();
    const a = [], b = [], c = [];
    for (const r of list) {
      const codes = (r[0] + " " + r[5]).split(" ");
      if (codes.includes(Q)) a.push(r);
      else if (codes.some((x) => x.startsWith(Q))) b.push(r);
      else if (q.length >= 3 && r[1].toLowerCase().includes(ql)) c.push(r);
      if (a.length + b.length > 40) break;
    }
    searchHits = [...a, ...b, ...c].slice(0, 12).map((r) => ({ ll: [r[2], r[3]], label: r[1], code: r[0] }));
  }
  searchSel = 0;
  box.innerHTML = searchHits.map((h, i) => `<div data-i="${i}" class="${i ? "" : "sel"}"><b>${h.code}</b><span>${h.label}</span></div>`).join("") ||
    `<div><span>No match</span></div>`;
  box.hidden = false;
  box.querySelectorAll("div[data-i]").forEach((d) => { d.onmousedown = (e) => { e.preventDefault(); pickHit(+d.dataset.i); }; });
}
function pickHit(i) {
  const h = searchHits[i];
  if (!h) return;
  $("results").hidden = true;
  $("search").value = h.code !== "↵" ? h.code : h.label;
  $("search").blur();
  map.setView(h.ll, Math.max(map.getZoom(), 7));
  selectPoint(h.ll[0], h.ll[1], h.code !== "↵" ? `${h.code} · ${h.label}` : null);
}
$("search").addEventListener("focus", ensureAirports);
$("search").addEventListener("input", doSearch);
$("search").addEventListener("blur", () => setTimeout(() => { $("results").hidden = true; }, 150));
$("search").addEventListener("keydown", (e) => {
  const box = $("results");
  if (e.key === "Enter") { e.preventDefault(); pickHit(searchSel); }
  else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    searchSel = Math.max(0, Math.min(searchHits.length - 1, searchSel + (e.key === "ArrowDown" ? 1 : -1)));
    box.querySelectorAll("div[data-i]").forEach((d) => d.classList.toggle("sel", +d.dataset.i === searchSel));
  } else if (e.key === "Escape") { box.hidden = true; }
});
document.querySelectorAll("[data-region]").forEach((b) => { b.onclick = () => map.fitBounds(REGIONS[b.dataset.region]); });

// ---------------------------------------------------------------------------
// time controls
// ---------------------------------------------------------------------------
function step(d) {
  const n = availableHours().length;
  if (!n) return;
  S.hourIdx = (S.hourIdx + d + n) % n;
  render();
  saveHash();
}
$("prev").onclick = () => step(-1);
$("next").onclick = () => step(1);
$("hour").oninput = () => { S.hourIdx = +$("hour").value; render(); saveHash(); };
let playTimer = null;
function togglePlay() {
  S.playing = !S.playing;
  $("play").innerHTML = S.playing ? "&#10074;&#10074;" : "&#9654;";
  clearInterval(playTimer);
  if (S.playing) playTimer = setInterval(() => step(1), 900);
}
$("play").onclick = togglePlay;
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
  if (e.key === "ArrowLeft") step(-1);
  else if (e.key === "ArrowRight") step(1);
  else if (e.key === " ") { e.preventDefault(); togglePlay(); }
});
$("run").onchange = () => selectRun($("run").value, curHour() != null ? validDate(curHour()).getTime() : null);
$("product").onchange = () => { S.product = $("product").value; store.set("product", S.product); render(); saveHash(); };

// ---------------------------------------------------------------------------
// URL hash (bookmarkable state)
// ---------------------------------------------------------------------------
let hashTimer = null;
function saveHash() {
  clearTimeout(hashTimer);
  hashTimer = setTimeout(() => {
    if (!S.model) return;
    const c = map.getCenter();
    const parts = [`m=${S.model}`, `p=${S.product}`, `ll=${c.lat.toFixed(2)},${c.lng.toFixed(2)},${map.getZoom()}`];
    if (S.run && S.index && S.run !== pickDefaultRun(S.model)) parts.push(`r=${S.run}`);
    if (curHour() != null) parts.push(`h=${curHour()}`);
    if (S.point) parts.push(`pt=${S.point.lat.toFixed(3)},${S.point.lon.toFixed(3)}`);
    history.replaceState(null, "", "#" + parts.join("&"));
    store.set("view", { m: S.model, p: S.product, ll: [c.lat, c.lng, map.getZoom()] });
  }, 300);
}
function readHash() {
  const o = {};
  for (const kv of location.hash.slice(1).split("&")) { const [k, v] = kv.split("="); if (k) o[k] = decodeURIComponent(v || ""); }
  return o;
}

// ---------------------------------------------------------------------------
// boot
// ---------------------------------------------------------------------------
async function boot() {
  const hs = readHash(), saved = store.get("view", {});
  const ll = (hs.ll || "").split(",").map(Number);
  if (ll.length === 3 && ll.every(isFinite)) map.setView([ll[0], ll[1]], ll[2]);
  else if (saved.ll) map.setView([saved.ll[0], saved.ll[1]], saved.ll[2]);
  else map.fitBounds(REGIONS.conus);
  S.product = hs.p || saved.p || store.get("product", "sfc_t");
  try {
    S.index = await fetchJSON(`${DATA}/index.json`);
  } catch (e) {
    showBanner("No forecast data published yet. The first update runs automatically; check back in a few minutes.");
    return;
  }
  buildModelButtons();
  let m = hs.m || saved.m || "rrfs";
  if (!S.index.models[m] || !S.index.models[m].runs.length) m = S.index.order.find((x) => S.index.models[x].runs.length);
  if (!m) { showBanner("No model runs are available yet."); return; }
  await selectModel(m, hs.r, false);
  if (hs.h != null) {
    const i = availableHours().indexOf(+hs.h);
    if (i >= 0) { S.hourIdx = i; await render(); }
  } else {
    // start at the forecast hour closest to now
    let best = 0, bd = Infinity;
    availableHours().forEach((h, i) => { const d = Math.abs(validDate(h).getTime() - Date.now()); if (d < bd) { bd = d; best = i; } });
    S.hourIdx = best; await render();
  }
  if (hs.pt) { const [a, b] = hs.pt.split(",").map(Number); if (isFinite(a) && isFinite(b)) selectPoint(a, b, null); }
  // refresh run list periodically so a long-open page picks up new runs
  setInterval(async () => {
    try {
      const idx = await fetchJSON(`${DATA}/index.json`);
      if (idx.generated !== S.index.generated) {
        S.index = idx;
        const cur = S.run, runs = idx.models[S.model].runs;
        buildModelButtons();
        for (const b of $("models").children) b.classList.toggle("on", b.dataset.model === S.model);
        metaCache.delete(`${S.model}/${cur}`);
        if (runs.some((r) => r.run === cur)) {
          const sel = $("run"); sel.innerHTML = "";
          for (const r of runs) { const o = document.createElement("option"); o.value = r.run; o.textContent = fmtRun(r.run, r.complete, r.available.length, r.nhours); sel.appendChild(o); }
          await selectRun(cur, curHour() != null ? validDate(curHour()).getTime() : null);
        } else await selectModel(S.model);
        updateStatus();
      }
    } catch (e) { /* offline; try again later */ }
  }, 5 * 60e3);
}
boot();
