"""Headless browser check of the viewer (used during development).

Usage: python tools/browser_test.py SITE_DIR LEAFLET_DIST_DIR OUT_DIR
Serves SITE_DIR, routes the Leaflet CDN to a local copy, exercises the UI and
saves screenshots. Reports JS errors.
"""
import os
import subprocess
import sys
import time

from playwright.sync_api import sync_playwright

site, leaflet, out = sys.argv[1], sys.argv[2], sys.argv[3]
os.makedirs(out, exist_ok=True)
srv = subprocess.Popen([sys.executable, "-m", "http.server", "8770", "--bind", "127.0.0.1", "-d", site],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1)
errors = []
try:
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-proxy-server"])
        pg = b.new_page(viewport={"width": 1400, "height": 860})
        pg.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type in ("error", "warning") else None)
        pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

        def route(r):
            url = r.request.url
            if "cdnjs.cloudflare.com/ajax/libs/leaflet" in url:
                fn = url.split("/")[-1].replace(".min", "")
                r.fulfill(path=os.path.join(leaflet, fn))
            elif "basemaps.cartocdn.com" in url:
                r.fulfill(status=204, body="")
            else:
                r.continue_()
        pg.route("**/*", route)

        pg.goto("http://127.0.0.1:8770/#m=rrfs&p=sfc_t&ll=40.5,-80,5")
        pg.wait_for_timeout(2500)
        pg.screenshot(path=f"{out}/1_sfc_temp.png")
        print("models:", pg.eval_on_selector_all("#models button", "els => els.map(e => e.textContent + (e.classList.contains('on')?'*':''))"))
        print("valid:", pg.inner_text("#valid"))
        print("status:", pg.inner_text("#status"))
        print("products:", pg.eval_on_selector("#product", "s => s.options.length"))

        pg.select_option("#product", "wind_500")
        pg.wait_for_timeout(1500)
        pg.screenshot(path=f"{out}/2_wind500.png")

        pg.select_option("#product", "ceil")
        pg.wait_for_timeout(1200)
        pg.screenshot(path=f"{out}/3_ceil.png")

        # airport search -> point panel
        pg.fill("#search", "KMMU")
        pg.wait_for_timeout(1500)
        print("search results:", pg.inner_text("#results")[:120].replace("\n", " | "))
        pg.press("#search", "Enter")
        pg.wait_for_timeout(1500)
        print("panel title:", pg.inner_text("#ptitle"), "|", pg.inner_text("#psub"))
        print("table first rows:", pg.inner_text("#pbody")[:400].replace("\n", " | ").replace("\t", " "))
        pg.screenshot(path=f"{out}/4_point_table.png")
        pg.click(".tabs button[data-tab=aloft]")
        pg.wait_for_timeout(500)
        print("aloft:", pg.inner_text("#pbody")[:300].replace("\n", " | ").replace("\t", " "))
        pg.click(".tabs button[data-tab=sounding]")
        pg.wait_for_timeout(800)
        pg.screenshot(path=f"{out}/5_sounding.png")

        # model switch + hour stepping
        pg.click("#models button[data-model=gfs]")
        pg.wait_for_timeout(1500)
        pg.keyboard.press("ArrowRight")
        pg.wait_for_timeout(1200)
        print("gfs valid:", pg.inner_text("#valid"), "| hash:", pg.evaluate("location.hash"))
        pg.click("#models button[data-model=rap]")
        pg.wait_for_timeout(1500)
        pg.screenshot(path=f"{out}/6_rap.png")

        # mobile layout
        m = b.new_page(viewport={"width": 390, "height": 800}, device_scale_factor=2)
        m.on("pageerror", lambda e: errors.append(f"mobile pageerror: {e}"))
        m.route("**/*", route)
        m.goto("http://127.0.0.1:8770/#m=rrfs&p=sfc_t&ll=41,-75,6&pt=40.8,-74.4")
        m.wait_for_timeout(2500)
        m.screenshot(path=f"{out}/7_mobile.png")
        b.close()
finally:
    srv.terminate()
print("JS errors:" if errors else "No JS errors")
for e in errors:
    print("  ", e)
