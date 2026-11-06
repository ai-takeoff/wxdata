#!/usr/bin/env bash
# Offline end-to-end test: synthetic NOAA-format files -> pipeline -> value checks.
# Usage: tools/selftest.sh   (needs: pip install -r requirements.txt)
set -euo pipefail
cd "$(dirname "$0")/.."
T=$(mktemp -d)
python tools/make_test_data.py "$T/srv" 2026110612 0,3,6,9 >/dev/null
python tools/range_server.py "$T/srv" 8765 & SRV=$!
trap 'kill $SRV' EXIT
sleep 1
B=http://127.0.0.1:8765
export NO_PROXY=127.0.0.1
export WX_SOURCES_RRFS="$B/rrfs/rrfs.{ymd}/{cc}/rrfs.t{cc}z.prslev.13km.f{fff}.na.grib2"
export WX_SOURCES_GFS="$B/gfs/gfs.{ymd}/{cc}/atmos/gfs.t{cc}z.pgrb2.0p25.f{fff}"
export WX_SOURCES_RAP="$B/rap/rap.{ymd}/rap.t{cc}z.awp130pgrbf{ff}.grib2"
export WX_HOURS_RRFS=0,3,6,9 WX_HOURS_GFS=0,3,6,9 WX_HOURS_RAP=0,3,6,9
python -m pipeline.run --data "$T/out/data" --work "$T/out/work" --now 2026-11-06T14:20 | tail -1
python tools/verify_test.py "$T/out/data" 2026110612
