#!/usr/bin/env python3
from __future__ import annotations

import argparse, csv, json, math, os, random, re, shutil, statistics, subprocess, tempfile, time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
import j25_sim_v2 as base

RESULT_HEADER = [
    "sample_round", "group_no", "deck_a", "deck_b", "winner",
    "technical_retry", "timeout_draws", "warning_count",
]
HARD_MARKERS = [
    "Could not load deck", "No deck found", "Exceeded number of exceptions",
    "Game threw exception", "Exception in thread", "StackOverflowError",
]
PAIR_RE = re.compile(r"^Round\s+\d+\s+-\s+(C\d+)\[\d+\]\s+vs\s+(C\d+)\[\d+\]")
WIN_RE = re.compile(r"^Match Winner\s+-\s+(C\d+)!", re.I)
CARD_RE = re.compile(r"\(Card \(([^)]+)\)\)")


def canon_pair(a, b):
    return tuple(sorted((a, b)))


@dataclass
class ParsedRun:
    rows: list[tuple[str, str, str]] = field(default_factory=list)
    timeout_pairs: Counter = field(default_factory=Counter)
    svar_warning_cards: Counter = field(default_factory=Counter)
    warning_lines: int = 0


def parse_tournament(text: str) -> ParsedRun:
    out = ParsedRun(); current = None
    for raw in text.splitlines():
        line = raw.strip()
        m = PAIR_RE.search(line)
        if m:
            current = canon_pair(m.group(1), m.group(2)); continue
        if "Stopping slow match as draw" in line:
            out.warning_lines += 1
            if current: out.timeout_pairs[current] += 1
            continue
        if "SVar " in line and ("not defined" in line or "not found in ability" in line):
            out.warning_lines += 1
            m = CARD_RE.search(line)
            if m: out.svar_warning_cards[m.group(1)] += 1
            continue
        m = WIN_RE.search(line)
        if m and current:
            winner = m.group(1); a, b = current
            if winner not in (a, b):
                raise RuntimeError(f"Winner {winner} not in {current}")
            out.rows.append((a, b, winner)); current = None
    return out


def run_group(forge_jar: Path, byid, ids, work: Path, timeout: int):
    group = work / "group"
    if group.exists(): shutil.rmtree(group)
    group.mkdir(parents=True)
    for cid in ids: base.write_deck(group / f"{cid}.dck", byid[cid])
    cmd = []
    if os.environ.get("J25_XVFB") == "1": cmd += ["xvfb-run", "-a"]
    cmd += [
        "java", "-Xms256m", "-Xmx3g", "-jar", str(forge_jar.resolve()),
        "sim", "-D", str(group.resolve()), "-t", "RoundRobin", "-m", "1",
        "-q", "-c", str(timeout),
    ]
    started = time.monotonic()
    p = subprocess.run(cmd, cwd=str(forge_jar.parent), stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True)
    elapsed = time.monotonic() - started
    text = p.stdout or ""
    hard = [x for x in HARD_MARKERS if x in text]
    if p.returncode != 0:
        raise RuntimeError(f"Forge exited {p.returncode}:\n{text[-16000:]}")
    if hard:
        raise RuntimeError(f"Forge hard anomalies {hard}:\n{text[-16000:]}")
    parsed = parse_tournament(text)
    expected = {canon_pair(a, b) for i, a in enumerate(ids) for b in ids[i+1:]}
    got = {canon_pair(a, b) for a, b, _ in parsed.rows}
    if got != expected or len(parsed.rows) != len(expected):
        missing = sorted(expected - got)[:12]
        raise RuntimeError(f"Parsed {len(parsed.rows)}/{len(expected)} matches; missing={missing}\n{text[-16000:]}")
    return parsed, elapsed


