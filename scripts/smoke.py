"""End-to-end smoke test against a running stack (``make up``), as a client would use it.

    uv run python scripts/smoke.py --url http://localhost:8000

Creates an API key inside the running api container unless --key is given.
Exit code 0 when the whole flow works.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import uuid

import httpx

from pcb_inspection import synthetic as sy
from pcb_inspection.engine.imaging import encode_jpeg

WIDTH = 1600  # above the default gate_min_width (1500)


def _key_from_container() -> str:
    out = subprocess.run(
        ["docker", "compose", "exec", "-T", "api", "pcbis", "apikey", "create", "--station", "smoke"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return out.split("key:")[1].strip()


def _wait_ready(client: httpx.Client, timeout_s: float = 90) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if client.get("/health/ready").status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise SystemExit("service did not become ready")


def check(cond: bool, message: str) -> None:
    print(("ok   " if cond else "FAIL ") + message)
    if not cond:
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--key", default=None)
    args = parser.parse_args()

    spec = sy.random_board(11, width=WIDTH, height=WIDTH * 2 // 3)
    ref = encode_jpeg(sy.photograph(sy.render(spec), sy.identity_camera(spec)), 95)
    defects = sy.Defects(removed=frozenset({4}))
    test = encode_jpeg(sy.photograph(sy.render(spec, defects), sy.camera(spec, 3)), 95)

    with httpx.Client(base_url=args.url, timeout=60) as client:
        _wait_ready(client)
        key = args.key or _key_from_container()
        client.headers["Authorization"] = f"Bearer {key}"

        s = client.post("/api/v1/sessions", json={"product_code": "smoke"}).json()
        check("id" in s, f"session created {s.get('id')}")
        r = client.post(
            f"/api/v1/sessions/{s['id']}/references",
            data={"side": "1"},
            files={"image": ("ref.jpg", ref, "image/jpeg")},
        )
        check(r.status_code == 201, f"reference uploaded ({r.status_code})")
        r = client.post(
            f"/api/v1/sessions/{s['id']}/inspections",
            data={"side": "1", "board": json.dumps({"board_key": "smoke-1"})},
            files={"image": ("test.jpg", test, "image/jpeg")},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        check(r.status_code == 202, f"inspection queued ({r.status_code})")
        t0 = time.monotonic()
        insp = client.get(r.json()["poll_url"], params={"wait": 30}).json()
        while insp["status"] in ("queued", "processing") and time.monotonic() - t0 < 120:
            insp = client.get(r.json()["poll_url"], params={"wait": 30}).json()
        check(insp["status"] == "completed", f"inspection {insp['status']} in {time.monotonic() - t0:.1f} s")
        check(len(insp["defects"]) >= 1, f"defects found: {len(insp['defects'])}")
        d = insp["defects"][0]
        crop = client.get(d["crop_url"])
        check(crop.status_code == 200 and crop.headers["content-type"] == "image/jpeg", "crop served")
        v = client.put(f"/api/v1/defects/{d['id']}/verdict", json={"verdict": "accepted"})
        check(v.json().get("verdict") == "accepted", "verdict stored")
        b = client.put(f"/api/v1/sessions/{s['id']}/boards/smoke-1/verdict", json={"verdict": "fail"})
        check(b.json().get("verdict") == "fail", "board verdict stored")
    print("smoke test passed")


if __name__ == "__main__":
    main()
