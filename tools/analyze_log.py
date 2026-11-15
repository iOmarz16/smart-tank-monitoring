"""Analyze a dashboard session CSV for the ICS rig tests (Table I).

    dashboard\\.venv\\Scripts\\python tools\\analyze_log.py dashboard\\logs\\session_....csv

I1  latency t_arrival - t_esp (offset-corrected), replayed samples excluded:
    median, p95, max; pass if every update <= 1.0 s
I2  loop period and logging: mean <= 100 ms, no period > 110 ms, >= 5 samples in every
    1 s window, timestamps strictly increasing, no missing seq (no gap after the outage)
I5  PI recomputation from the logged P_f (Kc 36, Ti 8 s, Ts 0.1 s): |logged - recomputed|
    duty <= 1 % at every RUN sample, output within 0..100 % without windup
Extras: I3 trip timestamps (decision -> output write) and I6a warning lead before 20 mbar(g).
Standard library only. Gains and limits are read from firmware/include/config.h.
"""

from __future__ import annotations

import argparse
import csv
import math
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_config() -> dict:
    txt = (ROOT / "firmware" / "include" / "config.h").read_text(encoding="utf-8")
    cfg = {}
    for name, raw in re.findall(r"constexpr\s+[\w:]+\s+(\w+)\s*=\s*([-\d.]+)[fF]?\s*;", txt):
        cfg[name] = float(raw)
    return cfg


C = load_config()