def clean_retry(forge_jar, byid, pair, retry_timeouts):
    last = None; total_draws = 0; warnings = 0; cards = Counter()
    for attempt, timeout in enumerate(retry_timeouts, 1):
        with tempfile.TemporaryDirectory(prefix="j25pair_") as td:
            parsed, elapsed = run_group(forge_jar, byid, list(pair), Path(td), timeout)
        last = parsed.rows[0]
        draws = sum(parsed.timeout_pairs.values()); total_draws += draws
        warnings += parsed.warning_lines; cards.update(parsed.svar_warning_cards)
        print(f"RETRY {pair[0]} vs {pair[1]} attempt={attempt} timeout={timeout}s elapsed={elapsed:.1f}s technical_draws={draws}")
        if draws == 0:
            return last, True, total_draws, warnings, cards
    return last, False, total_draws, warnings, cards


def completed_groups(path: Path):
    if not path.exists(): return set()
    with path.open(encoding="utf-8") as f:
        return {(int(r["sample_round"]), int(r["group_no"])) for r in csv.DictReader(f)}


def checkpoint(path: Path, rnd, group, matches, timeouts):
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new: w.writerow(["sample_round", "group_no", "matches", "timeout_pairs"])
        w.writerow([rnd, group, matches, timeouts]); f.flush(); os.fsync(f.fileno())


def write_telemetry(path: Path, stats):
    d = dict(stats)
    d["svar_warning_cards"] = dict(stats["svar_warning_cards"].most_common())
    times = stats["group_seconds"]
    d["mean_group_seconds"] = statistics.fmean(times) if times else 0
    d["median_group_seconds"] = statistics.median(times) if times else 0
    path.write_text(json.dumps(d, indent=2), encoding="utf-8")


def shard(args):
    root = Path(args.workdir).resolve(); root.mkdir(parents=True, exist_ok=True)
    packets = base.load_packets(root / "packets")
    combos = base.all_combos(packets); byid = {c["id"]: c for c in combos}
    base.write_combo_map(root / "combo_map.csv", combos)
    forge_jar = Path(args.forge_jar).resolve()
    out = Path(args.output).resolve(); out.parent.mkdir(parents=True, exist_ok=True)
    ck = out.with_suffix(".checkpoint.csv"); tele = out.with_suffix(".telemetry.json")
    quarantine = out.with_suffix(".quarantine.csv"); done = completed_groups(ck)
    stats = {
        "shard": args.shard, "groups_completed": 0, "matches_written": 0,
        "groups_with_timeout": 0, "timeout_pairs": 0, "clean_retries": 0,
        "unresolved_timeout_pairs": 0, "warning_lines": 0,
        "svar_warning_cards": Counter(), "group_seconds": [],
    }
    new = not out.exists()
    with out.open("a", newline="", encoding="utf-8") as fout:
        w = csv.writer(fout)
        if new: w.writerow(RESULT_HEADER)
        global_group = 0
        for rnd in range(args.rounds):
            ids = [c["id"] for c in combos]
            random.Random(args.seed + rnd).shuffle(ids)
            groups = [ids[i:i+args.group_size] for i in range(0, len(ids), args.group_size)]
            for gno, group in enumerate(groups):
                if len(group) < 2: global_group += 1; continue
                assigned = global_group % args.shards == args.shard; global_group += 1
                if not assigned or (rnd, gno) in done: continue
                with tempfile.TemporaryDirectory(prefix="j25grp_") as td:
                    parsed, elapsed = run_group(forge_jar, byid, group, Path(td), args.timeout)
                rows = {canon_pair(a,b):(a,b,win) for a,b,win in parsed.rows}
                timeout_pairs = set(parsed.timeout_pairs)
                stats["group_seconds"].append(elapsed); stats["warning_lines"] += parsed.warning_lines
                stats["svar_warning_cards"].update(parsed.svar_warning_cards)
                if timeout_pairs:
                    stats["groups_with_timeout"] += 1; stats["timeout_pairs"] += len(timeout_pairs)
                    print(f"TECHNICAL_TIMEOUTS group={gno} pairs={len(timeout_pairs)}; clean replay follows")
                unresolved = []
                for pair in sorted(timeout_pairs):
                    row, clean, retry_draws, warns, cards = clean_retry(
                        forge_jar, byid, pair, [args.retry_timeout, args.final_retry_timeout])
                    rows[pair] = row; stats["warning_lines"] += warns; stats["svar_warning_cards"].update(cards)
                    if clean: stats["clean_retries"] += 1
                    else:
                        stats["unresolved_timeout_pairs"] += 1
                        unresolved.append([rnd, gno, pair[0], pair[1], row[2], retry_draws])
                expected = len(group)*(len(group)-1)//2
                if len(rows) != expected: raise RuntimeError(f"Accounting mismatch {len(rows)} != {expected}")
                for pair, (a,b,win) in sorted(rows.items()):
                    w.writerow([rnd, gno, a, b, win, int(pair in timeout_pairs), parsed.timeout_pairs.get(pair,0), parsed.warning_lines])
                fout.flush(); os.fsync(fout.fileno())
                if unresolved:
                    qnew = not quarantine.exists()
                    with quarantine.open("a", newline="", encoding="utf-8") as qf:
                        qw = csv.writer(qf)
                        if qnew: qw.writerow(["sample_round","group_no","deck_a","deck_b","last_winner","retry_timeout_draws"])
                        qw.writerows(unresolved)
                checkpoint(ck, rnd, gno, expected, len(timeout_pairs))
                stats["groups_completed"] += 1; stats["matches_written"] += expected
                write_telemetry(tele, stats)
                print(f"SHARD {args.shard} round={rnd+1}/{args.rounds} group={gno+1}/{len(groups)} matches={expected} elapsed={elapsed:.1f}s timeouts={len(timeout_pairs)}")
    write_telemetry(tele, stats); print(json.dumps({k:v for k,v in stats.items() if k not in ('svar_warning_cards','group_seconds')}, indent=2))


