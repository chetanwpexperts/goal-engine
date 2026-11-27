"""A minimal server may have no time zone files (no /usr/share/zoneinfo): DBaba must still start.

Found by the clean-machine install matrix (deploy/onprem/tests): on a bare Ubuntu 22.04 image app/agents/spec.py's
ZoneInfo("Asia/Kolkata") raised ZoneInfoNotFoundError and the service never started. The tzdata package in
requirements.txt carries the zone data inside Python, so the OS doesn't have to."""

import subprocess
import sys


def test_zones_load_without_the_os_zone_files(tmp_path):
    code = ("import zoneinfo; assert zoneinfo.TZPATH == (), zoneinfo.TZPATH; "
            "zoneinfo.ZoneInfo('Asia/Kolkata'); zoneinfo.ZoneInfo('UTC'); "
            "import app.agents.spec")
    env = {"PYTHONTZPATH": "", "PATH": "/usr/bin:/bin"}
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, cwd=".")
    assert r.returncode == 0, r.stderr[-800:]


def test_tzdata_is_a_dependency():
    assert any(line.split("==")[0].strip().lower() == "tzdata" for line in open("requirements.txt"))
