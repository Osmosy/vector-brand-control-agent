"""Tests for the GEO scorer. Stdlib only: python3 -m unittest discover -s tests -q."""

import csv
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCORER = os.path.join(ROOT, "skills", "vector-brand-control-agent", "scripts", "geo_score.py")
SAMPLE = os.path.join(ROOT, "skills", "vector-brand-control-agent", "templates", "measurements-sample.csv")
PROMPTS = os.path.join(ROOT, "skills", "vector-brand-control-agent", "templates", "prompts.csv")

spec = importlib.util.spec_from_file_location("geo_score", SCORER)
geo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(geo)

HEADER = [
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


def write_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        writer.writerows(rows)
    return path


def row(engine="gaim", prompt="p01", cls="category", weight="1", repeat="1",
        mentioned="true", role="recommended", pos="1", comp="K", comp_role="listed",
        cited="true", comp_cited="true", web="true", date="2026-11-11"):
    return [
        date, engine, prompt, cls, weight, repeat, web, mentioned, role, pos,
        comp, comp_role, cited, comp_cited,
    ]


def run_cli(path, *extra):
    return subprocess.run(
        [sys.executable, SCORER, "--measurements", path, *extra],
        capture_output=True,
        text=True,
    )


def sanity_messages(rows):
    return [text for _, text in geo.sanity(rows)]


class MetricsTest(unittest.TestCase):
    def test_share_of_voice_counts_only_recommendations(self):
        rows = [row(role="recommended", comp_role="listed") for _ in range(2)]
        rows += [row(role="listed", comp_role="recommended") for _ in range(6)]
        metrics = geo.metrics(geo.load(write_csv(rows, self.path)))
        self.assertAlmostEqual(metrics["sov"], 25.0, places=6)

    def test_share_of_voice_is_na_without_any_recommendation(self):
        rows = [row(role="listed", comp_role="listed")]
        metrics = geo.metrics(geo.load(write_csv(rows, self.path)))
        self.assertIsNone(metrics["sov"])

    def test_win_rate_needs_rank_or_top_role(self):
        rows = [
            row(role="top", pos="2"),          # top role wins
            row(role="listed", pos="1"),       # first position wins
            row(role="listed", pos="3"),       # neither
            row(role="none", mentioned="false", pos=""),
        ]
        metrics = geo.metrics(geo.load(write_csv(rows, self.path)))
        self.assertAlmostEqual(metrics["win"], 50.0, places=6)
        self.assertAlmostEqual(metrics["mention"], 75.0, places=6)

    def test_weights_shift_the_denominator(self):
        rows = [row(weight="3", role="recommended", comp_role="listed")]
        rows += [row(weight="1", role="listed", comp_role="recommended") for _ in range(3)]
        metrics = geo.metrics(geo.load(write_csv(rows, self.path)))
        self.assertAlmostEqual(metrics["sov"], 50.0, places=6)

    def test_win_is_not_counted_without_a_mention(self):
        """Строка brand_mentioned=false не может выигрывать промпт: иначе
        противоречивая разметка даёт mention 0% при win 100%."""
        rows = [row(mentioned="false", role="none", pos="1", comp_role="listed")]
        metrics = geo.metrics(geo.load(write_csv(rows, self.path)))
        self.assertAlmostEqual(metrics["mention"], 0.0, places=6)
        self.assertAlmostEqual(metrics["win"], 0.0, places=6)

    def test_negative_rate_uses_mentions_as_denominator(self):
        rows = [
            row(role="listed", pos="2"),
            row(role="negative", pos=""),
            row(mentioned="false", role="none", pos="", comp_role="none"),
        ]
        metrics = geo.metrics(geo.load(write_csv(rows, self.path)))
        self.assertAlmostEqual(metrics["negative"], 50.0, places=6)

    def test_competitor_citation_is_measured(self):
        rows = [row(comp_cited="true"), row(comp_cited="false")]
        metrics = geo.metrics(geo.load(write_csv(rows, self.path)))
        self.assertAlmostEqual(metrics["comp_cite"], 50.0, places=6)

    def test_every_metric_key_is_reported_by_metrics(self):
        metrics = geo.metrics(geo.load(write_csv([row()], self.path)))
        for key in geo.METRIC_KEYS:
            self.assertIn(key, metrics)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "m.csv")

    def tearDown(self):
        self.tmp.cleanup()


