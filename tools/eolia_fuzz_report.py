#!/usr/bin/env python3
"""Mine eolia_fuzz.py JSONL logs for impossible / silently-ignored combinations.

    python tools/eolia_fuzz_report.py fuzz_runs/run_*.jsonl [--min-fail 2]

Sections:
  1. outcome counts
  2. rejections grouped by (error code, target mode)
  3. minimal "always rejected" feature sets (single features, then pairs, that were
     rejected >= --min-fail times and NEVER accepted anywhere in the logs)
  4. accepted-but-modified: what the server silently ignored / substituted, per mode
  5. server-forced side effects (fields that changed although not requested)
  6. drift between a PUT response and the next GET (state changing behind our back)
  7. /customsettings outcomes
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path


def load(paths):
    recs = []
    for p in paths:
        for line in Path(p).read_text().splitlines():
            if line.strip():
                recs.append(json.loads(line))
    return recs


def features(rec):
    """Feature set describing the request as SENT (payload), plus the state it came from."""
    p = rec["payload"]
    pre = rec["pre"]
    f = {
        f"mode={p['operation_mode']}",
        f"status={p['operation_status']}",
        f"nanoex={p['nanoex']}",
        f"ai={p['ai_control']}",
        f"air_flow={p['air_flow']}",
        f"shield_hit={p['wind_shield_hit']}",
        f"fan={p['wind_volume']}",
        f"vane={p['wind_direction']}",
        f"horizon={p['wind_direction_horizon']}",
        f"airquality={p['airquality']}",
        f"silence={p.get('silence_control')}",
        f"temp={'0' if p['temperature'] == 0 else ('half' if p['temperature'] % 1 else 'int')}",
        f"humidity={'yes' if 'humidity' in p else 'no'}",
        f"from_mode={pre['operation_mode']}",
        f"from_status={pre['operation_status']}",
    }
    if "temperature" in rec["changes"]:
        f.add(f"temp_val={p['temperature']}")
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("logs", nargs="+")
    ap.add_argument("--min-fail", type=int, default=2)
    args = ap.parse_args()
    recs = load(args.logs)
    status = [r for r in recs if r.get("kind") == "status"]
    cs = [r for r in recs if r.get("kind") == "customsettings"]

    print("== 1. outcomes ==")
    print("  /status:", dict(Counter(r["outcome"] for r in status)))
    print("  /customsettings:", dict(Counter(r["outcome"] for r in cs)))
    print("  lockouts:", sum(1 for r in recs if r.get("kind") == "lockout"))

    print("\n== 2. rejections by (code, target mode) ==")
    rej = [r for r in status if r["outcome"] == "rejected"]
    c = Counter((r["code"], r["payload"]["operation_mode"], r["payload"]["operation_status"]) for r in rej)
    for (code, mode, on), n in c.most_common():
        print(f"  {n:3d}  {code}  mode={mode} status={on}")
    msgs = {}
    for r in rej:
        msgs.setdefault(r["code"], r["message"])
    for code, m in msgs.items():
        print(f"  msg {code}: {m}")

    print("\n== 3. minimal always-rejected feature sets ==")
    accepted = [features(r) for r in status if r["outcome"] in ("ok", "modified")]
    rejected = [features(r) for r in rej]
    acc_single = Counter(x for f in accepted for x in f)
    rej_single = Counter(x for f in rejected for x in f)
    singles = {x for x, n in rej_single.items() if n >= args.min_fail and acc_single[x] == 0}
    for x in sorted(singles):
        print(f"  single  {x}   (rejected {rej_single[x]}x, accepted 0x)")
    acc_pair = Counter(p for f in accepted for p in combinations(sorted(f), 2))
    rej_pair = Counter(p for f in rejected for p in combinations(sorted(f), 2))
    for pair, n in sorted(rej_pair.items(), key=lambda kv: -kv[1]):
        if n < args.min_fail or acc_pair[pair] or set(pair) & singles:
            continue
        # Skip pairs with only "from_" bookkeeping features unless both sides matter.
        print(f"  pair    {pair[0]} + {pair[1]}   (rejected {n}x, accepted 0x)")

    print("\n== 4. accepted but modified (per requested change) ==")
    ign = defaultdict(lambda: [0, 0])  # (mode, field, sent, got) -> [modified, total attempts]
    for r in status:
        if r["outcome"] not in ("ok", "modified"):
            continue
        mode = r["payload"]["operation_mode"]
        for f in r["changes"]:
            if f in r["payload"]:
                key = (mode, f, str(r["payload"][f]))
                ign[key][1] += 1
        for f, m in r.get("mismatch", {}).items():
            if m["requested"]:
                ign[(mode, f, str(m["sent"]))][0] += 1
    for (mode, f, sent), (bad, tot) in sorted(ign.items()):
        if bad:
            print(f"  {bad}/{tot}  mode={mode}: {f}={sent} not applied")
    subs = Counter()
    for r in status:
        for f, m in r.get("mismatch", {}).items():
            if m["requested"]:
                subs[(r["payload"]["operation_mode"], f, str(m["sent"]), str(m["got"]))] += 1
    for (mode, f, sent, got), n in subs.most_common():
        print(f"  {n:3d}  mode={mode}: {f} sent {sent} -> got {got}")

    print("\n== 5. server-forced side effects (not requested) ==")
    side = Counter()
    for r in status:
        for f, m in r.get("mismatch", {}).items():
            if not m["requested"]:
                side[(r["payload"]["operation_mode"], f, str(m["sent"]), str(m["got"]))] += 1
    for (mode, f, sent, got), n in side.most_common():
        print(f"  {n:3d}  mode={mode}: {f} {sent} -> {got}")

    print("\n== 6. drift (PUT response vs next GET) ==")
    d = Counter()
    for r in status:
        for f, v in (r.get("drift") or {}).items():
            d[(f, str(v["put_response"]), str(v["next_get"]))] += 1
    for (f, a, b), n in d.most_common():
        print(f"  {n:3d}  {f}: {a} -> {b}")

    print("\n== 7. /customsettings ==")
    for r in cs:
        tail = r.get("code") or ""
        if r["outcome"] == "modified":
            tail = f"sent={r['mismatch']['sent']} got={r['mismatch']['got']}"
        print(
            f"  {r['outcome']:<8} {r['pre_cs']} -> {r['changes']['double_mode_temp']} "
            f"{tail}  unit_after={r.get('post', {}).get('operation_mode')}/{r.get('post', {}).get('operation_status')}"
        )


if __name__ == "__main__":
    main()
