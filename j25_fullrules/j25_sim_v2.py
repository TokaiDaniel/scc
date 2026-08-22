#!/usr/bin/env python3
from __future__ import annotations

import argparse, csv, json, math, os, random, re, shutil, subprocess, tempfile, urllib.parse, urllib.request
from collections import Counter, defaultdict
from pathlib import Path

PACKET_SOURCE_SHA = "f9b54681351dde7411494b4b92400f21e6edc814"
PACKET_API = f"https://api.github.com/repos/taw/magic-preconstructed-decks/contents/data/jumpstart/j25?ref={PACKET_SOURCE_SHA}"
RAW_BASE = f"https://raw.githubusercontent.com/taw/magic-preconstructed-decks/{PACKET_SOURCE_SHA}/data/jumpstart/j25/"
EXPECTED_PACKETS = 121
EXPECTED_THEMES = 46
EXPECTED_VARIANT_COUNTS = Counter({1: 11, 2: 15, 4: 20})
EXPECTED_COMBOS = 7381


def get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "j25-fullrules-actions/2.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def get_text(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "j25-fullrules-actions/2.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8")


def parse_packet(name: str, text: str):
    cards = Counter(); sideboard = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("//"): continue
        if line.lower() == "sideboard": sideboard = True; continue
        if sideboard: continue
        line = re.sub(r"\s+\[[^\]]+\]\s*$", "", line)
        m = re.match(r"^(\d+)\s+(.+?)\s*$", line)
        if not m: raise RuntimeError(f"Cannot parse {name}: {raw}")
        cards[m.group(2)] += int(m.group(1))
    if sum(cards.values()) != 20:
        raise RuntimeError(f"{name}: expected 20 cards, got {sum(cards.values())}")
    return cards


def theme_name(packet_stem: str):
    m = re.match(r"^(.*) \((\d+)\)$", packet_stem)
    if m:
        return m.group(1), int(m.group(2))
    # J25's 11 one-variant mythic themes are stored without a '(1)' suffix.
    # Treat an unsuffixed packet as the sole variant; the global 11/15/20
    # structure check below still catches any unexpected filename pattern.
    return packet_stem, 1


def validate_packet_structure(names):
    themes = defaultdict(list)
    for stem in names:
        theme, idx = theme_name(stem)
        themes[theme].append(idx)
    if len(themes) != EXPECTED_THEMES:
        raise RuntimeError(f"Expected {EXPECTED_THEMES} themes, got {len(themes)}")
    variant_hist = Counter()
    for theme, idxs in themes.items():
        idxs = sorted(idxs)
        expected = list(range(1, len(idxs) + 1))
        if idxs != expected:
            raise RuntimeError(f"Theme {theme} variants are {idxs}, expected {expected}")
        variant_hist[len(idxs)] += 1
    if variant_hist != EXPECTED_VARIANT_COUNTS:
        raise RuntimeError(f"Variant structure mismatch: {dict(variant_hist)} expected {dict(EXPECTED_VARIANT_COUNTS)}")
    return themes


def load_packets(cache: Path):
    cache.mkdir(parents=True, exist_ok=True)
    manifest = get_json(PACKET_API)
    files = sorted(x["name"] for x in manifest if x.get("type") == "file" and x["name"].endswith(".txt"))
    if len(files) != EXPECTED_PACKETS:
        raise RuntimeError(f"Expected {EXPECTED_PACKETS} packets, got {len(files)}")
    stems = [Path(x).stem for x in files]
    validate_packet_structure(stems)
    packets = {}
    for fn in files:
        p = cache / fn
        if not p.exists(): p.write_text(get_text(RAW_BASE + urllib.parse.quote(fn)), encoding="utf-8")
        stem = Path(fn).stem
        packets[stem] = parse_packet(stem, p.read_text(encoding="utf-8"))
    return packets


def all_combos(packets):
    names = sorted(packets); out = []; idx = 0
    for i, a in enumerate(names):
        for j in range(i, len(names)):
            b = names[j]; cards = packets[a] + packets[b]
            if sum(cards.values()) != 40: raise RuntimeError("Combo is not 40 cards")
            out.append({"id": f"C{idx:04d}", "a": a, "b": b, "cards": cards}); idx += 1
    if len(out) != EXPECTED_COMBOS:
        raise RuntimeError(f"Expected {EXPECTED_COMBOS} combos, got {len(out)}")
    return out


