"""段级区间估计。

独立单位是 run：把每段的均值/废耗率作为一个观察，用 Welch 双样本 t
构造均值差区间。单元内只有一个段时不给区间，只给点估计并标明无复制。
同母片跨段检件已在 design 层标记，这里不会把检件数当作自由度。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats

from .design import (CELLS, CELL_NAMES, RunSummary, contrast_weights)


@dataclass
class Interval:
    estimate: float
    lower: float | None
    upper: float | None
    level: float
    n_runs: list[int]
    reason: str = ""

    def as_dict(self) -> dict:
        def clean(value):
            if value is None or (isinstance(value, float)
                                 and (value != value)):  # NaN 检测
                return None
            return round(value, 4)
        return {
            "estimate": clean(self.estimate),
            "lower": clean(self.lower),
            "upper": clean(self.upper),
            "confidence": self.level,
            "n_runs": self.n_runs,
            "reason": self.reason,
        }


def _welch_diff(values_a: list[float], values_b: list[float],
                level: float) -> Interval:
    """a 相对 b 的均值差 Welch 区间。"""
    n_a, n_b = len(values_a), len(values_b)
    mean = float(np.mean(values_a) - np.mean(values_b))
    if n_a < 2 or n_b < 2:
        return Interval(
            estimate=mean, lower=None, upper=None, level=level,
            n_runs=[n_a, n_b],
            reason=("对比两侧各需至少 2 个独立段才能构造区间；"
                    "当前仅有点估计，不能作为承诺证据"),
        )
    var_a = float(np.var(values_a, ddof=1))
    var_b = float(np.var(values_b, ddof=1))
    se = np.sqrt(var_a / n_a + var_b / n_b)
    if se == 0:
        return Interval(
            estimate=mean, lower=mean, upper=mean, level=level,
            n_runs=[n_a, n_b], reason="段间方差为 0，退化为点区间",
        )
    df = (var_a / n_a + var_b / n_b) ** 2 / (
        (var_a / n_a) ** 2 / (n_a - 1) + (var_b / n_b) ** 2 / (n_b - 1))
    critical = stats.t.ppf(1 - (1 - level) / 2, df)
    return Interval(
        estimate=mean, lower=mean - critical * se, upper=mean + critical * se,
        level=level, n_runs=[n_a, n_b],
        reason=f"Welch t 区间，自由度 {df:.2f}，单位为独立段",
    )


def arm_values(runs: list[RunSummary], cell: tuple[int, int],
               metric: str, use_actual: bool) -> list[float]:
    values = []
    for item in runs:
        current = item.cell_actual if use_actual else item.cell_assign
        if current == cell:
            values.append(getattr(item, metric))
    return values


def cell_means(runs: list[RunSummary], use_actual: bool,
               level: float) -> dict[str, dict]:
    """逐单元的描述性统计（段等权，非检件等权）。"""
    out: dict[str, dict] = {}
    for cell in CELLS:
        key = CELL_NAMES[cell]
        members = [r for r in runs
                   if (r.cell_actual if use_actual else r.cell_assign) == cell]
        if not members:
            out[key] = {"n_runs": 0, "run_ids": []}
            continue
        seal = [r.seal_mean for r in members]
        scrap = [r.scrap_per_hundred for r in members]
        entry = {
            "n_runs": len(members),
            "run_ids": [r.run_id for r in members],
            "flagged": [r.run_id for r in members
                        if r.misassigned or r.stopped or r.assignment == "pilot"],
            "seal_mean_n": round(float(np.mean(seal)), 4),
            "scrap_per_hundred": round(float(np.mean(scrap)), 4),
        }
        if len(members) >= 2:
            entry["seal_sd_across_runs_n"] = round(float(np.std(seal, ddof=1)), 4)
            entry["scrap_sd_across_runs_per_hundred"] = round(
                float(np.std(scrap, ddof=1)), 4)
        else:
            entry["seal_sd_across_runs"] = None
            entry["scrap_sd_across_runs"] = None
            entry["interval_note"] = "单元仅 1 个段，无段间区间"
        out[key] = entry
    return out


def contrast_interval(runs: list[RunSummary], term: str, metric: str,
                      level: float, use_actual: bool = False) -> Interval:
    """按当前分配下的实际对比权重合成效果区间。

    效果不可估（单元缺失导致无对比权重）时返回 estimate=None；
    可估但涉及单元只有单段时给点估计、不给区间。
    """
    if use_actual:
        present = {run.cell_actual for run in runs}
    else:
        present = {run.cell_assign for run in runs}
    weights = contrast_weights(present, term)
    if weights is None:
        return Interval(
            estimate=float("nan"), lower=None, upper=None, level=level,
            n_runs=[0, 0],
            reason=f"效果 {term} 在现有单元组合下不可估（与其他变化混杂或缺单元）",
        )

    grouped: dict[tuple[int, int], list[float]] = {}
    for cell, weight in weights.items():
        if abs(weight) < 1e-12:
            continue
        values = arm_values(runs, cell, metric, use_actual)
        if not values:
            return Interval(
                estimate=float("nan"), lower=None, upper=None, level=level,
                n_runs=[0, 0],
                reason=f"效果 {term} 所需单元 {CELL_NAMES[cell]} 没有段，无法识别",
            )
        grouped[cell] = values

    point = float(sum(weights[cell] * float(np.mean(values))
                      for cell, values in grouped.items()))
    counts = [len(values) for values in grouped.values()]
    if min(counts) < 2:
        return Interval(
            estimate=point, lower=None, upper=None, level=level,
            n_runs=counts,
            reason=("涉及单元存在单段复制，效果点估计可算但无区间；"
                    "不可单独作为更改生产参数的证据"),
        )

    # 线性对比的 Welch 型方差：sum(w_i^2 * s_i^2/n_i)，df 用 Satterthwaite。
    variance = 0.0
    df_num = 0.0
    df_den = 0.0
    for cell, values in grouped.items():
        weight = weights[cell]
        n = len(values)
        s2 = float(np.var(values, ddof=1))
        component = (weight ** 2) * s2 / n
        variance += component
        df_num += component
        df_den += component ** 2 / (n - 1)
    se = float(np.sqrt(variance))
    if se == 0:
        return Interval(point, point, point, level, counts,
                        "段间方差为 0，退化为点区间")
    df = df_num ** 2 / df_den
    critical = stats.t.ppf(1 - (1 - level) / 2, df)
    return Interval(
        estimate=point, lower=point - critical * se, upper=point + critical * se,
        level=level, n_runs=counts,
        reason=f"当前分配对比的 Welch 型区间，自由度 {df:.2f}，单位为独立段",
    )


def joint_vs_control(runs: list[RunSummary], metric: str,
                     level: float, use_actual: bool = False) -> Interval:
    """11 单元相对 00 对照单元的直接差（联合改动描述）。"""
    values_11 = arm_values(runs, (1, 1), metric, use_actual)
    values_00 = arm_values(runs, (0, 0), metric, use_actual)
    if not values_11 or not values_00:
        return Interval(
            estimate=float("nan"), lower=None, upper=None, level=level,
            n_runs=[len(values_11), len(values_00)],
            reason="11 或 00 单元缺段，无法构造联合改动对比",
        )
    return _welch_diff(values_11, values_00, level)
