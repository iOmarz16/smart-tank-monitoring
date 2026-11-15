"""M021 dashboard: view and log only (no control authority).

    dashboard\\.venv\\Scripts\\python dashboard\\app.py                 # real rig (ESP32 AP)
    dashboard\\.venv\\Scripts\\python dashboard\\app.py --rig ws://127.0.0.1:8765/   # fake_rig

- connects to the rig WebSocket, estimates the clock offset from ping-pong round trips
  (minimum RTT), computes per-sample latency (test I1), deduplicates by seq and writes one
  CSV per session in dashboard/logs/
- serves the local UI on http://127.0.0.1:<port>/ (all assets from dashboard/static, no CDN)
  and pushes samples and stats to the browser over a local WebSocket
- the only messages ever sent to the rig are pings
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import mimetypes
import statistics
import sys
import time
import webbrowser
from collections import deque
from datetime import datetime
from http import HTTPStatus
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Response

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
LOGS = HERE / "logs"

SAMPLE_FIELDS = [
    "seq", "t_esp_ms", "P_raw", "P_f", "valid", "u_open", "integ", "valve_duty", "pump_on",
    "warning", "t_to_limit", "state", "trip", "trip_reason", "loop_period_us", "replayed",
    "slope", "v_gpio_mv", "sensor_fault", "zero_offset", "zero_forced", "trip_decision_us",
    "trip_output_us", "t3_us", "t3_source", "led_on", "rb_valve_off", "rb_pump_off", "reset_reason",
    "trip_latched", "auto_clears",
    # A34 team leak model: LOG AND DISPLAY ONLY (P_comp_assumed is an assumption, not a measurement)
    "P_corr", "P_comp_assumed", "leak_rate",
]
CSV_FIELDS = SAMPLE_FIELDS + ["t_arrival_pc_ms", "t_arrival_wall", "clock_offset_ms", "latency_ms"]

HISTORY_S = 300            # chart window sent to new browser tabs (5 min)
PING_INITIAL = 100         # round trips at connect (Table I, I1)
PING_REFRESH = 20          # round trips per refresh
PING_REFRESH_S = 30.0
PING_TIMEOUT_S = 0.5


_T0_NS = time.perf_counter_ns()


def pc_ms() -> float:
    """Monotonic PC clock in ms since dashboard start (immune to wall-clock jumps).
    Kept small so t_pc survives the round trip through the ESP32 JSON parser exactly."""
    return (time.perf_counter_ns() - _T0_NS) / 1e6


def percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return float("nan")
    k = (len(sorted_vals) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


class Session:
    def __init__(self, rig_url: str) -> None:
        self.rig_url = rig_url
        LOGS.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.csv_path = LOGS / f"session_{stamp}.csv"
        self._csv_file = self.csv_path.open("w", newline="", encoding="utf-8")
        self._csv = csv.DictWriter(self._csv_file, fieldnames=CSV_FIELDS, extrasaction="ignore")
        self._csv.writeheader()
        self._last_flush = 0.0

        self.seen: set[int] = set()
        self.history: deque[dict] = deque(maxlen=HISTORY_S * 10 + 200)
        self.latencies: list[float] = []   # live samples only
        self.over_1s = 0
        self.duplicates = 0
        self.replayed_count = 0

        self.offset_ms: float | None = None
        self.offset_rtt_ms: float | None = None
        self.offset_updated_pc: float | None = None

        self.connected = False
        self.last_arrival_pc: float | None = None
        self.last_sample: dict | None = None
        self.newest_seq = -1

        self.browsers: set[ServerConnection] = set()
        self._pending: dict[float, asyncio.Future] = {}

    # ------------------------------------------------------------------ rig side
    async def rig_loop(self) -> None:
        while True:
            try:
                async with connect(self.rig_url, open_timeout=3, ping_interval=2,
                                   ping_timeout=2, max_queue=4096) as ws:
                    self.connected = True
                    print(f"[dashboard] connected to {self.rig_url}", flush=True)
                    await self.broadcast_status()
                    reader = asyncio.create_task(self.read_rig(ws))
                    pinger = asyncio.create_task(self.ping_loop(ws))
                    done, pending = await asyncio.wait({reader, pinger}, return_when=asyncio.FIRST_COMPLETED)
                    for t in pending:
                        t.cancel()
                    for t in done:
                        if t.exception() and not isinstance(t.exception(), ConnectionClosed):
                            print(f"[dashboard] link error: {t.exception()!r}", flush=True)
                    print("[dashboard] rig link lost; reconnecting", flush=True)
            except (OSError, ConnectionClosed, TimeoutError) as e:
                if self.connected:
                    print(f"[dashboard] rig link lost: {e!r}", flush=True)
            except Exception as e:  # handshake rejected (e.g. 503) and similar
                if self.connected:
                    print(f"[dashboard] rig link lost: {e!r}", flush=True)
            if self.connected:
                self.connected = False
                await self.broadcast_status()
            for fut in self._pending.values():
                fut.cancel()
            self._pending.clear()
            await asyncio.sleep(0.5)

    async def read_rig(self, ws) -> None:
        async for raw in ws:
            t_arr = pc_ms()
            try:
                m = json.loads(raw)
            except ValueError:
                continue
            if m.get("type") == "pong":
                fut = self._take_pending(m.get("t_pc"))
                if fut and not fut.done():
                    fut.set_result((float(m["t_pc"]), t_arr, float(m["t_esp"])))
            elif m.get("type") == "sample":
                await self.on_sample(m, t_arr)

    def _take_pending(self, t_pc) -> asyncio.Future | None:
        if not isinstance(t_pc, (int, float)):
            return None
        fut = self._pending.pop(t_pc, None)
        if fut is None:  # tolerate float formatting differences on the ESP32 side
            for k in list(self._pending):
                if abs(k - t_pc) < 0.0005:
                    return self._pending.pop(k)
        return fut

    async def ping_batch(self, ws, n: int) -> None:
        best: tuple[float, float] | None = None  # (rtt, offset)
        for _ in range(n):
            t_pc = round(pc_ms(), 3)
            fut = asyncio.get_running_loop().create_future()
            self._pending[t_pc] = fut
            await ws.send(json.dumps({"type": "ping", "t_pc": t_pc}))
            try:
                t_send, t_recv, t_esp = await asyncio.wait_for(fut, PING_TIMEOUT_S)
            except (TimeoutError, asyncio.CancelledError):
                self._pending.pop(t_pc, None)
                continue
            rtt = t_recv - t_send
            off = (t_send + t_recv) / 2.0 - t_esp
            if best is None or rtt < best[0]:
                best = (rtt, off)
            await asyncio.sleep(0.01)
        if best:
            self.offset_rtt_ms, self.offset_ms = best
            self.offset_updated_pc = pc_ms()
            print(f"[dashboard] clock offset {self.offset_ms:.3f} ms (min RTT {self.offset_rtt_ms:.2f} ms, "
                  f"{n} pings)", flush=True)

    async def ping_loop(self, ws) -> None:
        await self.ping_batch(ws, PING_INITIAL)
        while True:
            await asyncio.sleep(PING_REFRESH_S)
            await self.ping_batch(ws, PING_REFRESH)

    async def on_sample(self, m: dict, t_arr: float) -> None:
        seq = m.get("seq")
        if not isinstance(seq, int):
            return
        if seq in self.seen:
            self.duplicates += 1
            return
        self.seen.add(seq)
        replayed = bool(m.get("replayed"))
        latency = None
        if not replayed and self.offset_ms is not None:
            latency = t_arr - (m["t_esp_ms"] + self.offset_ms)
            self.latencies.append(latency)
            if latency > 1000.0:
                self.over_1s += 1
        if replayed:
            self.replayed_count += 1
        row = dict(m)
        row.update(t_arrival_pc_ms=round(t_arr, 3),
                   t_arrival_wall=datetime.now().isoformat(timespec="milliseconds"),
                   clock_offset_ms=None if self.offset_ms is None else round(self.offset_ms, 3),
                   latency_ms=None if latency is None else round(latency, 3))
        self._csv.writerow(row)
        if t_arr - self._last_flush > 1000:
            self._csv_file.flush()
            self._last_flush = t_arr
        self.last_arrival_pc = t_arr
        out = {k: m.get(k) for k in SAMPLE_FIELDS}
        out["latency_ms"] = None if latency is None else round(latency, 2)
        if seq > self.newest_seq:
            self.newest_seq = seq
            self.last_sample = out
        self.history.append(out)
        await self.broadcast({"type": "sample", "s": out})

    # ------------------------------------------------------------- statistics
    def stats(self) -> dict:
        lat = sorted(self.latencies)
        age = None if self.last_arrival_pc is None else pc_ms() - self.last_arrival_pc
        return {
            "type": "status",
            "connected": self.connected,
            "rig_url": self.rig_url,
            "age_ms": age,
            "offset_ms": self.offset_ms,
            "offset_rtt_ms": self.offset_rtt_ms,
            "offset_age_s": None if self.offset_updated_pc is None else (pc_ms() - self.offset_updated_pc) / 1000,
            "lat_n": len(lat),
            "lat_median": statistics.median(lat) if lat else None,
            "lat_p95": percentile(lat, 0.95) if lat else None,
            "lat_max": lat[-1] if lat else None,
            "lat_over_1s": self.over_1s,
            "samples": len(self.seen),
            "replayed": self.replayed_count,
            "duplicates": self.duplicates,
            "missing": (self.newest_seq + 1 - len(self.seen)) if self.newest_seq >= 0 else 0,
            "csv": str(self.csv_path.relative_to(HERE.parent)),
        }

    async def stats_loop(self) -> None:
        while True:
            await asyncio.sleep(0.5)
            await self.broadcast(self.stats())

    async def broadcast_status(self) -> None:
        await self.broadcast(self.stats())

    async def broadcast(self, msg: dict) -> None:
        if not self.browsers:
            return
        data = json.dumps(msg, separators=(",", ":"), allow_nan=False, default=str)
        for b in list(self.browsers):
            try:
                await b.send(data)
            except ConnectionClosed:
                self.browsers.discard(b)

    # ------------------------------------------------------------ browser side
    async def browser_handler(self, ws: ServerConnection) -> None:
        self.browsers.add(ws)
        try:
            hist = sorted(self.history, key=lambda s: s["seq"])
            await ws.send(json.dumps({"type": "history", "samples": hist}, separators=(",", ":")))
            await ws.send(json.dumps(self.stats(), default=str))
            async for _ in ws:
                pass  # the browser has nothing to say: view only
        except ConnectionClosed:
            pass
        finally:
            self.browsers.discard(ws)

    def http(self, connection, request):
        path = request.path.split("?", 1)[0]
        if path == "/ws":
            return None  # continue the WebSocket handshake
        if path == "/":
            path = "/index.html"
        target = (STATIC / path.lstrip("/")).resolve()
        if STATIC not in target.parents or not target.is_file():
            return connection.respond(HTTPStatus.NOT_FOUND, "not found\n")
        body = target.read_bytes()
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        headers = Headers([("Content-Type", ctype), ("Content-Length", str(len(body))),
                           ("Cache-Control", "no-store")])
        return Response(200, "OK", headers, body)

    def close(self) -> None:
        self._csv_file.flush()
        self._csv_file.close()


async def main_async(args: argparse.Namespace) -> None:
    mimetypes.add_type("application/javascript", ".js")
    mimetypes.add_type("text/css", ".css")
    s = Session(args.rig)
    print(f"[dashboard] logging to {s.csv_path}", flush=True)
    try:
        async with serve(s.browser_handler, "127.0.0.1", args.port, process_request=s.http,
                         ping_interval=None):
            url = f"http://127.0.0.1:{args.port}/"
            print(f"[dashboard] UI at {url}", flush=True)
            if not args.no_browser:
                webbrowser.open(url)
            await asyncio.gather(s.rig_loop(), s.stats_loop())
    finally:
        s.close()
        st = s.stats()
        print(f"[dashboard] session closed: {st['samples']} samples, {st['missing']} missing, "
              f"latency n={st['lat_n']} median={st['lat_median']} p95={st['lat_p95']} max={st['lat_max']}",
              flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="M021 view-and-log dashboard")
    ap.add_argument("--rig", default="ws://192.168.4.1:81/", help="rig WebSocket URL")
    ap.add_argument("--port", type=int, default=8000, help="local UI port")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--duration", type=float, default=0.0, help="exit after N s (testing)")
    args = ap.parse_args()
    if args.duration > 0:
        async def bounded():
            try:
                await asyncio.wait_for(main_async(args), args.duration)
            except TimeoutError:
                pass
        coro = bounded()
    else:
        coro = main_async(args)
    try:
        asyncio.run(coro)
    except KeyboardInterrupt:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
