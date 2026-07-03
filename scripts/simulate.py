#!/usr/bin/env python3
"""
simulate.py — Continuously POST a realistic, in-range Ecowitt snapshot to the
local ingest endpoint so the dashboard has live data to render.

Usage:
  uv run python scripts/simulate.py [--host URL] [--interval SECONDS] [--count N]

Values drift via a small bounded random walk and stay within healthy ranges,
so the deterministic alert rules won't fire during normal local testing.
Run the server with GARDEN_DRY_RUN=1 (see scripts/dev.sh) as a backstop in
case a drift pushes a value past a threshold anyway.
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx

from garden.config import cfg

# (field, low, high, start) — random walk stays clamped to [low, high]
_CHANNELS: dict[str, tuple[float, float, float]] = {
    "soilmoisture1": (45, 65, 55),
    "soilmoisture2": (45, 65, 50),
    "soilmoisture3": (45, 65, 58),
    "soilmoisture4": (45, 65, 52),
    "soilbatt1": (1.4, 1.5, 1.45),
    "soilbatt2": (1.4, 1.5, 1.44),
    "soilbatt3": (1.4, 1.5, 1.46),
    "soilbatt4": (1.4, 1.5, 1.43),
    "tempf": (65, 85, 75),
    "humidity": (40, 70, 55),
    "tempinf": (68, 78, 73),
    "humidityin": (40, 60, 50),
    "baromrelin": (29.8, 30.0, 29.9),
    "baromabsin": (29.8, 30.0, 29.85),
    "temp1f": (65, 85, 74),
    "humidity1": (40, 70, 58),
}

_STEP = 0.03  # fraction of range moved per tick, at most


def _walk(state: dict[str, float]) -> dict[str, float]:
    for key, (low, high, _start) in _CHANNELS.items():
        span = high - low
        delta = random.uniform(-_STEP, _STEP) * span
        state[key] = min(high, max(low, state[key] + delta))
    return state


def _build_payload(state: dict[str, float]) -> dict[str, str]:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    payload = {
        "PASSKEY": cfg.ingest_passkey,
        "stationtype": "GW1200A_V2.1.4-SIM",
        "dateutc": now,
    }
    for key, value in state.items():
        payload[key] = f"{value:.2f}"
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Stream simulated Ecowitt sensor data")
    parser.add_argument("--host", default="http://127.0.0.1:8000")
    parser.add_argument("--interval", type=float, default=10.0, help="seconds between POSTs")
    parser.add_argument("--count", type=int, default=None, help="number of POSTs (default: infinite)")
    args = parser.parse_args()

    state = {key: start for key, (_low, _high, start) in _CHANNELS.items()}
    url = f"{args.host}/api/ecowitt"

    print(f"→ Streaming simulated sensor data to {url} every {args.interval}s (Ctrl-C to stop)")

    sent = 0
    with httpx.Client(timeout=10) as client:
        while args.count is None or sent < args.count:
            state = _walk(state)
            payload = _build_payload(state)
            try:
                resp = client.post(url, data=payload)
                resp.raise_for_status()
                body = resp.json()
                print(
                    f"[{payload['dateutc']}] soil1={state['soilmoisture1']:.1f}% "
                    f"soil2={state['soilmoisture2']:.1f}% temp={state['tempf']:.1f}F "
                    f"gazebo_temp={state['temp1f']:.1f}F gazebo_hum={state['humidity1']:.1f}% "
                    f"-> {body}"
                )
            except httpx.HTTPError as exc:
                print(f"! POST failed: {exc}", file=sys.stderr)

            sent += 1
            if args.count is None or sent < args.count:
                time.sleep(args.interval)

    print(f"✓ Done ({sent} snapshots sent).")


if __name__ == "__main__":
    main()
