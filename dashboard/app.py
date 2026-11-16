"""M021 dashboard: view and log only (no control authority).

    dashboard\\.venv\\Scripts\\python dashboard\\app.py                 # real rig (ESP32 AP)
    dashboard\\.venv\\Scripts\\python dashboard\\app.py --rig ws://127.0.0.1:8765/   # fake_rig

- connects to the rig WebSocket, estimates the clock offset from ping-pong round trips
  (minimum RTT), computes per-sample latency (test I1), deduplicates by seq and writes one
  CSV per session in dashboard/logs/
- serves the local UI on http://127.0.0.1:<port>/ (all assets from dashboard/static, no CDN)
  and pushes samples and stats to the browser over a local WebSocket
- the only messages ever sent to the rig are pings

DEMO mode (--demo, supervisor meeting, no ESP32): the dashboard starts tools/fake_rig.py on
127.0.0.1 and shows a Specifications panel (dashboard/demo_specs.py). The Scenario dropdown,
the demo speed and "Reset demo" go to the SIMULATOR's own control channel on 127.0.0.1:8766,
a port the firmware does not have. Nothing is ever sent to an ESP32: --demo refuses any rig
URL other than the local simulator, and without --demo every browser message is ignored.
Demo CSV logs go to dashboard/logs/demo/, never next to rig logs.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import mimetypes
import statistics
import subprocess
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
DEMO_LOGS = LOGS / "demo"        # DEMO runs only, never mixed with rig logs
FAKE_RIG = HERE.parent / "tools" / "fake_rig.py"
DEMO_RIG_URL = "ws://127.0.0.1:8765/"
DEMO_CONTROL_PORT = 8766
DEMO_CONTROL_URL = f"ws://127.0.0.1:{DEMO_CONTROL_PORT}/"
DEMO_FIELDS = ["demo_scenario", "demo_phase", "demo_t_s", "demo_speed", "demo_mark"]

SAMPLE_FIELDS = [
    # A35: in SIM, P_raw / P_f are the simulated pressure; P_real is always the measured one.
    "seq", "t_esp_ms", "P_raw", "P_f", "P_real", "simulated", "sim_rate", "valid", "u_open", "integ", "valve_duty", "pump_on",
    "warning", "t_to_limit", "state", "trip", "trip_reason", "loop_period_us", "replayed",
    "slope", "v_gpio_mv", "sensor_fault", "zero_offset", "zero_forced", "trip_decision_us",
    "trip_output_us", "t3_us", "t3_source", "led_on", "rb_valve_off", "rb_pump_off", "reset_reason",
    "trip_latched", "auto_clears",
    # A34 team leak model: LOG ONLY, not shown on the dashboard (P_comp_assumed is an assumption)
    "P_corr", "P_comp_assumed", "leak_rate",
]
CSV_FIELDS = SAMPLE_FIELDS + ["t_arrival_pc_ms", "t_arrival_wall", "clock_offset_ms", "latency_ms"]

BROWSER_QUEUE_MAX = 600    # messages (~30 s of samples) before a stalled browser tab is dropped
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
    def __init__(self, rig_url: str, demo: bool = False) -> None:
        self.rig_url = rig_url
        self.demo = demo
        self.sample_fields = SAMPLE_FIELDS + (DEMO_FIELDS if demo else [])
        self.csv_fields = self.sample_fields + CSV_FIELDS[len(SAMPLE_FIELDS):]
        self.log_dir = DEMO_LOGS if demo else LOGS
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._csv_file = None
        self.open_csv("idle")
        # One send queue per browser tab: a tab that stops reading must never stall the rig
        # reader (and with it the CSV log). A tab that falls BROWSER_QUEUE_MAX behind is dropped.
        self.browsers: dict[ServerConnection, asyncio.Queue] = {}
        self._pending: dict[float, asyncio.Future] = {}
        self.reset_counters()
        # DEMO only
        self.board = None
        self.ctl = None                   # control channel to the SIMULATOR (never the ESP32)
        self.sim_proc: subprocess.Popen | None = None
        self.sim_log = None
        if demo:
            from demo_specs import SpecBoard
            self.board = SpecBoard()

    def open_csv(self, label: str) -> None:
        if self._csv_file is not None:
            self._csv_file.flush()
            self._csv_file.close()
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        name = f"demo_{stamp}_{label}.csv" if self.demo else f"session_{stamp}.csv"
        self.csv_path = self.log_dir / name
        self._csv_file = self.csv_path.open("w", newline="", encoding="utf-8")
        self._csv = csv.DictWriter(self._csv_file, fieldnames=self.csv_fields, extrasaction="ignore")
        self._csv.writeheader()
        self._last_flush = 0.0

    def reset_counters(self) -> None:
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
        if self.demo:
            # One CSV per demo scenario run (in dashboard/logs/demo/).
            sid = m.get("demo_scenario") or ""
            if sid and m.get("demo_mark") == f"{sid} start":
                self.open_csv(sid)
                print(f"[dashboard] DEMO {sid}: logging to {self.csv_path}", flush=True)
        row = dict(m)
        row["simulated"] = 1 if m.get("simulated") else 0  # A35: 0/1 in the CSV
        row.update(t_arrival_pc_ms=round(t_arr, 3),
                   t_arrival_wall=datetime.now().isoformat(timespec="milliseconds"),
                   clock_offset_ms=None if self.offset_ms is None else round(self.offset_ms, 3),
                   latency_ms=None if latency is None else round(latency, 3))
        self._csv.writerow(row)
        if t_arr - self._last_flush > 1000:
            self._csv_file.flush()
            self._last_flush = t_arr
        self.last_arrival_pc = t_arr
        out = {k: m.get(k) for k in self.sample_fields}
        out["latency_ms"] = None if latency is None else round(latency, 2)
        if self.board is not None:
            self.board.on_sample(out)
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
            "demo": self.demo,
        }

    async def stats_loop(self) -> None:
        while True:
            await asyncio.sleep(0.5)
            await self.broadcast(self.stats())
            if self.board is not None:
                await self.broadcast(self.board.view())

    async def broadcast_status(self) -> None:
        await self.broadcast(self.stats())

    async def broadcast(self, msg: dict) -> None:
        if not self.browsers:
            return
        data = json.dumps(msg, separators=(",", ":"), allow_nan=False, default=str)
        for b, q in list(self.browsers.items()):
            try:
                q.put_nowait(data)  # never waits for a browser
            except asyncio.QueueFull:
                print("[dashboard] a browser tab stopped reading; dropping it (it reconnects)", flush=True)
                self.browsers.pop(b, None)
                b.transport.abort()

    async def browser_sender(self, ws: ServerConnection, q: asyncio.Queue) -> None:
        try:
            while True:
                await ws.send(await q.get())
        except ConnectionClosed:
            pass

    # ------------------------------------------------------------ browser side
    async def browser_handler(self, ws: ServerConnection) -> None:
        q: asyncio.Queue = asyncio.Queue(BROWSER_QUEUE_MAX)
        sender = None
        self.browsers[ws] = q  # queued from now on, sent after the history
        try:
            hist = sorted(self.history, key=lambda s: s["seq"])
            await ws.send(json.dumps({"type": "history", "samples": hist}, separators=(",", ":")))
            await ws.send(json.dumps(self.stats(), default=str))
            if self.demo:
                await ws.send(json.dumps(self.demo_mode_msg()))
                await ws.send(json.dumps(self.board.view(), default=str))
            sender = asyncio.create_task(self.browser_sender(ws, q))
            async for raw in ws:
                if not self.demo:
                    continue  # the browser has nothing to say: view only
                await self.on_demo_request(raw)
        except ConnectionClosed:
            pass
        finally:
            self.browsers.pop(ws, None)
            if sender is not None:
                sender.cancel()

    # ------------------------------------------------------------ DEMO only
    def demo_mode_msg(self) -> dict:
        from demo_specs import CARDS
        return {"type": "mode", "demo": True, "speeds": [1, 2, 4],
                "scenarios": [{"id": c["id"], "title": c["title"]} for c in CARDS]}

    async def on_demo_request(self, raw) -> None:
        """Browser -> SIMULATOR requests (scenario, speed, reset). DEMO mode only."""
        from demo_specs import CARD_IDS
        try:
            m = json.loads(raw)
        except (ValueError, TypeError):
            return
        if not isinstance(m, dict) or m.get("type") != "demo":
            return
        act = m.get("action")
        if act == "reset":
            await self.reset_demo()
            return
        if act == "scenario" and m.get("id") in CARD_IDS:
            req = {"type": "scenario", "id": m["id"]}
        elif act == "speed" and m.get("x") in (1, 2, 4) and not isinstance(m.get("x"), bool):
            req = {"type": "speed", "x": m["x"]}
        else:
            return
        ctl = self.ctl
        if ctl is not None:
            try:
                await ctl.send(json.dumps(req))
            except ConnectionClosed:
                pass

    async def demo_control_loop(self) -> None:
        """Control channel to tools/fake_rig.py on 127.0.0.1 (the firmware has no such port)."""
        while True:
            try:
                async with connect(DEMO_CONTROL_URL, open_timeout=3, ping_interval=None) as ctl:
                    self.ctl = ctl
                    async for raw in ctl:
                        try:
                            m = json.loads(raw)
                        except ValueError:
                            continue
                        if m.get("type") == "demo_state":
                            self.board.on_demo_state(m)
                        elif m.get("type") == "demo_ack":
                            await self.broadcast(m)
            except Exception:  # simulator (re)starting
                pass
            self.ctl = None
            await asyncio.sleep(0.5)

    def start_sim(self) -> None:
        if self.sim_log is None:
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            self.sim_log = (DEMO_LOGS / f"fake_rig_{stamp}.log").open("a", encoding="utf-8")
        self.sim_started = time.monotonic()
        self.sim_proc = subprocess.Popen(
            [sys.executable, str(FAKE_RIG), "--demo", "--port", "8765",
             "--control-port", str(DEMO_CONTROL_PORT), "--zero-error", "0"],
            stdin=subprocess.DEVNULL, stdout=self.sim_log, stderr=subprocess.STDOUT)
        print(f"[dashboard] DEMO simulator started (pid {self.sim_proc.pid}); its output: "
              f"{Path(self.sim_log.name).relative_to(HERE.parent)}", flush=True)

    def stop_sim(self) -> None:
        if self.sim_proc is not None and self.sim_proc.poll() is None:
            self.sim_proc.terminate()
            try:
                self.sim_proc.wait(5)
            except subprocess.TimeoutExpired:
                self.sim_proc.kill()
        self.sim_proc = None

    async def sim_watchdog(self) -> None:
        quick = 0
        while True:
            await asyncio.sleep(1.0)
            p = self.sim_proc
            if p is None or p.poll() is None:
                continue
            # an exit within 10 s of the start counts as a failed start
            quick = quick + 1 if time.monotonic() - self.sim_started < 10.0 else 1
            if quick > 3:
                print("[dashboard] DEMO simulator keeps exiting; see its log in dashboard/logs/demo/. "
                      "Stopped restarting it (Reset demo tries again).", flush=True)
                self.sim_proc = None
                continue
            print(f"[dashboard] DEMO simulator exited ({p.returncode}); restarting", flush=True)
            self.start_sim()

    async def reset_demo(self) -> None:
        """'Reset demo': restart the simulator and start a fresh demo session."""
        print("[dashboard] DEMO reset: restarting the simulator", flush=True)
        self.stop_sim()
        for fut in self._pending.values():
            fut.cancel()
        self._pending.clear()
        self.reset_counters()
        self.open_csv("idle")
        from demo_specs import SpecBoard
        self.board = SpecBoard()
        self.start_sim()
        await self.broadcast({"type": "reset"})
        await self.broadcast(self.stats())

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
    s = Session(args.rig, demo=args.demo)
    print(f"[dashboard] logging to {s.csv_path}", flush=True)
    tasks = [s.rig_loop(), s.stats_loop()]
    if args.demo:
        s.start_sim()
        tasks += [s.demo_control_loop(), s.sim_watchdog()]
    try:
        async with serve(s.browser_handler, "127.0.0.1", args.port, process_request=s.http,
                         ping_interval=None):
            url = f"http://127.0.0.1:{args.port}/"
            print(f"[dashboard] UI at {url}", flush=True)
            if not args.no_browser:
                webbrowser.open(url)
            await asyncio.gather(*tasks)
    finally:
        s.stop_sim()
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
    ap.add_argument("--demo", action="store_true",
                    help="DEMO mode: start tools/fake_rig.py and show the Specifications panel (no ESP32)")
    args = ap.parse_args()
    if args.demo:
        if args.rig != ap.get_default("rig") and args.rig != DEMO_RIG_URL:
            ap.error("--demo runs only against its own local simulator; do not pass --rig")
        args.rig = DEMO_RIG_URL
        # The demo must talk to the simulator it starts itself, not to one already running.
        import socket
        for port in (8765, DEMO_CONTROL_PORT, args.port):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                try:
                    probe.bind(("127.0.0.1", port))  # instant; a connect probe can hang on Windows
                except OSError:
                    ap.error(f"port {port} on 127.0.0.1 is already in use (another fake_rig or demo "
                             "running?). Close it and start run_demo.bat again.")
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
