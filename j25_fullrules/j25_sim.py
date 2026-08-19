#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

PACKET_API = "https://api.github.com/repos/taw/magic-preconstructed-decks/contents/data/jumpstart/j25?ref=master"
RAW_BASE = "https://raw.githubusercontent.com/taw/magic-preconstructed-decks/master/data/jumpstart/j25/"
EXPECTED_PACKETS = 121


def get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "j25-fullrules-actions/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def get_text(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "j25-fullrules-actions/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8")


def parse_packet(name: str, text: str):
    cards = Counter()
    sideboard = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        if line.lower() == "sideboard":
            sideboard = True
            continue
        if sideboard:
            continue
        line = re.sub(r"\s+\[[^\]]+\]\s*$", "", line)
        m = re.match(r"^(\d+)\s+(.+?)\s*$", line)
        if not m:
            raise RuntimeError(f"Cannot parse {name}: {raw}")
        cards[m.group(2)] += int(m.group(1))
    if sum(cards.values()) != 20:
        raise RuntimeError(f"{name}: expected 20 cards, got {sum(cards.values())}")
    return cards


def load_packets(cache: Path):
    cache.mkdir(parents=True, exist_ok=True)
    manifest = get_json(PACKET_API)
    files = sorted(x["name"] for x in manifest if x.get("type") == "file" and x["name"].endswith(".txt"))
    if len(files) != EXPECTED_PACKETS:
        raise RuntimeError(f"Expected 121 packets, got {len(files)}")
    packets = {}
    for fn in files:
        p = cache / fn
        if not p.exists():
            p.write_text(get_text(RAW_BASE + urllib.parse.quote(fn)), encoding="utf-8")
        packets[Path(fn).stem] = parse_packet(Path(fn).stem, p.read_text(encoding="utf-8"))
    return packets


def all_combos(packets):
    names = sorted(packets)
    out = []
    idx = 0
    for i, a in enumerate(names):
        for j in range(i, len(names)):
            b = names[j]
            cards = packets[a] + packets[b]
            if sum(cards.values()) != 40:
                raise RuntimeError("Combo is not 40 cards")
            out.append({"id": f"C{idx:04d}", "a": a, "b": b, "cards": cards})
            idx += 1
    if len(out) != 7381:
        raise RuntimeError(f"Expected 7381 combos, got {len(out)}")
    return out


def write_deck(path: Path, combo):
    lines = ["[metadata]", f"Name={combo['id']}", "[main]"]
    for card, qty in sorted(combo["cards"].items()):
        lines.append(f"{qty} {card}")
    lines += ["[sideboard]", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def write_combo_map(path: Path, combos):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["combo_id", "packet_a", "packet_b"])
        for c in combos:
            w.writerow([c["id"], c["a"], c["b"]])


def parse_tournament(text: str):
    rows = []
    current = None
    pair_re = re.compile(r"^Round\s+\d+\s+-\s+(C\d+)\[\d+\]\s+vs\s+(C\d+)\[\d+\]")
    win_re = re.compile(r"^Match Winner\s+-\s+(C\d+)!", re.I)
    for raw in text.splitlines():
        line = raw.strip()
        m = pair_re.search(line)
        if m:
            current = (m.group(1), m.group(2))
            continue
        m = win_re.search(line)
        if m and current:
            winner = m.group(1)
            a, b = current
            if winner not in (a, b):
                raise RuntimeError(f"Winner {winner} not in current pairing {current}")
            rows.append((a, b, winner))
            current = None
    return rows


def run_group(forge_jar: Path, combos_by_id, ids, work: Path, match_size: int, timeout: int):
    group_dir = work / "group"
    if group_dir.exists():
        shutil.rmtree(group_dir)
    group_dir.mkdir(parents=True)
    for cid in ids:
        write_deck(group_dir / f"{cid}.dck", combos_by_id[cid])
    cmd = [
        "java", "-Xms256m", "-Xmx3g", "-jar", str(forge_jar.resolve()),
        "sim", "-D", str(group_dir.resolve()), "-t", "RoundRobin", "-m", str(match_size),
        "-q", "-c", str(timeout)
    ]
    p = subprocess.run(cmd, cwd=str(forge_jar.parent), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"Forge exited {p.returncode}:\n{p.stdout[-10000:]}")
    rows = parse_tournament(p.stdout)
    expected = len(ids) * (len(ids) - 1) // 2
    if len(rows) != expected:
        raise RuntimeError(f"Parsed {len(rows)}/{expected} matches. Tail:\n{p.stdout[-12000:]}")
    return rows


