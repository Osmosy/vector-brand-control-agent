#!/usr/bin/env python3
"""GEO scoring for vector-brand-control-agent.

Reads one row per model answer (measurements.csv) and prints a markdown block:
overall metrics, per-engine and per-scenario-class tables, coverage against the
expected number of repeats, and the worst (weighted) loss scenarios.

Stdlib only. No network.

CSV contract (measurements.csv), header required:
  date,engine,prompt_id,scenario_class,weight,repeat,brand_mentioned,brand_role,
  brand_pos,competitor,competitor_role,competitor_pos,brand_cited,competitor_cited

brand_role / competitor_role: none|listed|recommended|top|negative
Booleans: true/false, 1/0, yes/no, \u0434\u0430/\u043d\u0435\u0442.

prompts.csv (optional, --prompts): id,lang,scenario_class,weight,text
"""

import argparse
import csv
import sys
from collections import defaultdict

TRUE = {"1", "true", "t", "yes", "y", "\u0434\u0430", "+"}
ROLES = {"none": 0.0, "listed": 1.0, "recommended": 2.0, "top": 3.0, "negative": 0.0}
REC = {"recommended", "top"}


def truthy(value):
    return (value or "").strip().lower() in TRUE


def role(value):
    value = (value or "").strip().lower()
    return value or "none"


def num(value, default=0.0):
    try:
        return float((value or "").strip().replace(",", "."))
    except ValueError:
        return default


def pct(part, whole):
    return 0.0 if not whole else 100.0 * part / whole


def fmt(value, suffix="%"):
    return "n/a" if value is None else f"{value:.1f}{suffix}"