def scan_forge_card_names(cardsfolder: Path):
    names = set()
    for p in cardsfolder.rglob("*.txt"):
        try:
            with p.open(encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.startswith("Name:"):
                        names.add(line[5:].strip()); break
        except OSError:
            pass
    return names


def audit(args):
    root = Path(args.workdir).resolve(); root.mkdir(parents=True, exist_ok=True)
    packets = load_packets(root / "packets")
    combos = all_combos(packets)
    all_cards = sorted({card for cards in packets.values() for card in cards})
    cardsfolder = Path(args.forge_res).resolve() / "cardsfolder"
    if not cardsfolder.is_dir(): raise SystemExit(f"Forge cardsfolder missing: {cardsfolder}")
    forge_names = scan_forge_card_names(cardsfolder)
    missing = [c for c in all_cards if c not in forge_names]
    themes = defaultdict(int)
    for p in packets: themes[theme_name(p)[0]] += 1
    result = {
        "packet_source_sha": PACKET_SOURCE_SHA,
        "packets": len(packets), "themes": len(themes), "combos": len(combos),
        "unique_cards": len(all_cards), "forge_script_names": len(forge_names),
        "variant_histogram": dict(sorted(Counter(themes.values()).items())),
        "missing_cards": missing,
    }
    out = Path(args.output).resolve(); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    if missing: raise SystemExit(f"{len(missing)} packet cards missing from Forge scripts")


def write_deck(path: Path, combo):
    lines = ["[metadata]", f"Name={combo['id']}", "[main]"]
    for card, qty in sorted(combo["cards"].items()): lines.append(f"{qty} {card}")
    lines += ["[sideboard]", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def write_combo_map(path: Path, combos):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["combo_id","packet_a","packet_b"])
        for c in combos: w.writerow([c["id"], c["a"], c["b"]])


def parse_tournament(text: str):
    rows=[]; current=None
    pair_re=re.compile(r"^Round\s+\d+\s+-\s+(C\d+)\[\d+\]\s+vs\s+(C\d+)\[\d+\]")
    win_re=re.compile(r"^Match Winner\s+-\s+(C\d+)!", re.I)
    for raw in text.splitlines():
        line=raw.strip(); m=pair_re.search(line)
        if m: current=(m.group(1),m.group(2)); continue
        m=win_re.search(line)
        if m and current:
            winner=m.group(1); a,b=current
            if winner not in (a,b): raise RuntimeError(f"Winner {winner} not in pairing {current}")
            rows.append((a,b,winner)); current=None
    return rows


def run_group(forge_jar: Path, combos_by_id, ids, work: Path, match_size: int, timeout: int):
    group_dir=work/"group"
    if group_dir.exists(): shutil.rmtree(group_dir)
    group_dir.mkdir(parents=True)
    for cid in ids: write_deck(group_dir/f"{cid}.dck", combos_by_id[cid])
    cmd=[]
    if os.environ.get("J25_XVFB") == "1": cmd += ["xvfb-run","-a"]
    cmd += ["java","-Xms256m","-Xmx3g","-jar",str(forge_jar.resolve()),"sim","-D",str(group_dir.resolve()),"-t","RoundRobin","-m",str(match_size),"-q","-c",str(timeout)]
    p=subprocess.run(cmd,cwd=str(forge_jar.parent),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    text=p.stdout
    bad_markers=["Could not load deck","No deck found","Stopping slow match as draw","Exceeded number of exceptions","Game threw exception","Exception in thread","StackOverflowError"]
    bad=[x for x in bad_markers if x in text]
    if p.returncode != 0: raise RuntimeError(f"Forge exited {p.returncode}:\n{text[-12000:]}")
    if bad: raise RuntimeError(f"Forge anomaly markers {bad}:\n{text[-12000:]}")
    rows=parse_tournament(text); expected=len(ids)*(len(ids)-1)//2
    if len(rows)!=expected: raise RuntimeError(f"Parsed {len(rows)}/{expected} matches. Tail:\n{text[-12000:]}")
    return rows


def shard(args):
    root=Path(args.workdir).resolve(); root.mkdir(parents=True,exist_ok=True)
    packets=load_packets(root/"packets"); combos=all_combos(packets); byid={c["id"]:c for c in combos}
    write_combo_map(root/"combo_map.csv",combos)
    forge_jar=Path(args.forge_jar).resolve()
    if not forge_jar.exists(): raise SystemExit(f"Forge jar missing: {forge_jar}")
    out=Path(args.output).resolve(); out.parent.mkdir(parents=True,exist_ok=True)
    done=set()
    if out.exists():
        with out.open(encoding="utf-8") as f:
            for r in csv.DictReader(f): done.add((int(r["sample_round"]),int(r["group_no"])))
    new=not out.exists()
    with out.open("a",newline="",encoding="utf-8") as fout:
        w=csv.writer(fout)
        if new: w.writerow(["sample_round","group_no","deck_a","deck_b","winner"])
        global_group=0; completed=0
        for round_no in range(args.rounds):
            ids=[c["id"] for c in combos]; random.Random(args.seed+round_no).shuffle(ids)
            groups=[ids[i:i+args.group_size] for i in range(0,len(ids),args.group_size)]
            for group_no,group in enumerate(groups):
                if len(group)<2: global_group+=1; continue
                assigned=(global_group%args.shards)==args.shard; global_group+=1
                if not assigned or (round_no,group_no) in done: continue
                with tempfile.TemporaryDirectory(prefix="j25grp_") as td:
                    rows=run_group(forge_jar,byid,group,Path(td),args.match_size,args.timeout)
                for a,b,winner in rows: w.writerow([round_no,group_no,a,b,winner])
                fout.flush(); completed+=1
                print(f"Shard {args.shard}: round {round_no+1}/{args.rounds}, group {group_no+1}/{len(groups)}, matches={len(rows)}")
    print(f"Shard {args.shard} complete: {completed} groups newly run")


def load_all_results(inputs: Path):
    rows=[]
    for p in sorted(inputs.rglob("*.csv")):
        if p.name.startswith("combo_map"): continue
        try:
            with p.open(encoding="utf-8") as f:
                rd=csv.DictReader(f)
                if not rd.fieldnames or "winner" not in rd.fieldnames: continue
                for r in rd:
                    a,b,w=r.get("deck_a"),r.get("deck_b"),r.get("winner")
                    if not a or not b or w not in (a,b): raise RuntimeError(f"Malformed result row in {p}: {r}")
                    rows.append((a,b,w))
        except UnicodeDecodeError: continue
    return rows


def fit_elo(ids,rows,iters=100):
    rating={x:1500.0 for x in ids}; games=defaultdict(int); wins=defaultdict(float)
    for a,b,w in rows: games[a]+=1; games[b]+=1; wins[w]+=1
    for _ in range(iters):
        grad=defaultdict(float); weight=defaultdict(float)
        for a,b,w in rows:
            ea=1/(1+10**((rating[b]-rating[a])/400)); ya=1.0 if w==a else 0.0; g=ya-ea
            grad[a]+=g; grad[b]-=g; weight[a]+=1; weight[b]+=1
        nr={}; maxmove=0
        for x in ids:
            move=max(-18,min(18,28*grad[x]/max(1,math.sqrt(weight[x])))); nr[x]=rating[x]+move; maxmove=max(maxmove,abs(move))
        mean=sum(nr.values())/len(nr); rating={k:v-mean+1500 for k,v in nr.items()}
        if maxmove<0.005: break
    return rating,games,wins


def read_combo_map(path: Path):
    out={}
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f): out[r["combo_id"]]=(r["packet_a"],r["packet_b"])
    if len(out)!=EXPECTED_COMBOS: raise RuntimeError(f"Combo map has {len(out)} rows, expected {EXPECTED_COMBOS}")
    return out


def aggregate(args):
    combo_map=read_combo_map(Path(args.combo_map).resolve()); rows=load_all_results(Path(args.inputs).resolve())
    if not rows: raise SystemExit("No result rows found")
    unknown=sorted({x for a,b,w in rows for x in (a,b,w) if x not in combo_map})
    if unknown: raise RuntimeError(f"Unknown combo ids in results: {unknown[:20]}")
    rating,games,wins=fit_elo(sorted(combo_map),rows)
    outdir=Path(args.output).resolve(); outdir.mkdir(parents=True,exist_ok=True)
    combo_rows=[]
    for cid,(pa,pb) in combo_map.items():
        n=games[cid]; wr=wins[cid]/n if n else 0.0; combo_rows.append((cid,pa,pb,n,wr,rating[cid]))
    combo_rows.sort(key=lambda x:x[5],reverse=True)
    with (outdir/"combo_ranking.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.writer(f); w.writerow(["rank","combo_id","packet_a","packet_b","games","win_rate","elo"])
        for i,r in enumerate(combo_rows,1): w.writerow([i,*r])
    packet_vals=defaultdict(list)
    for cid,pa,pb,n,wr,elo in combo_rows:
        packet_vals[pa].append(elo)
        if pb!=pa: packet_vals[pb].append(elo)
    packet_rows=[]
    for p,vals in packet_vals.items():
        mean=sum(vals)/len(vals); sd=math.sqrt(sum((x-mean)**2 for x in vals)/max(1,len(vals)-1)); packet_rows.append((p,len(vals),mean,sd))
    packet_rows.sort(key=lambda x:x[2],reverse=True)
    with (outdir/"packet_ranking.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.writer(f); w.writerow(["rank","packet","combo_count","mean_combo_elo","combo_elo_sd"])
        for i,r in enumerate(packet_rows,1): w.writerow([i,*r])
    with (outdir/"top64.txt").open("w",encoding="utf-8") as f:
        for r in combo_rows[:64]: f.write(r[0]+"\n")
    game_counts=[r[3] for r in combo_rows]
    summary={"packet_source_sha":PACKET_SOURCE_SHA,"match_results":len(rows),"decks":len(combo_map),"packets":len(packet_rows),"min_games_per_deck":min(game_counts),"median_games_per_deck":sorted(game_counts)[len(game_counts)//2],"max_games_per_deck":max(game_counts),"top20_combos":[{"rank":i+1,"combo_id":r[0],"packet_a":r[1],"packet_b":r[2],"games":r[3],"win_rate":r[4],"elo":r[5]} for i,r in enumerate(combo_rows[:20])],"top20_packets":[{"rank":i+1,"packet":r[0],"mean_combo_elo":r[2],"sd":r[3]} for i,r in enumerate(packet_rows[:20])]}
    (outdir/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8"); print(json.dumps(summary,indent=2))


def refine(args):
    root=Path(args.workdir).resolve(); packets=load_packets(root/"packets"); combos=all_combos(packets); byid={c["id"]:c for c in combos}
    top=[x.strip() for x in Path(args.top_ids).read_text(encoding="utf-8").splitlines() if x.strip()]
    if len(top)<2: raise SystemExit("Need at least two top deck ids")
    if any(x not in byid for x in top): raise RuntimeError("Unknown top deck id")
    forge_jar=Path(args.forge_jar).resolve(); all_rows=[]
    for rep in range(args.repeats):
        with tempfile.TemporaryDirectory(prefix="j25refine_") as td:
            all_rows.extend(run_group(forge_jar,byid,top,Path(td),1,args.timeout))
        print(f"Refinement repeat {rep+1}/{args.repeats}: {len(top)*(len(top)-1)//2} matches")
    out=Path(args.output).resolve(); out.parent.mkdir(parents=True,exist_ok=True)
    with out.open("w",newline="",encoding="utf-8") as f:
        w=csv.writer(f); w.writerow(["sample_round","group_no","deck_a","deck_b","winner"])
        per=len(top)*(len(top)-1)//2
        for i,(a,b,winner) in enumerate(all_rows): w.writerow([999+i//per,args.repeat_id,a,b,winner])
    print(f"Refinement complete: {len(all_rows)} BO1 matches")


def main():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest="cmd",required=True)
    s=sub.add_parser("audit"); s.add_argument("--forge-res",required=True); s.add_argument("--workdir",default="work"); s.add_argument("--output",required=True); s.set_defaults(func=audit)
    s=sub.add_parser("shard"); s.add_argument("--forge-jar",required=True); s.add_argument("--workdir",default="work"); s.add_argument("--output",required=True); s.add_argument("--shard",type=int,required=True); s.add_argument("--shards",type=int,required=True); s.add_argument("--rounds",type=int,default=3); s.add_argument("--group-size",type=int,default=16); s.add_argument("--match-size",type=int,default=1); s.add_argument("--timeout",type=int,default=180); s.add_argument("--seed",type=int,default=20260819); s.set_defaults(func=shard)
    a=sub.add_parser("aggregate"); a.add_argument("--inputs",required=True); a.add_argument("--combo-map",required=True); a.add_argument("--output",required=True); a.set_defaults(func=aggregate)
    r=sub.add_parser("refine"); r.add_argument("--forge-jar",required=True); r.add_argument("--workdir",default="work"); r.add_argument("--top-ids",required=True); r.add_argument("--output",required=True); r.add_argument("--repeats",type=int,default=1); r.add_argument("--repeat-id",type=int,default=0); r.add_argument("--timeout",type=int,default=240); r.set_defaults(func=refine)
    args=p.parse_args(); args.func(args)

if __name__=="__main__": main()