def load_results(inputs: Path):
    rows=[]
    for p in sorted(inputs.rglob("*.csv")):
        if p.name.startswith("combo_map") or p.name.endswith("checkpoint.csv") or p.name.endswith("quarantine.csv"): continue
        with p.open(encoding="utf-8") as f:
            rd=csv.DictReader(f)
            if not rd.fieldnames or "winner" not in rd.fieldnames: continue
            for r in rd:
                a,b,win=r.get("deck_a"),r.get("deck_b"),r.get("winner")
                if not a or not b or win not in (a,b): raise RuntimeError(f"Bad result {p}: {r}")
                rows.append((a,b,win))
    return rows


def bt_fit(ids, rows, ridge=0.25, iters=400):
    ids=list(ids); idx={x:i for i,x in enumerate(ids)}; n=len(ids)
    wins=[ridge]*n; ability=[1.0]*n; opp=[defaultdict(int) for _ in range(n)]
    games=defaultdict(int); rawwins=defaultdict(int)
    for a,b,win in rows:
        ia,ib=idx[a],idx[b]; wins[idx[win]] += 1; opp[ia][ib]+=1; opp[ib][ia]+=1
        games[a]+=1; games[b]+=1; rawwins[win]+=1
    for _ in range(iters):
        new=[]
        for i in range(n):
            den=ridge/(ability[i]+1.0)
            for j,nij in opp[i].items(): den += nij/(ability[i]+ability[j])
            new.append(max(1e-12,wins[i]/max(1e-15,den)))
        gm=math.exp(sum(math.log(x) for x in new)/n); new=[x/gm for x in new]
        delta=max(abs(math.log(a)-math.log(b)) for a,b in zip(ability,new)); ability=new
        if delta < 1e-8: break
    scale=400/math.log(10)
    rating={x:1500+scale*math.log(ability[idx[x]]) for x in ids}
    return rating,games,rawwins


def wilson(w,n,z=1.95996398454):
    if not n: return (0.0,1.0)
    p=w/n; d=1+z*z/n; c=(p+z*z/(2*n))/d; m=z*math.sqrt((p*(1-p)+z*z/(4*n))/n)/d
    return max(0,c-m),min(1,c+m)


