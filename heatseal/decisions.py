"""改善/劣化双门槛与效果报告组装。

规则（见 rules.csv，门槛按材料层备案）：

- 改善门：剥离强度增益的区间下界 ≥ min_seal_gain，且废耗增幅的区间上界
  ≤ max_scrap_increase。两个条件是合取，强度改善不能补偿废耗恶化。
- 劣化门：强度差区间下界 ≥ 0，且废耗增幅区间上界 ≤ max_scrap_increase。
  同样合取，且不能用改善侧的好结果补偿劣化侧的越界。
- 区间缺失（单段、无法识别、混杂）一律判为"证据不足"，既不通过也不否定，
  只说明缺什么证据。
- 完全混杂的效果不给单独效果报告；只能报联合改动（11 vs 00）。
"""
from __future__ import annotations

from dataclasses import dataclass

from .design import (CELL_NAMES, Estimability, RunSummary, all_scopes,
                     scope_groups, shared_overlap_report, summarize_runs)
from .inference import Interval, cell_means, contrast_interval, joint_vs_control


@dataclass
class GateVerdict:
    gate: str
    passed: bool | None
    seal: dict
    scrap: dict
    reasons: list[str]

    def as_dict(self) -> dict:
        return {
            "gate": self.gate,
            "verdict": {True: "通过", False: "不通过", None: "证据不足"}[self.passed],
            "seal": self.seal,
            "scrap": self.scrap,
            "reasons": self.reasons,
        }


def _gate_status(interval: Interval, lower_bound: float | None,
                 upper_bound: float | None) -> tuple[bool | None, list[str]]:
    """返回通过/不通过/证据不足及理由。"""
    reasons: list[str] = []
    if interval.lower is None or interval.upper is None:
        reasons.append(interval.reason or "区间缺失，证据不足")
        return None, reasons
    ok_low = True
    ok_high = True
    if lower_bound is not None:
        ok_low = interval.lower >= lower_bound
        if not ok_low:
            reasons.append(
                f"区间下界 {interval.lower:.3f} 低于门槛 {lower_bound:.3f}")
    if upper_bound is not None:
        ok_high = interval.upper <= upper_bound
        if not ok_high:
            reasons.append(
                f"区间上界 {interval.upper:.3f} 高于门槛 {upper_bound:.3f}")
    if ok_low and ok_high:
        reasons.append("区间整体落在门槛允许侧")
    return ok_low and ok_high, reasons


def evaluate_gates(seal_interval: Interval, scrap_interval: Interval,
                   rule: dict) -> list[GateVerdict]:
    level = float(rule["confidence"])
    min_gain = float(rule["min_seal_gain"])
    # 废耗门槛单位为每百件件数，与 scrap_per_hundred 同量纲。
    max_scrap = float(rule["max_scrap_increase"])

    improvement_seal, seal_reasons_i = _gate_status(seal_interval, min_gain, None)
    improvement_scrap, scrap_reasons_i = _gate_status(
        scrap_interval, None, max_scrap)
    if improvement_seal is True and improvement_scrap is True:
        improvement = True
    elif improvement_seal is False or improvement_scrap is False:
        improvement = False
    else:
        improvement = None

    degrade_seal, seal_reasons_d = _gate_status(seal_interval, 0.0, None)
    degrade_scrap, scrap_reasons_d = _gate_status(
        scrap_interval, None, max_scrap)
    if degrade_seal is True and degrade_scrap is True:
        degrade = True
    elif degrade_seal is False or degrade_scrap is False:
        degrade = False
    else:
        degrade = None

    seal_unit = {"unit": "牛顿（段均值）"}
    scrap_unit = {"unit": "件/百件"}
    return [
        GateVerdict(
            "改善门（强度增益达标且废耗不超限，合取）", improvement,
            seal_interval.as_dict() | seal_unit | {"threshold_lower": min_gain,
                                       "check_reasons": seal_reasons_i},
            scrap_interval.as_dict() | scrap_unit | {"threshold_upper": max_scrap,
                                        "check_reasons": scrap_reasons_i},
            seal_reasons_i + scrap_reasons_i,
        ),
        GateVerdict(
            "劣化门（强度不下降且废耗不超限，合取）", degrade,
            seal_interval.as_dict() | seal_unit | {"threshold_lower": 0.0,
                                       "check_reasons": seal_reasons_d},
            scrap_interval.as_dict() | scrap_unit | {"threshold_upper": max_scrap,
                                        "check_reasons": scrap_reasons_d},
            seal_reasons_d + scrap_reasons_d,
        ),
    ]


def _effect_block(runs: list[RunSummary], term: str, rule: dict,
                  estimability: Estimability) -> dict:
    level = float(rule["confidence"])
    seal = contrast_interval(runs, term, "seal_mean", level)
    scrap = contrast_interval(runs, term, "scrap_per_hundred", level)
    in_alias = any(term in group for group in estimability.alias_groups)
    return {
        "effect": {"p": "压力 P（0→1）", "c": "节拍 C（0→1）",
                   "pc": "P×C 交互作用"}[term],
        "estimable": term in estimability.estimable_terms,
        "aliased": in_alias,
        "replicated": term in estimability.replicated_terms,
        "seal_interval": seal.as_dict(),
        "scrap_interval": scrap.as_dict(),
        "gates": [verdict.as_dict()
                  for verdict in evaluate_gates(seal, scrap, rule)],
        "reporting_rule": (
            "该效果与别名组内其他效果在现有分配中完全混杂，不单独报效果"
            if in_alias else "效果可由现有分配识别"),
    }


