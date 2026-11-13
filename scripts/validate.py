#!/usr/bin/env python3
"""Validator for vector-brand-control-agent.

Checks that the documentation cannot drift from the tree: skill frontmatter,
referenced support files, prompt-set composition, sample markup contract,
metric names against the scorer, counts quoted in README and agent-description,
relative links (including ones Pages would break), and images.

Contract columns and metric names are not duplicated here: they are read from
scripts/geo_score.py, so a metric that exists only in the documentation fails.

Exit codes: 0 — clean, 1 — findings, 2 — bad input (required file missing).

Stdlib only, no network.
"""

import csv
import hashlib
import importlib.util
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = "vector-brand-control-agent"
SKILL_DIR = os.path.join(ROOT, "skills", SKILL)
TEMPLATES = os.path.join(SKILL_DIR, "templates")
SCORER_PATH = os.path.join(SKILL_DIR, "scripts", "geo_score.py")
ALLOWED_ROLES = {"none", "listed", "recommended", "top", "negative"}

_spec = importlib.util.spec_from_file_location("geo_score", SCORER_PATH)
if _spec is None or _spec.loader is None:
    sys.exit("cannot load the scorer: " + SCORER_PATH)
geo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(geo)

EXPECTED_MEASUREMENT_COLUMNS = list(geo.COLUMNS)
METRIC_NAMES = {geo.METRIC_NAMES.get(key, key) for key in geo.METRIC_KEYS}
DOC_ONLY_METRICS = {"loss_scenarios"}

findings = []
checks = 0


def ok(name, detail=""):
    global checks
    checks += 1
    print(f"OK   {name}" + (f" — {detail}" if detail else ""))


def fail(name, detail):
    global checks
    checks += 1
    findings.append(f"{name}: {detail}")
    print(f"FAIL {name} — {detail}")


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def strip_fences(text):
    return re.sub(r"```.*?```", "", text, flags=re.S)


# ---------------------------------------------------------------- frontmatter


def check_skill():
    path = os.path.join(SKILL_DIR, "SKILL.md")
    if not os.path.isfile(path):
        fail("skill/SKILL.md", "missing")
        return None
    text = read(path)
    if not text.startswith("---"):
        fail("skill/frontmatter", "file must start with '---'")
        return None
    match = re.search(r"\n---\s*\n", text[3:])
    if not match:
        fail("skill/frontmatter", "no closing '---'")
        return None
    front = text[3 : match.start() + 3]
    fields = {}
    for line in front.splitlines():
        if ":" in line and not line.startswith(" "):
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip().strip('"').strip("'")
    name = fields.get("name", "")
    description = fields.get("description", "")
    body = text[match.end() + 3 :].strip()
    if name != SKILL:
        fail("skill/name", f"frontmatter name '{name}' != directory '{SKILL}'")
    else:
        ok("skill/name", name)
    if not description:
        fail("skill/description", "empty")
    elif len(description) > 60:
        fail("skill/description", f"{len(description)} chars > 60")
    else:
        ok("skill/description", f"{len(description)} chars")
    if not body:
        fail("skill/body", "empty body after frontmatter")
    else:
        ok("skill/body", f"{len(body)} chars")
    return text


def check_skill_references(skill_text):
    if skill_text is None:
        return
    referenced = set()
    for pattern in (r"`((?:templates|scripts|references|assets)/[^`\s]+)`",):
        referenced.update(re.findall(pattern, skill_text))
    missing = sorted(item for item in referenced if not os.path.exists(os.path.join(SKILL_DIR, item)))
    if missing:
        fail("skill/references", "missing: " + ", ".join(missing))
    else:
        ok("skill/references", f"{len(referenced)} paths resolve")


# ------------------------------------------------------------------ prompt set


def check_prompts():
    path = os.path.join(TEMPLATES, "prompts.csv")
    rows = read_csv(path)
    if not rows:
        fail("prompts/rows", "empty")
        return None, None
    problems = []
    ids = []
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        if header[:5] != ["id", "lang", "scenario_class", "weight", "text"]:
            problems.append("header differs from id,lang,scenario_class,weight,text")
        for index, fields in enumerate(reader, start=2):
            if not fields:
                continue
            if len(fields) != 5:
                problems.append(f"line {index}: {len(fields)} fields (unquoted comma in text?)")
                continue
            ids.append(fields[0].strip())
    if len(set(ids)) != len(ids):
        problems.append("duplicate ids")
    classes = set()
    for index, row in enumerate(rows, start=2):
        scenario = (row.get("scenario_class") or "").strip()
        weight = (row.get("weight") or "").strip()
        text = (row.get("text") or "").strip()
        if not (row.get("id") or "").strip():
            problems.append(f"line {index}: empty id")
        if not scenario:
            problems.append(f"line {index}: empty scenario_class")
        else:
            classes.add(scenario)
        if not weight.isdigit() or not 1 <= int(weight) <= 3:
            problems.append(f"line {index}: weight '{weight}' not 1..3")
        if len(text) < 15:
            problems.append(f"line {index}: prompt text too short")
        if "{" not in text:
            problems.append(f"line {index}: prompt text has no placeholder")
    if problems:
        fail("prompts/contract", "; ".join(problems[:5]))
    else:
        ok("prompts/contract", f"{len(rows)} rows, {len(classes)} classes")
    return len(rows), len(classes)