class ContractTest(unittest.TestCase):
    def test_missing_column_is_a_hard_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "m.csv"), "w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["engine", "prompt_id"])  # урезанная шапка
                writer.writerow(["gaim", "p01"])
            result = run_cli(os.path.join(tmp, "m.csv"))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing columns", result.stderr)
            self.assertIn("web_search", result.stderr)

    def test_duplicate_repeat_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_csv([row(), row(), row()], os.path.join(tmp, "m.csv"))
            problems = sanity_messages(geo.load(path))
            self.assertTrue(any("уже размечен" in item for item in problems), problems)

    def test_duplicate_and_contradiction_are_different_kinds(self):
        """Дубль чинится перезамером, противоречие — правкой разметки: в отчёте
        это разные блоки, значит и вид находки должен различаться."""
        with tempfile.TemporaryDirectory() as tmp:
            path = write_csv(
                [row(), row(), row(mentioned="false", role="recommended")],
                os.path.join(tmp, "m.csv"),
            )
            kinds = {kind for kind, _ in geo.sanity(geo.load(path))}
            self.assertEqual(kinds, {"дубль", "противоречие"})

    def test_cli_prints_duplicates_apart_from_contradictions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_csv(
                [row(), row(), row(mentioned="false", role="listed", pos="2")],
                os.path.join(tmp, "m.csv"),
            )
            result = run_cli(path, "--min-n", "1")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("дубли ответов", result.stdout)
            self.assertIn("противоречивая разметка", result.stdout)

    def test_contradictory_role_without_mention_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_csv(
                [row(mentioned="false", role="recommended", pos="1")],
                os.path.join(tmp, "m.csv"),
            )
            problems = sanity_messages(geo.load(path))
            self.assertTrue(any("brand_mentioned=false" in item for item in problems), problems)

    def test_position_without_role_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_csv(
                [row(mentioned="false", role="none", pos="2")],
                os.path.join(tmp, "m.csv"),
            )
            problems = sanity_messages(geo.load(path))
            self.assertTrue(any("brand_role=none" in item for item in problems), problems)

    def test_recommended_competitor_without_name_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_csv([row(comp="", comp_role="recommended")], os.path.join(tmp, "m.csv"))
            problems = sanity_messages(geo.load(path))
            self.assertTrue(any("без имени конкурента" in item for item in problems), problems)

    def test_clean_sample_has_no_sanity_findings(self):
        self.assertEqual(geo.sanity(geo.load(SAMPLE)), [])

    def test_prompt_class_and_weight_drift_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            prompts = os.path.join(tmp, "prompts.csv")
            with open(prompts, "w", newline="", encoding="utf-8") as handle:
                handle.write("id,lang,scenario_class,weight,text\n")
                handle.write("p01,ru,category,3,какой сервис выбрать в 2026 году\n")
            path = write_csv([row(cls="kategory", weight="1")], os.path.join(tmp, "m.csv"))
            problems = geo.prompt_checks(geo.load(path), geo.read_prompts(prompts))
            self.assertTrue(any("класс" in item for item in problems), problems)
            self.assertTrue(any("вес" in item for item in problems), problems)

    def test_unknown_prompt_id_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            prompts = os.path.join(tmp, "prompts.csv")
            with open(prompts, "w", newline="", encoding="utf-8") as handle:
                handle.write("id,lang,scenario_class,weight,text\n")
                handle.write("p99,ru,category,3,другой промпт набора\n")
            path = write_csv([row(prompt="ghost")], os.path.join(tmp, "m.csv"))
            problems = geo.prompt_checks(geo.load(path), geo.read_prompts(prompts))
            self.assertTrue(any("нет в prompts.csv" in item for item in problems), problems)


class CoverageTest(unittest.TestCase):
    def test_missing_pair_and_short_repeats_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_csv(
                [
                    row(engine="gaim", prompt="p01", repeat="1"),
                    row(engine="gaim", prompt="p01", repeat="2"),
                    row(engine="other", prompt="p01", repeat="1"),
                ],
                os.path.join(tmp, "m.csv"),
            )
            rows = geo.load(path)
            short = dict(geo.coverage(rows, 3, ["p01", "p02"]))
            self.assertEqual(short[("gaim", "p01")], 2)
            self.assertEqual(short[("other", "p01")], 1)
            self.assertEqual(short[("gaim", "p02")], 0)
            self.assertEqual(short[("other", "p02")], 0)

    def test_full_coverage_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_csv([row(repeat="1"), row(repeat="2"), row(repeat="3")], os.path.join(tmp, "m.csv"))
            self.assertEqual(geo.coverage(geo.load(path), 3), [])

    def test_duplicate_repeat_numbers_do_not_fake_full_coverage(self):
        """Три строки с repeat=1 — это один повтор, а не три."""
        with tempfile.TemporaryDirectory() as tmp:
            path = write_csv([row(repeat="1") for _ in range(3)], os.path.join(tmp, "m.csv"))
            short = dict(geo.coverage(geo.load(path), 3))
            self.assertEqual(short[("gaim", "p01")], 1)


