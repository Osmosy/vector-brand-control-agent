"""Tests for the GEO scorer. Stdlib only: python3 -m unittest discover -s tests -q."""

import csv
import importlib.util
import os
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
    "brand_mentioned",
    "brand_role",
    "brand_pos",
    "competitor",
    "competitor_role",
    "competitor_pos",
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
        comp_pos="2", cited="true"):
    return [
        "2026-11-11", engine, prompt, cls, weight, repeat, mentioned, role, pos,
        comp, comp_role, comp_pos, cited, "true",
    ]


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

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "m.csv")

    def tearDown(self):
        self.tmp.cleanup()


class CoverageTest(unittest.TestCase):
    def test_missing_pair_and_short_repeats_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_csv(
                [
                    row(engine="gaim", prompt="p01"),
                    row(engine="gaim", prompt="p01"),
                    row(engine="other", prompt="p01"),
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
            path = write_csv([row() for _ in range(3)], os.path.join(tmp, "m.csv"))
            self.assertEqual(geo.coverage(geo.load(path), 3), [])


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
            self.assertAlmostEqual(scored[0]["loss"], 1.0)
            self.assertEqual(scored[0]["score"], 3.0)

    def test_brand_recommended_is_not_a_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = [row(prompt="p", role="recommended", comp_role="recommended")]
            scored = geo.loss_scenarios(geo.load(write_csv(rows, os.path.join(tmp, "m.csv"))), {}, 5)
            self.assertEqual(scored, [])


class CliTest(unittest.TestCase):
    def test_sample_run_is_clean_and_counts_36_answers(self):
        result = subprocess.run(
            [sys.executable, SCORER, "--measurements", SAMPLE, "--prompts", PROMPTS],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Ответов: 36", result.stdout)
        self.assertIn("По движкам", result.stdout)

    def test_small_slice_warns_before_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_csv([row() for _ in range(5)], os.path.join(tmp, "m.csv"))
            result = subprocess.run(
                [sys.executable, SCORER, "--measurements", path],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("n=5 < 30", result.stdout)


if __name__ == "__main__":
    unittest.main()