def load(path):
    with open(path, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        sys.exit(f"empty measurements file: {path}")
    required = {
        "engine",
        "prompt_id",
        "brand_mentioned",
        "brand_role",
        "brand_cited",
        "competitor_role",
    }
    missing = required - set(rows[0])
    if missing:
        sys.exit("measurements.csv is missing columns: " + ", ".join(sorted(missing)))
    prepared = []
    for index, row in enumerate(rows, start=2):
        prepared.append(
            {
                "line": index,
                "engine": (row.get("engine") or "?").strip(),
                "prompt_id": (row.get("prompt_id") or "?").strip(),
                "scenario_class": (row.get("scenario_class") or "?" ).strip(),
                "weight": num(row.get("weight"), 1.0),
                "repeat": (row.get("repeat") or "").strip(),
                "brand_mentioned": truthy(row.get("brand_mentioned")),
                "brand_role": role(row.get("brand_role")),
                "brand_pos": row.get("brand_pos") or "",
                "competitor": (row.get("competitor") or "").strip(),
                "competitor_role": role(row.get("competitor_role")),
                "brand_cited": truthy(row.get("brand_cited")),
            }
        )
    return prepared


def metrics(rows):
    n = len(rows)
    weight = sum(row["weight"] for row in rows) or 1.0
    mentioned = sum(row["weight"] for row in rows if row["brand_mentioned"])
    brand_rec = sum(row["weight"] for row in rows if row["brand_role"] in REC)
    comp_rec = sum(row["weight"] for row in rows if row["competitor_role"] in REC)
    wins = sum(
        row["weight"]
        for row in rows
        if row["brand_role"] in REC or row["brand_pos"].strip() in {"1", "1.0"}
    )
    cited = sum(row["weight"] for row in rows if row["brand_cited"])
    denom_sov = brand_rec + comp_rec
    return {
        "n": n,
        "mention": pct(mentioned, weight),
        "sov": pct(brand_rec, denom_sov) if denom_sov else None,
        "win": pct(wins, weight),
        "cite": pct(cited, weight),
        "brand_rec": brand_rec,
        "comp_rec": comp_rec,
    }


def table(title, groups, order=None):
    keys = order or sorted(groups)
    print(f"\n### {title}\n")
    print("| \u0421\u0440\u0435\u0437 | n | mention | SoV | win | cite |")
    print("|---|---|---|---|---|---|")
    for key in keys:
        m = metrics(groups[key])
        print(
            f"| {key} | {m['n']} | {fmt(m['mention'])} | {fmt(m['sov'])} | "
            f"{fmt(m['win'])} | {fmt(m['cite'])} |"
        )


def coverage(rows, min_repeats, prompt_ids=None):
    engines = sorted({row["engine"] for row in rows})
    counts = defaultdict(int)
    for row in rows:
        counts[(row["engine"], row["prompt_id"])] += 1
    prompts = sorted(prompt_ids) if prompt_ids else sorted({row["prompt_id"] for row in rows})
    short = []
    for prompt_id in prompts:
        for engine in engines:
            got = counts.get((engine, prompt_id), 0)
            if got < min_repeats:
                short.append(((engine, prompt_id), got))
    return short


def loss_scenarios(rows, prompt_text, limit):
    by_prompt = defaultdict(list)
    for row in rows:
        by_prompt[row["prompt_id"]].append(row)
    scored = []
    for prompt_id, group in by_prompt.items():
        weight = sum(row["weight"] for row in group) or 1.0
        lost = sum(
            row["weight"]
            for row in group
            if row["brand_role"] not in REC
            and (row["competitor_role"] in REC or row["competitor_role"] == "listed")
        )
        brand_rec = sum(row["weight"] for row in group if row["brand_role"] in REC)
        share = lost / weight
        scored.append(
            {
                "id": prompt_id,
                "class": group[0]["scenario_class"],
                "weight": group[0]["weight"],
                "n": len(group),
                "loss": share,
                "rec": brand_rec / weight,
                "score": group[0]["weight"] * share,
                "text": prompt_text.get(prompt_id, ""),
            }
        )
    scored.sort(key=lambda item: (-item["score"], -item["loss"]))
    return [item for item in scored if item["loss"] > 0][:limit]


def main():
    parser = argparse.ArgumentParser(description="GEO scoring: SoV, win rate, citations, coverage")
    parser.add_argument("--measurements", required=True)
    parser.add_argument("--prompts", help="prompts.csv with id,text for scenario names")
    parser.add_argument("--min-repeats", type=int, default=3)
    parser.add_argument("--min-n", type=int, default=30)
    args = parser.parse_args()

    rows = load(args.measurements)
    prompt_text = {}
    if args.prompts:
        with open(args.prompts, newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                prompt_text[(row.get("id") or "").strip()] = (row.get("text") or "").strip()

    unknown = sorted(
        {
            row["brand_role"]
            for row in rows
            if row["brand_role"] not in ROLES
        }
        | {row["competitor_role"] for row in rows if row["competitor_role"] not in ROLES}
    )

    total = metrics(rows)
    print("## GEO \u0441\u0432\u043e\u0434\u043a\u0430\n")
    print(f"**\u041e\u0442\u0432\u0435\u0442\u043e\u0432: {total['n']}**, SoV **{fmt(total['sov'])}**, "
          f"win **{fmt(total['win'])}**, mention **{fmt(total['mention'])}**, "
          f"cite **{fmt(total['cite'])}**")
    print(f"\n\u041f\u0440\u043e\u043c\u043f\u0442\u043e\u0432: {len({row['prompt_id'] for row in rows})}, "
          f"\u0434\u0432\u0438\u0436\u043a\u043e\u0432: {len({row['engine'] for row in rows})}")

    if total["n"] < args.min_n:
        print(f"\n> \u26a0 n={total['n']} < {args.min_n}: \u0441\u0440\u0435\u0437 \u0433\u043e\u0434\u0438\u0442\u0441\u044f \u0434\u043b\u044f \u0433\u0438\u043f\u043e\u0442\u0435\u0437\u044b, "
              f"\u043d\u0435 \u0434\u043b\u044f \u0432\u0435\u0440\u0434\u0438\u043a\u0442\u0430.")
    if unknown:
        print(f"\n> \u26a0 \u043d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u044b\u0435 \u0440\u043e\u043b\u0438 (\u0441\u0447\u0438\u0442\u0430\u044e\u0442\u0441\u044f \u043a\u0430\u043a 0): {', '.join(unknown)}")

    groups_engine = defaultdict(list)
    groups_class = defaultdict(list)
    for row in rows:
        groups_engine[row["engine"]].append(row)
        groups_class[row["scenario_class"]].append(row)
    table("\u041f\u043e \u0434\u0432\u0438\u0436\u043a\u0430\u043c", groups_engine)
    table("\u041f\u043e \u043a\u043b\u0430\u0441\u0441\u0430\u043c \u0441\u0446\u0435\u043d\u0430\u0440\u0438\u0435\u0432", groups_class)

    short = coverage(rows, args.min_repeats, sorted(prompt_text) if prompt_text else None)
    print(f"\n### \u041f\u043e\u043a\u0440\u044b\u0442\u0438\u0435 (\u043c\u0438\u043d\u0438\u043c\u0443\u043c {args.min_repeats} \u043f\u043e\u0432\u0442\u043e\u0440\u0430 \u043d\u0430 \u043f\u0440\u043e\u043c\u043f\u0442/\u0434\u0432\u0438\u0436\u043e\u043a)\n")
    if short:
        print(f"Недобор: {len(short)} комбинаций\n")
        for (engine, prompt_id), count in short[:15]:
            print(f"- {engine} / {prompt_id}: {count}")
        if len(short) > 15:
            print(f"- ... и ещё {len(short) - 15}")
    else:
        print("\u041f\u043e\u043b\u043d\u043e\u0435.")

    losses = loss_scenarios(rows, prompt_text, 5)
    print("\n### \u041f\u0440\u043e\u0432\u0430\u043b\u044c\u043d\u044b\u0435 \u0441\u0446\u0435\u043d\u0430\u0440\u0438\u0438 (\u0432\u0435\u0441 \u00d7 \u0434\u043e\u043b\u044f \u0432\u044b\u043f\u0430\u0434\u0435\u043d\u0438\u044f)\n")
    if not losses:
        print("\u041d\u0435\u0442: \u0432\u0435\u0437\u0434\u0435, \u0433\u0434\u0435 \u0435\u0441\u0442\u044c \u043a\u043e\u043d\u043a\u0443\u0440\u0435\u043d\u0442, \u0431\u0440\u0435\u043d\u0434 \u0442\u043e\u0436\u0435 \u0440\u0435\u043a\u043e\u043c\u0435\u043d\u0434\u043e\u0432\u0430\u043d.")
    else:
        print("| prompt | \u043a\u043b\u0430\u0441\u0441 | \u0432\u0435\u0441 | n | \u0432\u044b\u043f\u0430\u0434\u0435\u043d\u0438\u0435 | \u0431\u0440\u0435\u043d\u0434 \u0440\u0435\u043a\u043e\u043c\u0435\u043d\u0434\u043e\u0432\u0430\u043d | \u0441\u0446\u0435\u043d\u0430\u0440\u0438\u0439 |")
        print("|---|---|---|---|---|---|---|")
        for item in losses:
            text = item["text"].replace("|", "/")[:80]
            print(
                f"| {item['id']} | {item['class']} | {item['weight']:g} | {item['n']} | "
                f"{fmt(pct(item['loss'], 1))} | {fmt(pct(item['rec'], 1))} | {text} |"
            )

    print("\n> \u041d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u0435: \u0441\u0440\u0430\u0432\u043d\u0438\u0432\u0430\u0442\u044c \u0442\u043e\u043b\u044c\u043a\u043e \u0437\u0430\u043c\u0435\u0440\u044b \u0441 \u043e\u0434\u0438\u043d\u0430\u043a\u043e\u0432\u044b\u043c \u043d\u0430\u0431\u043e\u0440\u043e\u043c \u043f\u0440\u043e\u043c\u043f\u0442\u043e\u0432 \u0438 \u0434\u0432\u0438\u0436\u043a\u043e\u0432.")


if __name__ == "__main__":
    main()