def collect_telemetry(inputs: Path):
    total=Counter(); cards=Counter(); quarantine=0
    for p in inputs.rglob("*.telemetry.json"):
        try: d=json.loads(p.read_text(encoding="utf-8"))
        except Exception: continue
        for k in ["groups_completed","matches_written","groups_with_timeout","timeout_pairs","clean_retries","unresolved_timeout_pairs","warning_lines"]:
            total[k]+=int(d.get(k,0))
        cards.update(d.get("svar_warning_cards",{}))
    for p in inputs.rglob("*.quarantine.csv"):
        with p.open(encoding="utf-8") as f: quarantine += sum(1 for _ in csv.DictReader(f))
    return {**dict(total),"svar_warning_cards":dict(cards.most_common()),"quarantine_rows":quarantine}


def aggregate(args):
    cmap=base.read_combo_map(Path(args.combo_map).resolve()); inputs=Path(args.inputs).resolve(); rows=load_results(inputs)
    if not rows: raise SystemExit("No results")
    rating,games,wins=bt_fit(sorted(cmap),rows)
    oppsum=defaultdict(float); oppn=defaultdict(int)
    for a,b,_ in rows: oppsum[a]+=rating[b]; oppsum[b]+=rating[a]; oppn[a]+=1; oppn[b]+=1
    out=Path(args.output).resolve(); out.mkdir(parents=True,exist_ok=True)
    cr=[]
    for cid,(pa,pb) in cmap.items():
        n=games[cid]; wn=wins[cid]; wr=wn/n if n else 0; lo,hi=wilson(wn,n); sos=oppsum[cid]/oppn[cid] if oppn[cid] else 1500
        cr.append((cid,pa,pb,n,wn,wr,lo,hi,rating[cid],sos))
    cr.sort(key=lambda x:x[8],reverse=True)
    with (out/"combo_ranking.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.writer(f); w.writerow(["rank","combo_id","packet_a","packet_b","games","wins","win_rate","ci95_low","ci95_high","bt_elo","strength_of_schedule_elo"])
        for i,r in enumerate(cr,1): w.writerow([i,*r])
    pv=defaultdict(list); pwr=defaultdict(list)
    for cid,pa,pb,n,wn,wr,lo,hi,elo,sos in cr:
        pv[pa].append(elo); pwr[pa].append(wr)
        if pb!=pa: pv[pb].append(elo); pwr[pb].append(wr)
    pr=[]
    for p,vals in pv.items():
        pr.append((p,len(vals),statistics.fmean(vals),statistics.median(vals),statistics.stdev(vals) if len(vals)>1 else 0,min(vals),max(vals),statistics.fmean(pwr[p])))
    pr.sort(key=lambda x:x[2],reverse=True)
    with (out/"packet_ranking.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.writer(f); w.writerow(["rank","packet","combo_count","mean_combo_bt_elo","median_combo_bt_elo","combo_bt_elo_sd","worst_partner_bt_elo","best_partner_bt_elo","mean_combo_raw_win_rate"])
        for i,r in enumerate(pr,1): w.writerow([i,*r])
    (out/"top64.txt").write_text("\n".join(r[0] for r in cr[:64])+"\n",encoding="utf-8")
    counts=[r[3] for r in cr]; tel=collect_telemetry(inputs)
    summary={"packet_source_sha":base.PACKET_SOURCE_SHA,"ranking_model":"Bradley-Terry MLE, Elo-scaled","match_results":len(rows),"decks":len(cmap),"packets":len(pr),"min_games_per_deck":min(counts),"median_games_per_deck":statistics.median(counts),"max_games_per_deck":max(counts),"decks_with_zero_games":sum(x==0 for x in counts),"telemetry":tel,"top20_combos":[{"rank":i+1,"combo_id":r[0],"packet_a":r[1],"packet_b":r[2],"games":r[3],"wins":r[4],"win_rate":r[5],"ci95_low":r[6],"ci95_high":r[7],"bt_elo":r[8],"strength_of_schedule_elo":r[9]} for i,r in enumerate(cr[:20])],"top20_packets":[{"rank":i+1,"packet":r[0],"mean_combo_bt_elo":r[2],"median_combo_bt_elo":r[3],"sd":r[4]} for i,r in enumerate(pr[:20])]}
    (out/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8"); print(json.dumps(summary,indent=2))


def refine(args):
    root=Path(args.workdir).resolve(); root.mkdir(parents=True,exist_ok=True)
    packets=base.load_packets(root/"packets"); combos=base.all_combos(packets); byid={c["id"]:c for c in combos}
    top=[x.strip() for x in Path(args.top_ids).read_text(encoding="utf-8").splitlines() if x.strip()]
    forge=Path(args.forge_jar).resolve(); out=Path(args.output).resolve(); out.parent.mkdir(parents=True,exist_ok=True)
    allrows=[]; tel={"matches":0,"timeout_pairs":0,"clean_retries":0,"unresolved_timeout_pairs":0,"warning_lines":0,"svar_warning_cards":Counter()}
    for rep in range(args.repeats):
        with tempfile.TemporaryDirectory(prefix="j25refine_") as td:
            parsed,elapsed=run_group(forge,byid,top,Path(td),args.timeout)
        rows={canon_pair(a,b):(a,b,w) for a,b,w in parsed.rows}; tel["warning_lines"]+=parsed.warning_lines; tel["svar_warning_cards"].update(parsed.svar_warning_cards); tel["timeout_pairs"]+=len(parsed.timeout_pairs)
        for pair in sorted(parsed.timeout_pairs):
            row,clean,draws,warns,cards=clean_retry(forge,byid,pair,[args.retry_timeout,args.final_retry_timeout]); rows[pair]=row; tel["warning_lines"]+=warns; tel["svar_warning_cards"].update(cards); tel["clean_retries"]+=int(clean); tel["unresolved_timeout_pairs"]+=int(not clean)
        expected=len(top)*(len(top)-1)//2
        if len(rows)!=expected: raise RuntimeError(f"Refine {len(rows)} != {expected}")
        allrows.extend((rep,*r) for r in rows.values()); tel["matches"]+=expected
        print(f"REFINE repeat={rep+1}/{args.repeats} matches={expected} elapsed={elapsed:.1f}s")
    with out.open("w",newline="",encoding="utf-8") as f:
        w=csv.writer(f); w.writerow(RESULT_HEADER)
        for rep,a,b,win in allrows: w.writerow([999+rep,args.repeat_id,a,b,win,0,0,0])
    serial=dict(tel); serial["svar_warning_cards"]=dict(tel["svar_warning_cards"].most_common()); out.with_suffix(".telemetry.json").write_text(json.dumps(serial,indent=2),encoding="utf-8")


def main():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest="cmd",required=True)
    a=sub.add_parser("audit"); a.add_argument("--forge-res",required=True); a.add_argument("--workdir",default="work"); a.add_argument("--output",required=True); a.set_defaults(func=base.audit)
    s=sub.add_parser("shard"); s.add_argument("--forge-jar",required=True); s.add_argument("--workdir",default="work"); s.add_argument("--output",required=True); s.add_argument("--shard",type=int,required=True); s.add_argument("--shards",type=int,required=True); s.add_argument("--rounds",type=int,default=3); s.add_argument("--group-size",type=int,default=16); s.add_argument("--timeout",type=int,default=180); s.add_argument("--retry-timeout",type=int,default=600); s.add_argument("--final-retry-timeout",type=int,default=1200); s.add_argument("--seed",type=int,default=20260819); s.set_defaults(func=shard)
    a=sub.add_parser("aggregate"); a.add_argument("--inputs",required=True); a.add_argument("--combo-map",required=True); a.add_argument("--output",required=True); a.set_defaults(func=aggregate)
    r=sub.add_parser("refine"); r.add_argument("--forge-jar",required=True); r.add_argument("--workdir",default="work"); r.add_argument("--top-ids",required=True); r.add_argument("--output",required=True); r.add_argument("--repeats",type=int,default=1); r.add_argument("--repeat-id",type=int,default=0); r.add_argument("--timeout",type=int,default=300); r.add_argument("--retry-timeout",type=int,default=900); r.add_argument("--final-retry-timeout",type=int,default=1500); r.set_defaults(func=refine)
    args=p.parse_args(); args.func(args)

if __name__=="__main__": main()