def num(v: str):
    if v in ("", "None", "null"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def flag(v: str) -> bool:
    return v in ("True", "true", "1")


def pct(sorted_vals, q):
    if not sorted_vals:
        return math.nan
    k = (len(sorted_vals) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def verdict(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def load(path: Path) -> list[dict]:
    rows: dict[int, dict] = {}
    with path.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            seq = int(r["seq"])
            if seq not in rows:  # dedup by seq, first copy wins (same rule as the dashboard)
                rows[seq] = r
    return [rows[k] for k in sorted(rows)]


# ---------------------------------------------------------------------------
def i1(rows) -> bool:
    lat = sorted(num(r["latency_ms"]) for r in rows
                 if not flag(r["replayed"]) and num(r["latency_ms"]) is not None)
    print("\n== I1  sensor-to-dashboard latency (Spec 4, <= 1.0 s) ==")
    if not lat:
        print("  no live samples with a latency value")
        return False
    over = sum(1 for v in lat if v > 1000.0)
    print(f"  updates measured : {len(lat)}  (replayed excluded: "
          f"{sum(1 for r in rows if flag(r['replayed']))})")
    print(f"  median / p95 / max: {statistics.median(lat):.1f} / {pct(lat, .95):.1f} / {lat[-1]:.1f} ms")
    print(f"  min              : {lat[0]:.1f} ms" + ("  (negative: offset error)" if lat[0] < 0 else ""))
    print(f"  updates > 1.0 s  : {over}")
    print("  note: arrival at the dashboard backend; the 240 fps video cross-check covers screen drawing")
    ok = over == 0
    print(f"  -> {verdict(ok)}")
    return ok


def i2(rows) -> bool:
    print("\n== I2  loop period and logging (Spec 5) ==")
    seqs = [int(r["seq"]) for r in rows]
    missing = (seqs[-1] - seqs[0] + 1) - len(seqs)
    gaps = [(a, b) for a, b in zip(seqs, seqs[1:]) if b != a + 1]
    periods = [num(r["loop_period_us"]) / 1000.0 for r in rows[1:] if num(r["loop_period_us"])]
    # The first logged sample's period may span a reconnect/boot; skip seq 0.
    periods = [p for r, p in zip(rows[1:], periods) if int(r["seq"]) != 0]
    t = [num(r["t_esp_ms"]) for r in rows]
    increasing = all(b > a for a, b in zip(t, t[1:]))
    t0 = t[0]
    windows: dict[int, int] = {}
    for v in t:
        windows[int((v - t0) // 1000)] = windows.get(int((v - t0) // 1000), 0) + 1
    full = [n for w, n in windows.items() if w != max(windows)]  # last window may be partial
    min_win = min(full) if full else None
    mean_p = statistics.fmean(periods) if periods else math.nan
    max_p = max(periods) if periods else math.nan
    over110 = sum(1 for p in periods if p > 110.0)
    dur = (t[-1] - t[0]) / 1000.0
    print(f"  span             : seq {seqs[0]}..{seqs[-1]}, {dur:.1f} s on the ESP32 clock")
    print(f"  loop period      : mean {mean_p:.3f} ms, max {max_p:.3f} ms, > 110 ms: {over110}")
    print(f"  samples per 1 s  : min {min_win} (over {len(full)} full windows)")
    print(f"  t_esp increasing : {increasing}")
    print(f"  missing seq      : {missing}" + (f"  gaps: {gaps[:5]}{' ...' if len(gaps) > 5 else ''}" if gaps else ""))
    ok = mean_p <= 100.0 + 0.5 and over110 == 0 and (min_win or 0) >= 5 and increasing and missing == 0
    print("  note: mean <= 100 ms is checked with 0.5 ms tolerance for timer rounding")
    print(f"  -> {verdict(ok)}")
    return ok


def i5(rows, tol=1.0) -> bool:
    print("\n== I5  PI recomputation (IS2, Kc 36, Ti 8 s, Ts 0.1 s, <= 1 %) ==")
    kc, ti, ts, sp = C["KC"], C["TI_S"], C["TS_S"], C["SETPOINT"]
    k = ts / ti
    worst_a = worst_b = worst_d = 0.0
    n = seg = bad_range = windup = 0
    integ = 0.0
    prev_seq, prev_state = None, None
    prev_li: float | None = None
    for r in rows:
        seq, state = int(r["seq"]), r["state"]
        contiguous = prev_seq is not None and seq == prev_seq + 1
        restart = state == "RUN" and not (contiguous and prev_state == "RUN")
        prev_seq, prev_state = seq, state
        if state != "RUN":
            prev_li = None
            continue
        u_log, li = num(r["u_open"]), num(r["integ"])
        if restart:
            seg += 1
            prev_li = None
            if contiguous:
                integ = 0.0          # START from IDLE: firmware resets the integrator
            else:
                integ = li           # log gap: resume the recursion from the logged value
                continue
        pf = num(r["P_f"])
        if not flag(r["valid"]) or pf is None:
            continue                 # sensor fault: integrator frozen, duty held
        e = pf - sp
        # (a) one-step: the logged integrator and P_f must give the logged output
        u_a = min(100.0, max(0.0, kc * (e + k * li)))
        # (b) full recursion with conditional integration, from logged P_f only
        s = integ + e
        u_cand = kc * (e + k * s)
        if (u_cand > 100 and e > 0) or (u_cand < 0 and e < 0):
            s = integ
        integ = s
        u_b = min(100.0, max(0.0, kc * (e + k * integ)))
        worst_a = max(worst_a, abs(u_a - u_log))
        worst_b = max(worst_b, abs(u_b - u_log))
        duty = num(r["valve_duty"])
        if duty is not None:
            worst_d = max(worst_d, abs((100.0 - u_log) - duty))
        if not 0.0 <= u_log <= 100.0:
            bad_range += 1
        # windup: if integrating this e would saturate the output in e's direction,
        # the logged integrator must not have moved (conditional integration)
        if prev_li is not None:
            u_c = kc * (e + k * (prev_li + e))
            if ((u_c > 100 and e > 0) or (u_c < 0 and e < 0)) and abs(li - prev_li) > 1e-4:
                windup += 1
        prev_li = li
        n += 1
    if n == 0:
        print("  no RUN samples")
        return False
    print(f"  RUN samples      : {n} in {seg} segment(s)")
    print(f"  max |u - u_log|, one step (logged integrator) : {worst_a:.4f} %")
    print(f"  max |u - u_log|, full recursion from P_f      : {worst_b:.4f} %")
    print(f"  max |(100 - u_open) - valve_duty|             : {worst_d:.4f} %")
    print(f"  u outside 0..100 : {bad_range}; windup violations: {windup}")
    ok = max(worst_a, worst_b, worst_d) <= tol and bad_range == 0 and windup == 0
    print(f"  -> {verdict(ok)}")
    return ok


def extras(rows) -> None:
    print("\n== Extras: I3 trip timestamps / I6a warning lead (information only) ==")
    lim = C["P_SOFT_LIMIT"]
    trips = []
    for a, b in zip(rows, rows[1:]):
        if a["state"] != "TRIPPED" and b["state"] == "TRIPPED":
            trips.append(b)
    if not trips:
        print("  no trip in this log")
    for b in trips:
        dec, out = num(b["trip_decision_us"]), num(b["trip_output_us"])
        seq = int(b["seq"])
        # t1: first valid sample >= 20 mbar(g) up to the trip sample
        t1 = next((r for r in rows if int(r["seq"]) <= seq and flag(r["valid"])
                   and (num(r["P_raw"]) or -1) >= lim and int(r["seq"]) > seq - 50), None)
        print(f"  trip at seq {seq}: reason {b['trip_reason']}, P_raw {b['P_raw']}")
        if dec and out:
            print(f"    decision -> output write: {(out - dec):.0f} us (software timestamps)")
        if t1 and out:
            print(f"    t1 (first sample >= {lim:g}) -> output write: {out / 1000 - num(t1['t_esp_ms']):.1f} ms")
        # I6a: first warning sample before t1 within the preceding run-up
        if t1:
            t1_ms = num(t1["t_esp_ms"])
            w = None
            for r in reversed([r for r in rows if int(r["seq"]) <= int(t1["seq"])]):
                if flag(r["warning"]):
                    w = r
                elif w is not None:
                    break
            if w is not None:
                lead_esp = (t1_ms - num(w["t_esp_ms"])) / 1000
                print(f"    warning lead (ESP32 clock): {lead_esp:.1f} s  (IS1 needs >= 10 s)")
                ta_w, off = num(w["t_arrival_pc_ms"]), num(t1["clock_offset_ms"])
                if ta_w is not None and off is not None and not flag(w["replayed"]):
                    lead_dash = (t1_ms + off - ta_w) / 1000
                    print(f"    warning lead at the dashboard: {lead_dash:.1f} s")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", type=Path)
    ap.add_argument("--from-seq", type=int, default=None, help="analyze from this seq")
    ap.add_argument("--to-seq", type=int, default=None, help="analyze up to this seq")
    a = ap.parse_args()
    rows = load(a.csv)
    if a.from_seq is not None:
        rows = [r for r in rows if int(r["seq"]) >= a.from_seq]
    if a.to_seq is not None:
        rows = [r for r in rows if int(r["seq"]) <= a.to_seq]
    if len(rows) < 2:
        print("not enough samples")
        return 2
    print(f"file: {a.csv}  ({len(rows)} unique samples)")
    res = {"I1": i1(rows), "I2": i2(rows), "I5": i5(rows)}
    extras(rows)
    print("\nsummary: " + "  ".join(f"{k} {verdict(v)}" for k, v in res.items()))
    print("(I5 FAIL with 'no RUN samples' just means the log has no RUN period.)")
    return 0 if all(res.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