def analyze_bundle(bundle) -> dict:
    """组装完整分析报告：两套视图、可估性、区间、门槛、适用范围。"""
    summaries = summarize_runs(bundle)
    rules_by_layer = {row["layer"]: row for row in bundle.rules}
    scopes_est = all_scopes(bundle)

    report: dict = {
        "unit_discipline": {
            "independent_unit": "run（整段设定层级的随机指派）",
            "piece_note": "段内检件只用于段均值，不作为独立试验计数",
            "shared_overlaps": shared_overlap_report(bundle),
        },
        "scopes": [],
        "anomalies": [],
        "descriptive_actual_view": [],
    }

    for run in summaries.values():
        if run.misassigned or run.stopped or run.assignment == "pilot":
            report["anomalies"].append({
                "run": run.run_id, "station": run.station, "layer": run.layer,
                "assigned_cell": CELL_NAMES[run.cell_assign],
                "actual_cell": CELL_NAMES[run.cell_actual],
                "misassigned": run.misassigned, "stopped": run.stopped,
                "pilot": run.assignment == "pilot",
                "itt_handling": (
                    "保留在指派组参与 ITT，不并入实际设置组"
                    if run.assignment == "random"
                    else "试跑段不进入因果/ITT 视图，只出现在实际设置描述视图"),
            })

    # ITT 因果视图，按 (工位, 材料层) 适用范围分别给结论。
    causal_groups = scope_groups(summaries, causal_only=True)
    for (station, layer), estimability in sorted(scopes_est.items()):
        rule = rules_by_layer[layer]
        runs = causal_groups[(station, layer)]
        level = float(rule["confidence"])
        joint_seal = joint_vs_control(runs, "seal_mean", level)
        joint_scrap = joint_vs_control(runs, "scrap_per_hundred", level)
        scope_block = {
            "station": station, "layer": layer,
            "applicability": f"工位 {station}、材料层 {layer} 的随机指派段",
            "cells_itt": {name: info.__dict__ for name, info in
                          estimability.cells.items()},
            "design_rank": estimability.rank,
            "estimable_terms": estimability.estimable_terms,
            "alias_groups": estimability.alias_groups,
            "replicated_terms": estimability.replicated_terms,
            "estimability_note": estimability.note,
            "joint_change_11_vs_00": {
                "seal_interval": joint_seal.as_dict(),
                "scrap_interval": joint_scrap.as_dict(),
                "gates": [v.as_dict()
                          for v in evaluate_gates(joint_seal, joint_scrap, rule)],
                "interpretation": "两项一起改动的直接对比；P/C 混杂时这是唯一可报的因果对比",
            },
            "effects": [_effect_block(runs, term, rule, estimability)
                        for term in ("p", "c", "pc")],
        }
        report["scopes"].append(scope_block)

    # 描述视图：按实际设置（含 pilot），明确标注不可作更改证据。
    desc_groups = scope_groups(summaries, causal_only=False)
    for (station, layer), runs in sorted(desc_groups.items()):
        level = float(rules_by_layer[layer]["confidence"])
        report["descriptive_actual_view"].append({
            "station": station, "layer": layer,
            "cells": cell_means(runs, use_actual=True, level=level),
            "caveat": (
                "按实际设置汇总的描述性差异，含换错/停机/试跑段；"
                "随机化已被破坏，不能用于更改生产参数的证据，只描述实际发生了什么"),
        })
    return report


def committable_changes(bundle) -> list[dict]:
    """扁平化输出当前可以承诺/不能承诺的参数更改清单。"""
    report = analyze_bundle(bundle)
    decisions = []
    for scope in report["scopes"]:
        # 联合改动
        gates = {v["gate"]: v["verdict"]
                 for v in scope["joint_change_11_vs_00"]["gates"]}
        decisions.append({
            "station": scope["station"], "layer": scope["layer"],
            "target": "联合改动 P=1,C=1（相对 00）",
            "improvement": gates.get("改善门（强度增益达标且废耗不超限，合取）"),
            "no_degradation": gates.get("劣化门（强度不下降且废耗不超限，合取）"),
            "can_change_production": all(
                v == "通过" for v in gates.values()),
            "limiting_evidence": scope["joint_change_11_vs_00"]["gates"],
        })
        for effect in scope["effects"]:
            if effect["aliased"]:
                decisions.append({
                    "station": scope["station"], "layer": scope["layer"],
                    "target": effect["effect"],
                    "improvement": "不可单报",
                    "no_degradation": "不可单报",
                    "can_change_production": False,
                    "limiting_evidence": [effect["reporting_rule"]],
                })
                continue
            gates = {v["gate"]: v["verdict"] for v in effect["gates"]}
            decisions.append({
                "station": scope["station"], "layer": scope["layer"],
                "target": effect["effect"],
                "improvement": gates.get("改善门（强度增益达标且废耗不超限，合取）"),
                "no_degradation": gates.get("劣化门（强度不下降且废耗不超限，合取）"),
                "can_change_production": all(
                    v == "通过" for v in gates.values()),
                "limiting_evidence": [v["reasons"]
                                      for v in effect["gates"]],
            })
    return decisions