def check_sample():
    path = os.path.join(TEMPLATES, "measurements-sample.csv")
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        rows = [row for row in reader if row]
    if header != EXPECTED_MEASUREMENT_COLUMNS:
        fail(
            "sample/columns",
            "header differs from the documented 14 columns: " + ",".join(header),
        )
    else:
        ok("sample/columns", "14 columns match the skill contract")
    bad = []
    engines = set()
    seen = set()
    for index, row in enumerate(rows, start=2):
        if len(row) != len(EXPECTED_MEASUREMENT_COLUMNS):
            bad.append(f"line {index}: {len(row)} fields")
            continue
        record = dict(zip(EXPECTED_MEASUREMENT_COLUMNS, row))
        engines.add(record["engine"])
        for key in ("brand_role", "competitor_role"):
            value = record[key].strip()
            if value and value not in ALLOWED_ROLES:
                bad.append(f"line {index}: {key} '{value}'")
        for key in ("web_search", "brand_mentioned", "brand_cited", "competitor_cited"):
            value = record[key].strip().lower()
            if value not in {"true", "false"}:
                bad.append(f"line {index}: {key} '{record[key]}' is not true/false")
        key = (record["date"], record["engine"], record["prompt_id"], record["repeat"])
        if key in seen:
            bad.append(
                f"line {index}: duplicate answer "
                f"{record['engine']}/{record['prompt_id']} repeat={record['repeat']}"
            )
        seen.add(key)
    if bad:
        fail("sample/contract", "; ".join(bad[:5]))
    else:
        ok("sample/contract", f"{len(rows)} answers, {len(engines)} engines")
    return len(engines)


# ----------------------------------------------------------------- counts/doc


def check_counts(prompts, classes, engines):
    description = read(os.path.join(ROOT, "agent-description.md"))
    table = {}
    for line in description.splitlines():
        match = re.match(r"\|\s*([^|]+?)\s*\|\s*(\d+)\s*\|", line)
        if match:
            table[match.group(1)] = int(match.group(2))
    expected = {
        "Промпты в наборе": prompts,
        "Классы сценариев": classes,
        "Движки в образце": engines,
        "Шаблоны": len([name for name in os.listdir(TEMPLATES) if os.path.isfile(os.path.join(TEMPLATES, name))]),
        "Скрипты замера": len(
            [name for name in os.listdir(os.path.join(SKILL_DIR, "scripts")) if name.endswith(".py")]
        ),
        "Метрики в сводке": len(geo.METRIC_KEYS),
    }
    problems = []
    for label, value in expected.items():
        if label not in table:
            problems.append(f"agent-description row missing: {label}")
        elif table[label] != value:
            problems.append(f"{label}: doc {table[label]}, tree {value}")
    if problems:
        fail("counts/agent-description", "; ".join(problems))
    else:
        ok("counts/agent-description", f"{len(expected)} rows match the tree")

    readme = read(os.path.join(ROOT, "README.md"))
    phrases = [
        f"промптов-{prompts}",
        f"классов_сценариев-{classes}",
        f"{prompts} запросов в {classes} классах",
        f"{engines} движка",
    ]
    missing = [phrase for phrase in phrases if phrase not in readme]
    if missing:
        fail("counts/readme", "missing or stale: " + "; ".join(missing))
    else:
        ok("counts/readme", f"{len(phrases)} phrases match the tree")


# ---------------------------------------------------------------------- links


def pages_excluded():
    """Каталоги и файлы, которые _config.yml исключает из сборки Pages:
    относительная ссылка в такой каталог на опубликованном сайте даёт 404."""
    config = read(os.path.join(ROOT, "_config.yml"))
    block = re.search(r"exclude:\s*\n((?:\s+-.*\n)+)", config)
    excluded = set()
    if block:
        for line in block.group(1).splitlines():
            value = line.strip().lstrip("-").strip().strip('"').strip("'")
            if value:
                excluded.add(value.rstrip("/"))
    return excluded


