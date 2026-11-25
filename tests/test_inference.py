"""区间估计与双门槛决策测试。"""
from __future__ import annotations

import unittest
from pathlib import Path

from heatseal.decisions import analyze_bundle, committable_changes
from heatseal.design import scope_groups, summarize_runs
from heatseal.inference import contrast_interval, joint_vs_control

DATA = Path(__file__).resolve().parents[1] / "data"


class InferenceTests(unittest.TestCase):
    def setUp(self):
        from heatseal.loading import load_bundle
        self.bundle = load_bundle(DATA)
        self.summaries = summarize_runs(self.bundle)
        self.runs_n = scope_groups(self.summaries, True)[("N", "thin")]
        self.runs_thick = scope_groups(self.summaries, True)[("S", "thick")]

    def test_joint_interval_uses_run_level_units(self):
        interval = joint_vs_control(self.runs_n, "seal_mean", 0.95)
        # 11 单元两段均值 10.25/9.45，00 单元 8.7/8.7，差 1.15。
        self.assertAlmostEqual(interval.estimate, 1.15, places=6)
        self.assertIsNotNone(interval.lower)
        self.assertEqual(interval.n_runs, [2, 2])
        # 自由度受段数限制（每臂 2 段 -> df=1），区间很宽。
        self.assertIn("自由度 1.00", interval.reason)

    def test_single_replication_gives_point_but_no_interval(self):
        interval = contrast_interval(self.runs_n, "p", "seal_mean", 0.95)
        self.assertIsNotNone(interval.estimate)
        self.assertIsNone(interval.lower)
        self.assertIsNone(interval.upper)
        self.assertIn("单段复制", interval.reason)

    def test_inestimable_effect_returns_missing_estimate(self):
        interval = contrast_interval(self.runs_thick, "p", "seal_mean", 0.95)
        # 00/11 设计下 P 与 C、PC 混杂，不给点估计。
        self.assertTrue(interval.estimate != interval.estimate)  # NaN
        self.assertIsNone(interval.lower)

    def test_scrap_and_seal_gates_are_conjunctive(self):
        report = analyze_bundle(self.bundle)
        n_thin = next(s for s in report["scopes"]
                      if s["station"] == "N" and s["layer"] == "thin")
        verdicts = {g["gate"].split("（")[0]: g["verdict"]
                    for g in n_thin["joint_change_11_vs_00"]["gates"]}
        # 强度与废耗任一证据不足都不能通过，且互不补偿。
        self.assertEqual(set(verdicts.values()), {"证据不足"})

    def test_no_change_is_committable_in_current_data(self):
        for item in committable_changes(self.bundle):
            self.assertFalse(item["can_change_production"])

    def test_aliased_effect_is_flagged_not_reported(self):
        report = analyze_bundle(self.bundle)
        thick = next(s for s in report["scopes"]
                     if s["station"] == "S" and s["layer"] == "thick")
        for effect in thick["effects"]:
            self.assertTrue(effect["aliased"])
            self.assertIn("不单独报效果", effect["reporting_rule"])
            self.assertIsNone(effect["seal_interval"]["estimate"])

    def test_descriptive_view_is_separate_and_caveated(self):
        report = analyze_bundle(self.bundle)
        self.assertEqual(len(report["descriptive_actual_view"]), 3)
        for view in report["descriptive_actual_view"]:
            self.assertIn("不能用于更改生产参数", view["caveat"])
        # E04 实际落在 10，描述视图 10 单元含 E04 且带异常标记。
        n_desc = next(v for v in report["descriptive_actual_view"]
                      if v["station"] == "N" and v["layer"] == "thin")
        self.assertIn("E04", n_desc["cells"]["10"]["run_ids"])
        self.assertIn("E04", n_desc["cells"]["10"]["flagged"])
        # 废耗描述以件/百件给出（0–1 比率乘 100）。
        self.assertIn("scrap_per_hundred", n_desc["cells"]["10"])

    def test_gate_thresholds_differ_between_improvement_and_degradation(self):
        report = analyze_bundle(self.bundle)
        n_thin = next(s for s in report["scopes"]
                      if s["station"] == "N" and s["layer"] == "thin")
        improve, degrade = n_thin["joint_change_11_vs_00"]["gates"]
        # 改善门强度门槛 0.7，劣化门强度门槛 0。
        self.assertEqual(improve["seal"]["threshold_lower"], 0.7)
        self.assertEqual(degrade["seal"]["threshold_lower"], 0.0)
        # 废耗门槛两门相同但都是独立合取项。
        self.assertEqual(improve["scrap"]["threshold_upper"], 0.2)
        self.assertEqual(degrade["scrap"]["threshold_upper"], 0.2)


if __name__ == "__main__":
    unittest.main()
