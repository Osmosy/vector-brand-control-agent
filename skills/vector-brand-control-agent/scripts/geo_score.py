#!/usr/bin/env python3
"""GEO scoring for vector-brand-control-agent.

Reads one row per model answer (measurements.csv) and prints a markdown block:
overall metrics, per-engine / per-scenario-class / per-channel tables, coverage
against the expected number of repeats, and the worst (weighted) loss scenarios.

Stdlib only. No network.

CSV contract (measurements.csv), header required, 14 columns:
  date,engine,prompt_id,scenario_class,weight,repeat,web_search,brand_mentioned,
  brand_role,brand_pos,competitor,competitor_role,brand_cited,competitor_cited

  date       — дата волны (YYYY-MM-DD). Волны не смешиваются: файл с несколькими
               датами получает предупреждение, --date оставляет одну волну.
  repeat     — номер повтора. Покрытие считает УНИКАЛЬНЫЕ повторы, а не строки.
  web_search — был ли в этом ответе поиск. Канал (поиск / память) — отдельный срез.

brand_role / competitor_role: none|listed|recommended|top|negative
Booleans: true/false, 1/0, yes/no, да/нет.

Провал (loss) по контракту: промпт, где конкурент рекомендован (recommended|top),
а бренд — нет. Конкурент, который лишь упомянут (listed), провалом не считается.

prompts.csv (optional, --prompts): id,lang,scenario_class,weight,text
  Сверяется с разметкой по prompt_id: класс и вес. Расхождение и неизвестный
  prompt_id печатаются предупреждением — иначе опечатка молча создаёт новый
  класс и меняет приоритет провала.
"""

import argparse
import csv
import sys
from collections import defaultdict

TRUE = {"1", "true", "t", "yes", "y", "да", "+"}
ROLES = {"none", "listed", "recommended", "top", "negative"}
REC = {"recommended", "top"}
NEGATIVE = "negative"

COLUMNS = [
    "date",
    "engine",
    "prompt_id",
    "scenario_class",
    "weight",
    "repeat",
    "web_search",
    "brand_mentioned",
    "brand_role",
    "brand_pos",
    "competitor",
    "competitor_role",
    "brand_cited",
    "competitor_cited",
]

# Метрики сводки. Список — источник правды для документации и валидатора:
# метрика, которой здесь нет, не имеет права стоять в таблице SKILL.md, и
# наоборот. METRIC_NAMES — как метрика называется в документации.
METRIC_KEYS = ("mention", "sov", "win", "cite", "comp_cite", "negative")
METRIC_NAMES = {
    "mention": "mention_rate",
    "sov": "share_of_voice",
    "win": "win_rate",
    "cite": "citation_share",
    "comp_cite": "competitor_citation_share",
    "negative": "negative_rate",
}


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
    return None if not whole else 100.0 * part / whole


def fmt(value, suffix="%"):
    return "n/a" if value is None else f"{value:.1f}{suffix}"