class LossScenarioTest(unittest.TestCase):
    def test_ranking_uses_weight_times_loss_share(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = [
                # heavy prompt, always lost
                row(prompt="heavy", weight="3", role="listed", comp_role="recommended"),
                row(prompt="heavy", weight="3", role="listed", comp_role="recommended"),
                # light prompt, always lost
                row(prompt="light", weight="1", role="listed", comp_role="recommended"),
                row(prompt="light", weight="1", role="listed", comp_role="recommended"),
                # heavy prompt, always won
                row(prompt="won", weight="3", role="recommended", comp_role="listed"),
            ]
            scored = geo.loss_scenarios(geo.load(write_csv(rows, os.path.join(tmp, "m.csv"))), {}, 5)
            self.assertEqual([item["id"] for item in scored], ["heavy", "light"])
            self.assertAlmostEqual(scored[0]["loss"], 100.0)
            self.assertEqual(scored[0]["score"], 3.0)

    def test_brand_recommended_is_not_a_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = [row(prompt="p", role="recommended", comp_role="recommended")]
            scored = geo.loss_scenarios(geo.load(write_csv(rows, os.path.join(tmp, "m.csv"))), {}, 5)
            self.assertEqual(scored, [])

    def test_listed_competitor_is_not_a_loss(self):
        """Конкурент только упомянут — рекомендации нет ни у кого, это не провал."""
        with tempfile.TemporaryDirectory() as tmp:
            rows = [row(prompt="p", role="listed", comp_role="listed")]
            scored = geo.loss_scenarios(geo.load(write_csv(rows, os.path.join(tmp, "m.csv"))), {}, 5)
            self.assertEqual(scored, [])

    def test_top_flag_limits_the_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = [
                row(prompt=f"p{index}", weight="3", role="listed", comp_role="recommended")
                for index in range(7)
            ]
            path = write_csv(rows, os.path.join(tmp, "m.csv"))
            result = run_cli(path, "--min-n", "1", "--top", "2")
            self.assertEqual(result.returncode, 0, result.stderr)
            body = [line for line in result.stdout.splitlines() if re.match(r"\| p\d", line)]
            self.assertEqual(len(body), 2, result.stdout)


class WaveTest(unittest.TestCase):
    def test_two_waves_in_one_file_are_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = [row(repeat=str(index), date="2026-11-11") for index in range(1, 4)]
            rows += [row(repeat=str(index), date="2026-12-09") for index in range(1, 4)]
            path = write_csv(rows, os.path.join(tmp, "m.csv"))
            result = run_cli(path, "--min-n", "1")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("волн: 2", result.stdout)
            self.assertIn("несколько волн в одном файле", result.stdout)
            self.assertIn("По волнам", result.stdout)

    def test_date_filter_keeps_one_wave(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = [row(repeat=str(index), date="2026-11-11") for index in range(1, 4)]
            rows += [row(repeat=str(index), date="2026-12-09", role="listed", pos="3")
                     for index in range(1, 4)]
            path = write_csv(rows, os.path.join(tmp, "m.csv"))
            result = run_cli(path, "--min-n", "1", "--date", "2026-11-11")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Ответов: 3", result.stdout)
            self.assertIn("волн: 1", result.stdout)
            self.assertNotIn("несколько волн в одном файле", result.stdout)

    def test_unknown_date_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_csv([row()], os.path.join(tmp, "m.csv"))
            result = run_cli(path, "--date", "2020-01-01")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("no such wave", result.stderr)

    def test_date_changes_do_not_leave_the_report_identical(self):
        """Раньше date выбрасывался: две волны давали ту же сводку, что и одна."""
        with tempfile.TemporaryDirectory() as tmp:
            one = [row(repeat=str(index), date="2026-11-11") for index in range(1, 4)]
            path_one = write_csv(one, os.path.join(tmp, "one.csv"))
            two = one + [row(repeat=str(index), date="2026-12-09") for index in range(1, 4)]
            path_two = write_csv(two, os.path.join(tmp, "two.csv"))
            first = run_cli(path_one, "--min-n", "1").stdout
            second = run_cli(path_two, "--min-n", "1").stdout
            self.assertNotEqual(first, second)


class CliTest(unittest.TestCase):
    def test_sample_run_is_clean_and_counts_36_answers(self):
        result = run_cli(SAMPLE, "--prompts", PROMPTS)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Ответов: 36", result.stdout)
        self.assertIn("По движкам", result.stdout)
        self.assertIn("По каналу", result.stdout)
        self.assertNotIn("противоречивая разметка", result.stdout)

    def test_small_slice_warns_before_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_csv([row() for _ in range(5)], os.path.join(tmp, "m.csv"))
            result = run_cli(path)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("n=5 < 30", result.stdout)


if __name__ == "__main__":
    unittest.main()