def shard(args):
    root = Path(args.workdir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    packets = load_packets(root / "packets")
    combos = all_combos(packets)
    combos_by_id = {c["id"]: c for c in combos}
    write_combo_map(root / "combo_map.csv", combos)
    forge_jar = Path(args.forge_jar).resolve()
    if not forge_jar.exists():
        raise SystemExit(f"Forge jar missing: {forge_jar}")

    out = Path(args.output).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    done_keys = set()
    if out.exists():
        with out.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                done_keys.add((int(r["sample_round"]), int(r["group_no"])))
    newfile = not out.exists()
    fout = out.open("a", newline="", encoding="utf-8")
    w = csv.writer(fout)
    if newfile:
        w.writerow(["sample_round", "group_no", "deck_a", "deck_b", "winner"])

    global_group = 0
    completed = 0
    for round_no in range(args.rounds):
        ids = [c["id"] for c in combos]
        random.Random(args.seed + round_no).shuffle(ids)
        groups = [ids[i:i + args.group_size] for i in range(0, len(ids), args.group_size)]
        for group_no, group in enumerate(groups):
            if len(group) < 2:
                global_group += 1
                continue
            assigned = (global_group % args.shards) == args.shard
            global_group += 1
            if not assigned or (round_no, group_no) in done_keys:
                continue
            with tempfile.TemporaryDirectory(prefix="j25grp_") as td:
                rows = run_group(forge_jar, combos_by_id, group, Path(td), args.match_size, args.timeout)
            for a, b, winner in rows:
                w.writerow([round_no, group_no, a, b, winner])
            fout.flush()
            completed += 1
            print(f"Shard {args.shard}: round {round_no+1}/{args.rounds}, group {group_no+1}/{len(groups)}, matches={len(rows)}")
    fout.close()
    print(f"Shard {args.shard} complete: {completed} groups newly run")


def load_all_results(inputs: Path):
    rows = []
    for p in sorted(inputs.rglob("*.csv")):
        if p.name == "combo_map.csv":
            continue
        try:
            with p.open(encoding="utf-8") as f:
                rd = csv.DictReader(f)
                if not rd.fieldnames or "winner" not in rd.fieldnames:
                    continue
                rows.extend((r["deck_a"], r["deck_b"], r["winner"]) for r in rd)
        except UnicodeDecodeError:
            continue
    return rows


def fit_elo(combo_ids, rows, iters=80):
    rating = {cid: 1500.0 for cid in combo_ids}
    games = defaultdict(int)
    wins = defaultdict(float)
    for a, b, winner in rows:
        games[a] += 1; games[b] += 1
        wins[winner] += 1.0
    for _ in range(iters):
        grad = defaultdict(float)
        weight = defaultdict(float)
        for a, b, winner in rows:
            ea = 1.0 / (1.0 + 10 ** ((rating[b] - rating[a]) / 400.0))
            ya = 1.0 if winner == a else 0.0
            g = ya - ea
            grad[a] += g; grad[b] -= g
            weight[a] += 1; weight[b] += 1
        maxmove = 0.0
        nr = {}
        for cid in combo_ids:
            move = 28.0 * grad[cid] / max(1.0, math.sqrt(weight[cid]))
            move = max(-18.0, min(18.0, move))
            nr[cid] = rating[cid] + move
            maxmove = max(maxmove, abs(move))
        mean = sum(nr.values()) / len(nr)
        rating = {k: v - mean + 1500.0 for k, v in nr.items()}
        if maxmove < 0.005:
            break
    return rating, games, wins


def read_combo_map(path: Path):
    out = {}
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[r["combo_id"]] = (r["packet_a"], r["packet_b"])
    return out


def aggregate(args):
    inputs = Path(args.inputs).resolve()
    combo_map = read_combo_map(Path(args.combo_map).resolve())
    rows = load_all_results(inputs)
    if not rows:
        raise SystemExit("No result rows found")
    rating, games, wins = fit_elo(sorted(combo_map), rows)
    outdir = Path(args.output).resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    combo_rows = []
    for cid, (pa, pb) in combo_map.items():
        n = games[cid]
        wr = wins[cid] / n if n else 0.0
        combo_rows.append((cid, pa, pb, n, wr, rating[cid]))
    combo_rows.sort(key=lambda x: x[5], reverse=True)
    with (outdir / "combo_ranking.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["rank", "combo_id", "packet_a", "packet_b", "games", "win_rate", "elo"])
        for i, r in enumerate(combo_rows, 1): w.writerow([i, *r])

    packet_vals = defaultdict(list)
    for cid, pa, pb, n, wr, elo in combo_rows:
        packet_vals[pa].append(elo)
        packet_vals[pb].append(elo)
    packet_rows = []
    for p, vals in packet_vals.items():
        mean = sum(vals) / len(vals)
        sd = math.sqrt(sum((x-mean)**2 for x in vals) / max(1, len(vals)-1))
        packet_rows.append((p, len(vals), mean, sd))
    packet_rows.sort(key=lambda x: x[2], reverse=True)
    with (outdir / "packet_ranking.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["rank", "packet", "combo_count", "mean_combo_elo", "combo_elo_sd"])
        for i, r in enumerate(packet_rows, 1): w.writerow([i, *r])

    with (outdir / "top64.txt").open("w", encoding="utf-8") as f:
        for r in combo_rows[:64]: f.write(r[0] + "\n")

    summary = {
        "match_results": len(rows),
        "decks": len(combo_map),
        "packets": len(packet_rows),
        "top20_combos": [
            {"rank": i+1, "combo_id": r[0], "packet_a": r[1], "packet_b": r[2], "games": r[3], "win_rate": r[4], "elo": r[5]}
            for i, r in enumerate(combo_rows[:20])
        ],
        "top20_packets": [
            {"rank": i+1, "packet": r[0], "mean_combo_elo": r[2], "sd": r[3]}
            for i, r in enumerate(packet_rows[:20])
        ]
    }
    (outdir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


def refine(args):
    root = Path(args.workdir).resolve()
    packets = load_packets(root / "packets")
    combos = all_combos(packets)
    combos_by_id = {c["id"]: c for c in combos}
    top_ids = [x.strip() for x in Path(args.top_ids).read_text(encoding="utf-8").splitlines() if x.strip()]
    if len(top_ids) < 2:
        raise SystemExit("Need at least two top deck ids")
    forge_jar = Path(args.forge_jar).resolve()
    with tempfile.TemporaryDirectory(prefix="j25refine_") as td:
        rows = run_group(forge_jar, combos_by_id, top_ids, Path(td), args.match_size, args.timeout)
    out = Path(args.output).resolve(); out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["sample_round", "group_no", "deck_a", "deck_b", "winner"])
        for a, b, winner in rows: w.writerow([999, 0, a, b, winner])
    print(f"Refinement complete: {len(rows)} matches")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("shard")
    s.add_argument("--forge-jar", required=True)
    s.add_argument("--workdir", default="work")
    s.add_argument("--output", required=True)
    s.add_argument("--shard", type=int, required=True)
    s.add_argument("--shards", type=int, required=True)
    s.add_argument("--rounds", type=int, default=3)
    s.add_argument("--group-size", type=int, default=16)
    s.add_argument("--match-size", type=int, default=1)
    s.add_argument("--timeout", type=int, default=180)
    s.add_argument("--seed", type=int, default=20260819)
    s.set_defaults(func=shard)

    a = sub.add_parser("aggregate")
    a.add_argument("--inputs", required=True)
    a.add_argument("--combo-map", required=True)
    a.add_argument("--output", required=True)
    a.set_defaults(func=aggregate)

    r = sub.add_parser("refine")
    r.add_argument("--forge-jar", required=True)
    r.add_argument("--workdir", default="work")
    r.add_argument("--top-ids", required=True)
    r.add_argument("--output", required=True)
    r.add_argument("--match-size", type=int, default=3)
    r.add_argument("--timeout", type=int, default=240)
    r.set_defaults(func=refine)

    args = p.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
