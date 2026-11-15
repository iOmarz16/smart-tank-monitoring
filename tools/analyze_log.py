"""Analyze a dashboard session CSV for the ICS rig tests (Table I).

    dashboard\\.venv\\Scripts\\python tools\\analyze_log.py dashboard\\logs\\session_....csv

I1  latency t_arrival - t_esp (offset-corrected), replayed samples excluded:
    median, p95, max; pass if every update <= 1.0 s
I2  loop period and logging: mean <= 100 ms, no period > 110 ms, >= 5 samples in every
    1 s window, timestamps strictly increasing, no missing seq (no gap after the outage)
I5  PI recomputation from the logged P_f (Kc 36, Ti 8 s, Ts 0.1 s): |logged - recomputed|
    duty <= 1 % at every RUN sample, output within 0..100 % without windup
Extras: I3 trip timing (t3 from read-back when enabled, else software) and I6a warning lead.
SIM   per SIM RAMP run (A35): fitted slope vs target +/-10 %, warning lead before 20 mbar(g)
    (>= 10 s), trip decision -> output time. I5 stays RUN-only (Table I is about real runs).
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
        prev_was = prev_state
        restart = state == "RUN" and not (contiguous and prev_state == "RUN")
        prev_seq, prev_state = seq, state
        if state != "RUN":
            prev_li = None
            continue
        u_log, li = num(r["u_open"]), num(r["integ"])
        if restart:
            seg += 1
            prev_li = None
            if contiguous and prev_was != "TRIPPED":
                integ = 0.0          # START from IDLE: firmware resets the integrator
            else:
                # log gap, or SOFT_LIMIT auto-clear (TRIPPED -> RUN, A30): the firmware
                # initialises the integrator for a bumpless restart; resume from the log.
                integ = li
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
        # t3: with read-back the edge time arrives a sample or two after the trip sample
        t3, src = None, None
        for r in rows:
            if int(r["seq"]) < seq:
                continue
            if r["state"] != "TRIPPED":
                break
            src = r.get("t3_source") or None
            if src in ("SOFTWARE", "READBACK", "READBACK_TIMEOUT"):
                t3 = num(r.get("t3_us", ""))
                break
        if src is None:  # log from firmware before t3 existed
            t3, src = out, "SOFTWARE (trip_output_us)"
        if src == "READBACK_TIMEOUT":
            print("    t3: READBACK_TIMEOUT - no MOSFET OFF edge within 1 s (check wiring / dividers)")
        if t1 and t3:
            print(f"    t3 - t1 (first sample >= {lim:g} -> outputs OFF, {src}): "
                  f"{t3 / 1000 - num(t1['t_esp_ms']):.1f} ms   (I3a needs <= 1000 ms)")
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


def sim_runs(rows, quiet: bool = False) -> list[dict]:
    """SIM RAMP runs (A35): every contiguous stretch of simulated == 1 samples.

    Per run: least-squares slope of the simulated P_raw against t_esp_ms (mbar/min) vs the
    target rate (+/-10 %); warning lead = first sample of the warning stretch that is
    still on when the simulated pressure first reaches 20 mbar(g), up to that sample
    (IS1/I6 need >= 10 s); trip decision -> output write time (software timestamps).
    The trip sample itself is still simulated (the trip ends SIM on the next sample)."""
    lim = C["P_SOFT_LIMIT"]
    runs: list[list[dict]] = []
    prev_seq = None
    for r in rows:
        if not flag(r.get("simulated", "0")):
            prev_seq = None
            continue
        seq = int(r["seq"])
        if prev_seq is None or seq != prev_seq + 1 or num(r["sim_rate"]) != num(runs[-1][-1]["sim_rate"]):
            runs.append([])
        runs[-1].append(r)
        prev_seq = seq
    out = []
    if not quiet:
        print("\n== SIM RAMP runs (A35: simulated pressure; real protection stays on) ==")
        if not runs:
            print("  no SIM samples in this log")
    for i, run in enumerate(runs, 1):
        rate = num(run[0]["sim_rate"]) or 0.0
        pts = [(num(r["t_esp_ms"]) / 1000.0, num(r["P_raw"])) for r in run
               if num(r["P_raw"]) is not None and num(r["t_esp_ms"]) is not None]
        fit = math.nan
        if len(pts) >= 3:
            n = len(pts)
            mt = sum(t for t, _ in pts) / n
            mp = sum(p for _, p in pts) / n
            sxx = sum((t - mt) ** 2 for t, _ in pts)
            if sxx > 0:
                fit = sum((t - mt) * (p - mp) for t, p in pts) / sxx * 60.0  # mbar/min
        slope_ok = rate > 0 and not math.isnan(fit) and abs(fit - rate) <= 0.10 * rate
        # warning lead before the simulated pressure reaches the soft limit
        k20 = next((k for k, r in enumerate(run) if (num(r["P_raw"]) or -1e9) >= lim), None)
        lead = None
        if k20 is not None and flag(run[k20]["warning"]):
            k = k20
            while k > 0 and flag(run[k - 1]["warning"]):
                k -= 1
            lead = (num(run[k20]["t_esp_ms"]) - num(run[k]["t_esp_ms"])) / 1000.0
        elif k20 is not None:
            lead = 0.0  # reached 20 without a warning
        # trip: the first TRIPPED sample of the run (the step that decided it)
        trip = next((r for r in run if r["state"] == "TRIPPED"), None)
        dec_out = None
        if trip is not None:
            dec, outp = num(trip["trip_decision_us"]), num(trip["trip_output_us"])
            if dec and outp:
                dec_out = outp - dec
        end = "trip" if trip is not None else "ended without a trip (SIM EXIT / STOP / log end)"
        res = {"run": i, "seq0": int(run[0]["seq"]), "seq1": int(run[-1]["seq"]), "rate": rate,
               "fit": fit, "slope_ok": slope_ok, "lead_s": lead,
               "lead_ok": lead is not None and lead >= 10.0, "trip": trip is not None,
               "trip_reason": trip["trip_reason"] if trip is not None else None,
               "real_at_trip": num(trip.get("P_real", "")) if trip is not None else None,
               "dec_out_us": dec_out, "end": end}
        out.append(res)
        if quiet:
            continue
        dur = (num(run[-1]["t_esp_ms"]) - num(run[0]["t_esp_ms"])) / 1000.0
        print(f"  run {i}: seq {res['seq0']}..{res['seq1']} ({dur:.1f} s), target {rate:g} mbar/min, "
              f"{end}")
        print(f"    fitted slope     : {fit:.2f} mbar/min ({(fit / rate - 1) * 100:+.2f} %)  "
              f"target +/-10 %: {verdict(slope_ok)}" if rate else "    fitted slope     : no rate")
        if lead is None:
            print(f"    warning lead     : n/a (simulated pressure never reached {lim:g} mbar(g))")
        else:
            print(f"    warning lead     : {lead:.1f} s before {lim:g} mbar(g)  "
                  f"(IS1/I6 need >= 10 s): {verdict(res['lead_ok'])}")
        if trip is not None:
            print(f"    trip             : {res['trip_reason']}, P_sim {trip['P_raw']}, "
                  f"P_real {trip.get('P_real', '')}")
            print("    decision -> output: " + (f"{dec_out:.0f} us (software timestamps)"
                                                if dec_out is not None else "n/a"))
    return out


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
    sims = sim_runs(rows)
    if sims:
        res["SIM"] = all(r["slope_ok"] and (r["lead_s"] is None or r["lead_ok"]) for r in sims)
    print("\nsummary: " + "  ".join(f"{k} {verdict(v)}" for k, v in res.items()))
    print("(I5 FAIL with 'no RUN samples' just means the log has no RUN period.)")
    return 0 if all(res.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