def load(path):
    with open(path, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        sys.exit(f"empty measurements file: {path}")
    missing = [name for name in COLUMNS if name not in rows[0]]
    if missing:
        sys.exit(
            "measurements.csv is missing columns: "
            + ", ".join(missing)
            + "\ncontract: "
            + ",".join(COLUMNS)
        )
    prepared = []
    for number, row in enumerate(rows, start=2):
        prepared.append(
            {
                "line": number,
                "date": (row.get("date") or "").strip(),
                "engine": (row.get("engine") or "?").strip(),
                "prompt_id": (row.get("prompt_id") or "?").strip(),
                "scenario_class": (row.get("scenario_class") or "?").strip(),
                "weight": num(row.get("weight"), 1.0),
                "repeat": (row.get("repeat") or "").strip(),
                "web_search": truthy(row.get("web_search")),
                "brand_mentioned": truthy(row.get("brand_mentioned")),
                "brand_role": role(row.get("brand_role")),
                "brand_pos": (row.get("brand_pos") or "").strip(),
                "competitor": (row.get("competitor") or "").strip(),
                "competitor_role": role(row.get("competitor_role")),
                "brand_cited": truthy(row.get("brand_cited")),
                "competitor_cited": truthy(row.get("competitor_cited")),
            }
        )
    return prepared


def sanity(rows):
    """Дешёвые проверки согласованности разметки. Противоречивая строка искажает
    метрику молча, поэтому каждая находка печатается с номером строки."""
    problems = []
    seen = {}
    for row in rows:
        key = (row["date"], row["engine"], row["prompt_id"], row["repeat"])
        if row["repeat"] and key in seen:
            problems.append(
                f"строка {row['line']}: повтор {row['repeat']} "
                f"{row['engine']}/{row['prompt_id']} уже размечен (строка {seen[key]})"
            )
        else:
            seen.setdefault(key, row["line"])
        if not row["brand_mentioned"] and row["brand_role"] in (REC | {"listed"}):
            problems.append(
                f"строка {row['line']}: brand_role={row['brand_role']} при "
                f"brand_mentioned=false — метрика считает бренд неупомянутым"
            )
        elif not row["brand_mentioned"] and row["brand_pos"]:
            problems.append(
                f"строка {row['line']}: brand_pos={row['brand_pos']} при brand_mentioned=false"
            )
        if row["brand_role"] == "none" and row["brand_pos"]:
            problems.append(
                f"строка {row['line']}: brand_pos={row['brand_pos']} при brand_role=none"
            )
        if row["competitor_role"] in REC and not row["competitor"]:
            problems.append(
                f"строка {row['line']}: competitor_role={row['competitor_role']} "
                f"без имени конкурента"
            )
    return problems


def read_prompts(path):
    prompts = {}
    with open(path, newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            prompt_id = (row.get("id") or "").strip()
            if not prompt_id:
                continue
            prompts[prompt_id] = {
                "scenario_class": (row.get("scenario_class") or "").strip(),
                "weight": num(row.get("weight"), 0.0),
                "text": (row.get("text") or "").strip(),
            }
    return prompts


def prompt_checks(rows, prompts):
    """Сверка разметки с набором промптов: класс и вес по prompt_id."""
    problems = []
    for row in rows:
        prompt = prompts.get(row["prompt_id"])
        if prompt is None:
            problems.append(f"строка {row['line']}: prompt_id '{row['prompt_id']}' нет в prompts.csv")
            continue
        if prompt["scenario_class"] and prompt["scenario_class"] != row["scenario_class"]:
            problems.append(
                f"строка {row['line']}: класс '{row['scenario_class']}' в разметке, "
                f"'{prompt['scenario_class']}' в prompts.csv ({row['prompt_id']})"
            )
        if prompt["weight"] and abs(prompt["weight"] - row["weight"]) > 1e-9:
            problems.append(
                f"строка {row['line']}: вес {row['weight']:g} в разметке, "
                f"{prompt['weight']:g} в prompts.csv ({row['prompt_id']})"
            )
    return problems


def metrics(rows):
    """Знаменатель — ответ. Роли считаются только у упомянутого бренда: строка
    brand_mentioned=false с ролью recommended противоречива (её называет
    sanity()), и молча завышать по ней рекомендации нельзя."""
    weight = sum(row["weight"] for row in rows) or 1.0
    mentioned = sum(row["weight"] for row in rows if row["brand_mentioned"])
    brand_rec = sum(
        row["weight"] for row in rows if row["brand_mentioned"] and row["brand_role"] in REC
    )
    comp_rec = sum(row["weight"] for row in rows if row["competitor_role"] in REC)
    wins = sum(
        row["weight"]
        for row in rows
        if row["brand_mentioned"]
        and (row["brand_role"] in REC or row["brand_pos"] in {"1", "1.0"})
    )
    cited = sum(row["weight"] for row in rows if row["brand_cited"])
    comp_cited = sum(row["weight"] for row in rows if row["competitor_cited"])
    negative = sum(row["weight"] for row in rows if row["brand_role"] == NEGATIVE)
    denom_sov = brand_rec + comp_rec
    return {
        "n": len(rows),
        "mention": pct(mentioned, weight),
        "sov": pct(brand_rec, denom_sov) if denom_sov else None,
        "win": pct(wins, weight),
        "cite": pct(cited, weight),
        "comp_cite": pct(comp_cited, weight),
        "negative": pct(negative, mentioned) if mentioned else None,
    }


def table(title, groups, order=None):
    keys = order or sorted(groups)
    print(f"\n### {title}\n")
    print("| Срез | n | mention | SoV | win | cite | цит.конк. | негатив |")
    print("|---|---|---|---|---|---|---|---|")
    for key in keys:
        m = metrics(groups[key])
        print(
            f"| {key} | {m['n']} | {fmt(m['mention'])} | {fmt(m['sov'])} | "
            f"{fmt(m['win'])} | {fmt(m['cite'])} | {fmt(m['comp_cite'])} | {fmt(m['negative'])} |"
        )


def coverage(rows, min_repeats, prompt_ids=None):
    """Покрытие по УНИКАЛЬНЫМ номерам повторов: три строки с repeat=1 — это один
    повтор, а не три (иначе дубль строки даёт ложное «Полное»)."""
    engines = sorted({row["engine"] for row in rows})
    seen = defaultdict(set)
    for row in rows:
        seen[(row["engine"], row["prompt_id"])].add(row["repeat"] or f"line{row['line']}")
    prompts = sorted(prompt_ids) if prompt_ids else sorted({row["prompt_id"] for row in rows})
    short = []
    for prompt_id in prompts:
        for engine in engines:
            got = len(seen.get((engine, prompt_id), ()))
            if got < min_repeats:
                short.append(((engine, prompt_id), got))
    return short


def loss_scenarios(rows, prompt_text, limit):
    """Провал = конкурент рекомендован, бренд — нет. Конкурент, который только
    упомянут (listed), провалом не считается: рекомендации нет ни у кого."""
    by_prompt = defaultdict(list)
    for row in rows:
        by_prompt[row["prompt_id"]].append(row)
    scored = []
    for prompt_id, group in by_prompt.items():
        weight = sum(row["weight"] for row in group) or 1.0
        lost = sum(
            row["weight"]
            for row in group
            if row["brand_role"] not in REC and row["competitor_role"] in REC
        )
        brand_rec = sum(row["weight"] for row in group if row["brand_role"] in REC)
        comp_cited = sum(row["weight"] for row in group if row["competitor_cited"])
        scored.append(
            {
                "id": prompt_id,
                "class": group[0]["scenario_class"],
                "weight": group[0]["weight"],
                "n": len(group),
                "loss": pct(lost, weight),
                "rec": pct(brand_rec, weight),
                "comp_cite": pct(comp_cited, weight),
                "score": group[0]["weight"] * lost / weight,
                "text": prompt_text.get(prompt_id, ""),
            }
        )
    scored.sort(key=lambda item: (-item["score"], -(item["loss"] or 0.0)))
    return [item for item in scored if item["score"] > 0][:limit]


def warn(lines, title, limit=10):
    if not lines:
        return
    print(f"\n> ⚠ {title}: {len(lines)}")
    for line in lines[:limit]:
        print(f"> - {line}")
    if len(lines) > limit:
        print(f"> - ... и ещё {len(lines) - limit}")


def main():
    parser = argparse.ArgumentParser(description="GEO scoring: SoV, win rate, citations, coverage")
    parser.add_argument("--measurements", required=True)
    parser.add_argument("--prompts", help="prompts.csv: id,lang,scenario_class,weight,text")
    parser.add_argument("--min-repeats", type=int, default=3)
    parser.add_argument("--min-n", type=int, default=30)
    parser.add_argument("--top", type=int, default=5, help="сколько провальных сценариев печатать")
    parser.add_argument(
        "--date",
        action="append",
        metavar="YYYY-MM-DD",
        help="оставить только эту волну (флаг можно повторять)",
    )
    args = parser.parse_args()

    rows = load(args.measurements)
    all_dates = {row["date"] for row in rows if row["date"]}
    if args.date:
        wanted = {value.strip() for value in args.date}
        unknown_dates = wanted - all_dates
        if unknown_dates:
            sys.exit("--date: no such wave in the file: " + ", ".join(sorted(unknown_dates)))
        rows = [row for row in rows if row["date"] in wanted]
        if not rows:
            sys.exit("--date: no rows for " + ", ".join(sorted(wanted)))

    prompts = read_prompts(args.prompts) if args.prompts else {}
    prompt_text = {key: value["text"] for key, value in prompts.items()}

    unknown = sorted(
        {row["brand_role"] for row in rows if row["brand_role"] not in ROLES}
        | {row["competitor_role"] for row in rows if row["competitor_role"] not in ROLES}
    )

    total = metrics(rows)
    print("## GEO сводка\n")
    print(f"**Ответов: {total['n']}**, SoV **{fmt(total['sov'])}**, "
          f"win **{fmt(total['win'])}**, mention **{fmt(total['mention'])}**, "
          f"cite **{fmt(total['cite'])}**, цит.конк. **{fmt(total['comp_cite'])}**, "
          f"негатив **{fmt(total['negative'])}**")
    dates_in_use = sorted({row["date"] for row in rows if row["date"]})
    print(f"\nПромптов: {len({row['prompt_id'] for row in rows})}, "
          f"движков: {len({row['engine'] for row in rows})}, "
          f"волн: {len(dates_in_use)}")

    if total["n"] < args.min_n:
        print(f"\n> ⚠ n={total['n']} < {args.min_n}: срез годится для гипотезы, "
              f"не для вердикта.")
    if unknown:
        print(f"\n> ⚠ неизвестные роли (считаются как 0): {', '.join(unknown)}")

    warn(sanity(rows), "противоречивая разметка")
    if prompts:
        warn(prompt_checks(rows, prompts), "расхождения с prompts.csv")

    if len(dates_in_use) > 1 and not args.date:
        warn(
            [
                f"файл содержит {len(dates_in_use)} волны ({', '.join(dates_in_use)}): "
                "сводка их смешивает — волны сравнивать по таблице ниже или через --date"
            ],
            "несколько волн в одном файле",
        )

    groups_engine = defaultdict(list)
    groups_class = defaultdict(list)
    groups_date = defaultdict(list)
    groups_channel = defaultdict(list)
    for row in rows:
        groups_engine[row["engine"]].append(row)
        groups_class[row["scenario_class"]].append(row)
        groups_date[row["date"] or "?"].append(row)
        groups_channel["поиск" if row["web_search"] else "память"].append(row)

    if len(groups_date) > 1:
        table("По волнам (дата замера)", groups_date)
    if len(groups_channel) > 1:
        table("По каналу (веб-поиск / память)", groups_channel)
    table("По движкам", groups_engine)
    table("По классам сценариев", groups_class)

    short = coverage(rows, args.min_repeats, sorted(prompt_text) if prompt_text else None)
    print(f"\n### Покрытие (минимум {args.min_repeats} повтора на промпт/движок)\n")
    if short:
        print(f"Недобор: {len(short)} комбинаций\n")
        for (engine, prompt_id), count in short[:15]:
            print(f"- {engine} / {prompt_id}: {count}")
        if len(short) > 15:
            print(f"- ... и ещё {len(short) - 15}")
    else:
        print("Полное.")

    losses = loss_scenarios(rows, prompt_text, args.top)
    print(f"\n### Провальные сценарии (вес × доля выпадения, топ {args.top})\n")
    if not losses:
        print("Нет: нет ни одного ответа, где конкурент рекомендован, а бренд — нет.")
    else:
        print("| prompt | класс | вес | n | выпадение | бренд рекомендован | цит.конк. | сценарий |")
        print("|---|---|---|---|---|---|---|---|")
        for item in losses:
            text = item["text"].replace("|", "/")[:80]
            print(
                f"| {item['id']} | {item['class']} | {item['weight']:g} | {item['n']} | "
                f"{fmt(item['loss'])} | {fmt(item['rec'])} | {fmt(item['comp_cite'])} | {text} |"
            )

    print("\n> Напоминание: сравнивать только замеры с одинаковым набором промптов и движков "
          "и в пределах одной волны (--date).")


if __name__ == "__main__":
    main()
