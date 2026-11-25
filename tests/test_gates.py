"""用合成快照直接验证双门槛的翻案逻辑（不依赖演练数据的特定数值）。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from heatseal.decisions import evaluate_gates
from heatseal.inference import Interval
from heatseal.loading import CONFIG_PATH, load_bundle, write_bundle
from heatseal.planning import evaluate_feasibility

DATA = Path(__file__).resolve().parents[1] / "data"

THIN_RULE = {"confidence": "0.95", "min_seal_gain": "0.7",
             "max_scrap_increase": "0.2", "layer": "thin"}


def _interval(estimate: float, half_width: float,
              level: float = 0.95) -> Interval:
    return Interval(estimate=estimate, lower=estimate - half_width,
                    upper=estimate + half_width, level=level,
                    n_runs=[4, 4], reason="合成区间，单位为独立段")


class GateLogicTests(unittest.TestCase):
    def setUp(self):
        self.rule = THIN_RULE

    def test_improvement_passes_when_both_metrics_clear_thresholds(self):
        # 强度增益区间整体高于 0.7；废耗增幅区间整体低于 0.2。
        seal = _interval(1.2, 0.3)       # 下界 0.9 > 0.7
        scrap = _interval(0.05, 0.1)     # 上界 0.15 < 0.2
        verdicts = evaluate_gates(seal, scrap, self.rule)
        self.assertEqual([v.passed for v in verdicts], [True, True])

    def test_degradation_gate_is_stricter_but_separate_from_improvement(self):
        # 强度增益不足（改善门失败），但强度本身不下降且废耗可控（劣化门通过）。
        seal = _interval(0.5, 0.1)       # 下界 0.4：高于 0 但低于 0.7
        scrap = _interval(0.05, 0.1)
        improve, degrade = evaluate_gates(seal, scrap, self.rule)
        self.assertFalse(improve.passed)
        self.assertTrue(degrade.passed)

    def test_scrap_degradation_cannot_be_compensated_by_seal_gain(self):
        # 强度大幅改善，但废耗区间上界超过备案上限：两门都不能靠强度补偿。
        seal = _interval(3.0, 0.2)
        scrap = _interval(0.3, 0.05)    # 上界 0.35 > 0.2
        improve, degrade = evaluate_gates(seal, scrap, self.rule)
        self.assertFalse(improve.passed)
        self.assertFalse(degrade.passed)
        scrap_reasons = improve.scrap["check_reasons"]
        self.assertTrue(any("高于门槛" in text for text in scrap_reasons))

    def test_missing_interval_is_inconclusive_not_failure(self):
        seal = Interval(estimate=1.0, lower=None, upper=None, level=0.95,
                        n_runs=[1, 1], reason="单段无区间")
        scrap = _interval(0.05, 0.1)
        improve, degrade = evaluate_gates(seal, scrap, self.rule)
        self.assertIsNone(improve.passed)
        self.assertIsNone(degrade.passed)

    def test_boundary_touch_counts_as_pass(self):
        # 区间下界恰好等于门槛 0.7：落在允许侧，判通过。
        seal = _interval(0.9, 0.2)       # 下界 = 0.7
        scrap = _interval(0.1, 0.1)      # 上界 = 0.2
        verdicts = evaluate_gates(seal, scrap, self.rule)
        self.assertEqual([v.passed for v in verdicts], [True, True])


class SnapshotConsistencyTests(unittest.TestCase):
    def test_written_snapshot_preserves_budget_semantics(self):
        base = load_bundle(DATA)
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "snapshot"
            write_bundle(base, out)
            reloaded = load_bundle(out)
            result = evaluate_feasibility(reloaded, ["W02", "W04", "W06"])
            self.assertTrue(result["feasible"])
            # Q1 锁定窗 W01 已扣 4，草案不再使用 Q1，剩余仍是 2。
            self.assertEqual(result["roll_status"]["Q1"]["remaining"], 2)

    def test_config_remains_the_single_source_of_columns(self):
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        bundle = load_bundle(DATA)
        for name, spec in config["tables"].items():
            with (DATA / f"{name}.csv").open(encoding="utf-8") as handle:
                import csv
                header = next(csv.reader(handle))
            self.assertEqual(header, spec["columns"])
        self.assertEqual({row["layer"] for row in bundle.rules},
                         {"thin", "thick"})


if __name__ == "__main__":
    unittest.main()
