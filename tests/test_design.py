"""试验单位、ITT 纪律与可估性判断的单元测试。"""
from __future__ import annotations

import unittest

from heatseal.design import (CELL_NAMES, all_scopes, contrast_weights,
                             scope_groups, shared_overlap_report,
                             summarize_runs)
from heatseal.loading import load_bundle

DATA = __import__("pathlib").Path(__file__).resolve().parents[1] / "data"


class DesignTests(unittest.TestCase):
    def setUp(self):
        self.bundle = load_bundle(DATA)
        self.summaries = summarize_runs(self.bundle)

    def test_independent_unit_is_run_not_piece(self):
        # 段内两件只折叠成一个段均值，段数才是独立单位数。
        e01 = self.summaries["E01"]
        self.assertEqual(e01.n_pieces, 2)
        groups = scope_groups(self.summaries, causal_only=True)
        n_thin = groups[("N", "thin")]
        # E01..E04（initial）+ E10、E11（followup）共 6 个独立段；
        # 段内 2 件检件不增加独立单位数。
        self.assertEqual(len(n_thin), 6)

    def test_shared_overlap_cross_station_detected(self):
        report = shared_overlap_report(self.bundle)
        cross = {item["overlap"] for item in report if item["cross_station"]}
        # P001/P002 同卷跨 N/S；P014/P015 是 followup 跨 N/S 同母片。
        self.assertEqual(cross, {"P001", "P002", "P005", "P014", "P015"})
        for item in report:
            self.assertGreaterEqual(len(item["runs"]), 2)

    def test_itt_keeps_misassigned_and_stopped_in_assigned_cell(self):
        # E04 指派 11、实际 10：ITT 视图仍在 11；E06 停机仍在指派组。
        self.assertEqual(CELL_NAMES[self.summaries["E04"].cell_assign], "11")
        self.assertEqual(CELL_NAMES[self.summaries["E04"].cell_actual], "10")
        self.assertTrue(self.summaries["E04"].misassigned)
        self.assertTrue(self.summaries["E06"].stopped)
        scopes = all_scopes(self.bundle)
        n_thin = scopes[("N", "thin")]
        self.assertIn("E04", n_thin.cells["11"].runs)
        self.assertIn("E04", n_thin.cells["11"].flagged_runs)
        self.assertIn("E06", scopes[("S", "thin")].cells["11"].flagged_runs)

    def test_pilot_excluded_from_causal_view(self):
        causal = scope_groups(self.summaries, causal_only=True)
        thick_runs = causal[("S", "thick")]
        self.assertNotIn("E09", [run.run_id for run in thick_runs])
        # 描述视图（含 pilot）里 E09 在 01 单元。
        descriptive = scope_groups(self.summaries, causal_only=False)
        self.assertIn("E09", [
            run.run_id for run in descriptive[("S", "thick")]
            if run.cell_actual == (0, 1)])

    def test_full_factorial_estimability_and_partial_alias(self):
        scopes = all_scopes(self.bundle)
        # N thin 四单元齐：P/C/PC 都可估，但没有任何效果达到复制要求。
        n_thin = scopes[("N", "thin")]
        self.assertEqual(n_thin.rank, 4)
        self.assertEqual(set(n_thin.estimable_terms), {"p", "c", "pc"})
        self.assertEqual(n_thin.alias_groups, [])
        self.assertEqual(n_thin.replicated_terms, [])

        # S thick 只有 00/11：三项完全混杂，不给单报。
        s_thick = scopes[("S", "thick")]
        self.assertEqual(s_thick.alias_groups, [["p", "c", "pc"]])
        self.assertEqual(s_thick.estimable_terms, [])

        # S thin 有 00/10/11：P 可估，C 与 PC 混杂。
        s_thin = scopes[("S", "thin")]
        self.assertEqual(s_thin.estimable_terms, ["p"])
        self.assertEqual(s_thin.alias_groups, [["c", "pc"]])

    def test_contrast_weights_match_known_designs(self):
        # 全因子 P 对比为标准 ±0.5（P=1 两单元 +0.5，P=0 两单元 -0.5）。
        full = {(0, 0), (0, 1), (1, 0), (1, 1)}
        weights = contrast_weights(full, "p")
        self.assertAlmostEqual(weights[(1, 0)], 0.5)
        self.assertAlmostEqual(weights[(1, 1)], 0.5)
        self.assertAlmostEqual(weights[(0, 0)], -0.5)
        self.assertAlmostEqual(weights[(0, 1)], -0.5)
        # {00,10,11} 下 P 权重为 -1/+1/0，C/PC 不可估。
        partial = {(0, 0), (1, 0), (1, 1)}
        wp = contrast_weights(partial, "p")
        self.assertIsNone(contrast_weights(partial, "c"))
        self.assertIsNone(contrast_weights(partial, "pc"))
        self.assertAlmostEqual(wp[(0, 0)], -1.0)
        self.assertAlmostEqual(wp[(1, 0)], 1.0)
        self.assertAlmostEqual(wp[(1, 1)], 0.0)

    def test_scope_carries_station_layer_and_applicability(self):
        scopes = all_scopes(self.bundle)
        self.assertEqual(set(scopes), {("N", "thin"), ("S", "thin"),
                                       ("S", "thick")})


if __name__ == "__main__":
    unittest.main()
