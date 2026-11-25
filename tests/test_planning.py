"""排程约束、证据价值分级与新批次登记测试。"""
from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from heatseal.loading import CONFIG_PATH, load_bundle, merge_bundles
from heatseal.planning import (classify_slot_value,
                               enumerate_feasible_drafts, evaluate_feasibility,
                               explain_change)

DATA = Path(__file__).resolve().parents[1] / "data"


def _write_table(directory: Path, name: str, columns: list[str],
                 rows: list[dict]) -> None:
    with (directory / f"{name}.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _empty_bundle_dir(directory: Path) -> None:
    """按交换约定写一套空批次骨架；新批次只追加 runs/results/slots。

    rolls/rules 是资源与备案主表，不随交付批复制（merge 也会拒绝重复键）。
    """
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    for name, spec in config["tables"].items():
        _write_table(directory, name, spec["columns"], [])


class FeasibilityTests(unittest.TestCase):
    def setUp(self):
        self.bundle = load_bundle(DATA)

    def test_locked_window_consumes_budget_first(self):
        # W01 锁定，先扣 Q1 4 件；Q1 上限 6，剩余 2 不足以再放任何 Q1 窗口。
        result = evaluate_feasibility(self.bundle, ["W03"])
        self.assertFalse(result["feasible"])
        self.assertIn("W01", result["locked_ids"])
        blockers = result["windows"][0]["blockers"]
        self.assertTrue(any("Q1 预算不足" in text for text in blockers))

    def test_maintain_window_never_enters_trial(self):
        values = {item["slot"]: item for item in
                  classify_slot_value(self.bundle)}
        self.assertEqual(values["W05"]["value"], "不进入试验")

    def test_crew_overlap_blocks(self):
        # W01(锁定, R1, 480-505) 与 W02(R1, 505-530) 首尾相接不重叠；
        # 构造一个同班组且时间重叠的选择冲突场景：W02 与 W03 同为 R1、505 起。
        result = evaluate_feasibility(self.bundle, ["W02", "W03"])
        self.assertFalse(result["feasible"])
        all_blockers = sum((w["blockers"] for w in result["windows"]), [])
        self.assertTrue(any("班组" in text for text in all_blockers))

    def test_after_prerequisite_requires_prior_selection(self):
        alone = evaluate_feasibility(self.bundle, ["W06"])
        self.assertTrue(any("前置窗口 W04" in text
                            for text in alone["windows"][0]["blockers"]))
        together = evaluate_feasibility(self.bundle, ["W04", "W06"])
        self.assertTrue(together["feasible"])

    def test_value_classification(self):
        values = {item["slot"]: item for item in
                  classify_slot_value(self.bundle)}
        # W04 给 thick 补 01 单元，拆开 00/11 混杂。
        self.assertEqual(values["W04"]["value"], "拆分混杂")
        self.assertEqual(values["W06"]["value"], "拆分混杂")
        # W02 落在已存在的 N thin 01 单元，只提精度。
        self.assertEqual(values["W02"]["value"], "仅提高精度")
        # W03/W07 被 Q1 预算硬阻断。
        self.assertEqual(values["W03"]["value"], "不可行")
        self.assertEqual(values["W07"]["value"], "不可行")

    def test_drafts_do_not_double_count_resources(self):
        drafts = enumerate_feasible_drafts(self.bundle)
        # 所有可行草案都不能包含被预算/互斥阻断的 W03、W07。
        for draft in drafts:
            self.assertTrue(set(draft["draft"]).isdisjoint({"W03", "W07"}))
        best = drafts[0]
        # W04+W06 同时拆开 thick 的 P、C 混杂，排名最高。
        self.assertEqual(best["splits_alias"], 2)
        self.assertIn("W04", best["draft"])
        self.assertIn("W06", best["draft"])
        # 每个可行草案的卷料占用都不超上限。
        for roll, status in best["roll_status"].items():
            self.assertLessEqual(status["spent"], status["cap"])

    def test_explain_requires_disambiguation(self):
        with self.assertRaisesRegex(ValueError, "消歧"):
            explain_change(self.bundle, "压力")
        answer = explain_change(self.bundle, "压力", "S", "thick")
        self.assertFalse(answer["allowed_now"])
        self.assertEqual(answer["improvement_gate"], "不可单报")


class RegisterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.incoming = self.root / "incoming"
        self.incoming.mkdir()
        _empty_bundle_dir(self.incoming)

    def tearDown(self):
        self.temp.cleanup()

    def _append(self, table: str, rows: list[dict]) -> None:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        spec = config["tables"][table]
        _write_table(self.incoming, table, spec["columns"], rows)

    def test_duplicate_identity_rejected_on_merge(self):
        base = load_bundle(DATA)
        self._append("runs", [dict(base.runs[0])])
        with self.assertRaisesRegex(ValueError, "已存在，拒绝重复登记"):
            merge_bundles(base, load_bundle(self.incoming, partial=True))

    def test_new_batch_redecides_pending_parameters(self):
        base = load_bundle(DATA)
        # 新批次给 S thick 补一个随机 01 段（拆开 00/11 混杂），两件检件。
        self._append("runs", [{
            "id": "E20", "station": "S", "layer": "thick", "roll": "R10",
            "p_assign": 0, "c_assign": 1, "p_actual": 0, "c_actual": 1,
            "stopped": 0, "assignment": "random", "stage": "round2",
        }])
        self._append("results", [
            {"id": "S101", "run": "E20", "overlap": "P020",
             "seal": 10.4, "scrap": 0},
            {"id": "S102", "run": "E20", "overlap": "P021",
             "seal": 10.8, "scrap": 0},
        ])
        merged = merge_bundles(base, load_bundle(self.incoming, partial=True))
        from heatseal.design import all_scopes
        scopes = all_scopes(merged)
        # 00/01/11 三单元：C 可估，P 与 PC 仍混杂；不再是三项一团。
        self.assertEqual(scopes[("S", "thick")].estimable_terms, ["c"])
        self.assertEqual(scopes[("S", "thick")].alias_groups, [["p", "pc"]])

    def test_locked_trial_remains_after_new_data(self):
        base = load_bundle(DATA)
        self._append("runs", [{
            "id": "E21", "station": "N", "layer": "thin", "roll": "R11",
            "p_assign": 1, "c_assign": 0, "p_actual": 1, "c_actual": 0,
            "stopped": 0, "assignment": "random", "stage": "round2",
        }])
        self._append("results", [
            {"id": "S103", "run": "E21", "overlap": "P022",
             "seal": 10.1, "scrap": 0},
        ])
        merged = merge_bundles(base, load_bundle(self.incoming, partial=True))
        locked = [row["id"] for row in merged.slots if int(row["locked"]) == 1]
        self.assertEqual(locked, ["W01"])

    def test_roundtrip_write_and_reload(self):
        from heatseal.loading import write_bundle
        base = load_bundle(DATA)
        out = self.root / "out"
        write_bundle(base, out)
        reloaded = load_bundle(out)
        self.assertEqual(len(reloaded.runs), len(base.runs))
        self.assertEqual(len(reloaded.results), len(base.results))
        # 数值类型保持。
        self.assertIsInstance(reloaded.runs[0]["p_assign"], int)
        self.assertIsInstance(reloaded.results[0]["seal"], float)


if __name__ == "__main__":
    unittest.main()