def check_links():
    problems = []
    broken_on_pages = []
    total = 0
    excluded = pages_excluded()
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [name for name in dirs if name not in {".git", "__pycache__"}]
        for name in files:
            if not name.endswith(".md"):
                continue
            path = os.path.join(base, name)
            text = strip_fences(read(path))
            for target in re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", text):
                target = target.strip()
                if target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                anchor_free = target.split("#", 1)[0]
                if not anchor_free:
                    continue
                total += 1
                resolved = os.path.normpath(os.path.join(base, anchor_free))
                if not os.path.exists(resolved):
                    problems.append(f"{os.path.relpath(path, ROOT)} -> {target}")
                    continue
                top = os.path.relpath(resolved, ROOT).split(os.sep)[0].rstrip("/")
                if top in excluded:
                    broken_on_pages.append(
                        f"{os.path.relpath(path, ROOT)} -> {target} "
                        f"(каталог '{top}' исключён из сборки Pages)"
                    )
    if problems:
        fail("links/relative", "; ".join(problems[:5]))
    else:
        ok("links/relative", f"{total} links resolve")
    if broken_on_pages:
        fail("links/pages", "; ".join(broken_on_pages[:5]))
    else:
        ok("links/pages", "no link points into an excluded directory")


def check_images():
    excluded = pages_excluded()
    problems = []
    for name in ("README.md", "agent-description.md", "NOTICE.md"):
        text = read(os.path.join(ROOT, name))
        sources = re.findall(r'<img[^>]+src="([^"]+)"', text)
        sources += [target.split()[0] for target in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text)]
        for src in sources:
            if src.startswith(("http://", "https://", "data:")):
                continue
            if not os.path.isfile(os.path.join(ROOT, src)):
                problems.append(f"{name}: missing file {src}")
                continue
            top = src.split("/")[0].rstrip("/")
            if top in excluded:
                problems.append(f"{name}: {src} lives in excluded dir '{top}'")
    if problems:
        fail("images/assets", "; ".join(problems[:5]))
    else:
        ok("images/assets", "images exist and are not excluded")


# -------------------------------------------------------------------- metrics


def check_metrics():
    """Таблица метрик в SKILL.md сверяется с кодом в ОБЕ стороны: метрика в
    документации без реализации (sentiment) и реализованная метрика без строки
    в документации — обе ошибки."""
    text = read(os.path.join(SKILL_DIR, "SKILL.md"))
    block = re.search(r"## Что именно измеряется\n(.*?)\n## ", text, flags=re.S)
    if not block:
        fail("metrics/skill", "section '## Что именно измеряется' not found")
        return
    documented = set()
    for line in block.group(1).splitlines():
        if not line.startswith("|"):
            continue
        name = re.search(r"`([a-z][a-z_]+)`", line)
        if name:
            documented.add(name.group(1))
    expected = METRIC_NAMES | DOC_ONLY_METRICS
    ghosts = sorted(documented - expected)
    missing = sorted(expected - documented)
    problems = []
    if ghosts:
        problems.append("в документации нет в коде: " + ", ".join(ghosts))
    if missing:
        problems.append("в коде есть, в документации нет: " + ", ".join(missing))
    if problems:
        fail("metrics/skill", "; ".join(problems))
    else:
        ok("metrics/skill", f"{len(expected)} metrics match the scorer")


def check_license_and_notice():
    for name in ("LICENSE", "NOTICE.md"):
        if not os.path.isfile(os.path.join(ROOT, name)):
            fail(f"files/{name}", "missing")
    notice = read(os.path.join(ROOT, "NOTICE.md"))
    digest = hashlib.sha256(open(os.path.join(ROOT, "assets/vector-logo.png"), "rb").read()).hexdigest()
    if digest not in notice:
        fail("notice/logo-hash", f"{digest} not quoted in NOTICE.md")
    else:
        ok("notice/logo-hash", digest[:12])


def check_json():
    path = os.path.join(TEMPLATES, "brand.json")
    try:
        data = json.loads(read(path))
    except json.JSONDecodeError as error:
        fail("brand.json", str(error))
        return
    required = {"brand", "competitors", "measurement"}
    missing = required - set(data)
    if missing:
        fail("brand.json", "missing keys: " + ", ".join(sorted(missing)))
    elif not data["competitors"]:
        fail("brand.json", "competitors list is empty")
    else:
        ok("brand.json", "keys and competitor list present")


def main():
    required = [
        "README.md",
        "agent-description.md",
        "NOTICE.md",
        "LICENSE",
        "_config.yml",
        os.path.join("skills", SKILL, "SKILL.md"),
    ]
    missing = [name for name in required if not os.path.isfile(os.path.join(ROOT, name))]
    if missing:
        print("FAIL input — missing: " + ", ".join(missing))
        return 2

    skill_text = check_skill()
    check_skill_references(skill_text)
    prompts, classes = check_prompts()
    engines = check_sample()
    if prompts is not None and classes is not None and engines is not None:
        check_counts(prompts, classes, engines)
    check_metrics()
    check_links()
    check_images()
    check_license_and_notice()
    check_json()

    print(f"\n{checks} checks, {len(findings)} findings")
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
