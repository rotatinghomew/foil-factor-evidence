"""试验设计层：段级汇总、分组与可估性判断。

核心纪律：

1. 随机指派发生在整段设定上，独立试验单位是 run（段），不是段内检件；
   段内多件只用于刻画该段的均值与废耗。
2. 同一母片(overlap)跨 run（含上游同卷跨工位）的检件标记为关联观察，
   不增加独立样本量，只作为关联提示输出。
3. ITT 视图按安排时的 ``p_assign/c_assign`` 分组：换错设置(E04)、中途停机
   (E06)仍留在指派组并带异常标记；试跑(pilot)不进因果视图。
4. 描述视图按实际 ``p_actual/c_actual`` 分组，可纳入 pilot，但同样标注异常，
   两者永远分开呈现。
5. 2x2 设计用设计矩阵秩判断压力、节拍、交互作用各自是否可估；
   与其他变化无法区分的效果只报别名组，不单独报效果。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

TERMS = ("intercept", "p", "c", "pc")
CELLS = ((0, 0), (0, 1), (1, 0), (1, 1))
CELL_NAMES = {(0, 0): "00", (0, 1): "01", (1, 0): "10", (1, 1): "11"}
CONTRASTS = {
    # 每个效果在 2x2 单元上的对比系数（单元顺序 00,01,10,11）
    "p": np.array([-0.5, -0.5, 0.5, 0.5]),
    "c": np.array([-0.5, 0.5, -0.5, 0.5]),
    "pc": np.array([0.5, -0.5, -0.5, 0.5]),
}


@dataclass
class RunSummary:
    """一个段（独立试验单位）的汇总。"""

    run_id: str
    station: str
    layer: str
    roll: str
    cell_assign: tuple[int, int]
    cell_actual: tuple[int, int]
    assignment: str
    stage: str
    stopped: bool
    misassigned: bool
    n_pieces: int
    seal_mean: float
    scrap_rate: float
    overlaps: list[str] = field(default_factory=list)

    @property
    def scrap_per_hundred(self) -> float:
        """废耗率换算为每百件件数，与 rules 的门槛同量纲。"""
        return self.scrap_rate * 100.0


def summarize_runs(bundle) -> dict[str, RunSummary]:
    """把逐检件结果折叠为段级汇总；空结果段不进入汇总。"""
    by_run: dict[str, list[dict]] = {}
    for result in bundle.results:
        by_run.setdefault(result["run"], []).append(result)

    summaries: dict[str, RunSummary] = {}
    for run in bundle.runs:
        pieces = by_run.get(run["id"], [])
        if not pieces:
            continue
        seal_mean = float(np.mean([item["seal"] for item in pieces]))
        scrap_rate = float(np.mean([item["scrap"] for item in pieces]))
        summaries[run["id"]] = RunSummary(
            run_id=run["id"], station=run["station"], layer=run["layer"],
            roll=run["roll"],
            cell_assign=(run["p_assign"], run["c_assign"]),
            cell_actual=(run["p_actual"], run["c_actual"]),
            assignment=run["assignment"], stage=run["stage"],
            stopped=bool(run["stopped"]),
            misassigned=run["p_assign"] != run["p_actual"]
            or run["c_assign"] != run["c_actual"],
            n_pieces=len(pieces),
            seal_mean=seal_mean, scrap_rate=scrap_rate,
            overlaps=sorted(item["overlap"] for item in pieces),
        )
    return summaries


def ittt_eligible(summaries: dict[str, RunSummary]) -> list[RunSummary]:
    """ITT 因果视图：只含随机指派段；换错/停机保留在指派组并打标。"""
    return [item for item in summaries.values() if item.assignment == "random"]


def scope_groups(summaries: dict[str, RunSummary],
                 causal_only: bool) -> dict[tuple[str, str], list[RunSummary]]:
    """按 (工位, 材料层) 划分适用范围。"""
    items = ittt_eligible(summaries) if causal_only else list(summaries.values())
    groups: dict[tuple[str, str], list[RunSummary]] = {}
    for item in items:
        groups.setdefault((item.station, item.layer), []).append(item)
    return groups


def design_matrix(cell: tuple[int, int]) -> np.ndarray:
    """单个 2x2 单元在截距/P/C/P×C 下的设计行。"""
    p, c = cell
    return np.array([1.0, float(p), float(c), float(p * c)])


@dataclass
class CellInfo:
    cell: tuple[int, int]
    runs: list[str]
    n_runs: int
    flagged_runs: list[str]


@dataclass
class Estimability:
    """一个 (工位, 材料层) 范围内的可估性结论。"""

    station: str
    layer: str
    cells: dict[str, CellInfo]
    rank: int
    estimable_terms: list[str]
    alias_groups: list[list[str]]
    replicated_terms: list[str]
    note: str


def contrast_weights(present: set[tuple[int, int]], term: str):
    """求现有单元上实现某效果对比的单元权重；不可估时返回 None。

    全因子时采用 2^2 析因惯例的标准 ±0.5 主效应/交互对比；
    部分因子时解 A^T w = e_term（A 为现有单元的去重设计矩阵），
    给出该分配下实际可估的对比系数（最小范数解），
    其含义是"当前单元组合下能识别的最接近效果"，并在报告中注明。
    """
    ordered = sorted(present)
    if not ordered:
        return None
    if len(ordered) == 4:
        return {cell: float(weight) for cell, weight in
                zip(CELLS, CONTRASTS[term])}
    term_index = {"p": 1, "c": 2, "pc": 3}[term]
    unique_matrix = np.vstack([design_matrix(cell) for cell in ordered])
    basis = np.zeros(4)
    basis[term_index] = 1.0
    weights, residuals, *_ = np.linalg.lstsq(
        unique_matrix.T, basis, rcond=None)
    if residuals.size and residuals[0] > 1e-18:
        return None
    if np.linalg.norm(unique_matrix.T @ weights - basis) > 1e-8:
        return None
    return {cell: float(weight) for cell, weight in zip(ordered, weights)}


def assess_estimability(station: str, layer: str,
                        runs: list[RunSummary]) -> Estimability:
    """对一个范围内的 ITT 段做秩检验与复制度判断。"""
    cell_runs: dict[tuple[int, int], list[RunSummary]] = {}
    for item in runs:
        cell_runs.setdefault(item.cell_assign, []).append(item)

    cells: dict[str, CellInfo] = {}
    rows: list[np.ndarray] = []
    for cell in CELLS:
        members = cell_runs.get(cell, [])
        cells[CELL_NAMES[cell]] = CellInfo(
            cell=cell, runs=[m.run_id for m in members],
            n_runs=len(members),
            flagged_runs=[m.run_id for m in members
                          if m.misassigned or m.stopped],
        )
        rows.extend([design_matrix(cell)] * len(members))

    if rows:
        matrix = np.vstack(rows)
        rank = int(np.linalg.matrix_rank(matrix, tol=1e-10))
    else:
        matrix = np.empty((0, 4))
        rank = 0

    estimable: list[str] = []
    if rank:
        for term_index, term in enumerate(TERMS):
            basis = np.zeros(4)
            basis[term_index] = 1.0
            # 列向量落在行空间 <=> 可由现有分配估计
            projection = matrix.T @ np.linalg.pinv(matrix @ matrix.T) @ matrix \
                if matrix.size else np.zeros((4, 4))
            if np.linalg.norm(projection @ basis - basis) < 1e-8:
                if term != "intercept":
                    estimable.append(term)

    # 复制度：效果实际对比涉及的每个单元都至少有 2 个段，才可能给区间。
    replicated: list[str] = []
    present = set(cell_runs)
    for term in ("p", "c", "pc"):
        weights = contrast_weights(present, term)
        if weights is not None and all(
                len(cell_runs.get(cell, ())) >= 2 for cell in weights):
            replicated.append(term)

    non_estimable = [term for term in ("p", "c", "pc")
                     if term not in estimable]
    if len(present) <= 1:
        aliases: list[list[str]] = []
        note = "范围内只有一个设置单元（或无段），没有任何效果对比信息"
    elif non_estimable:
        # 行空间秩不足时，所有不可估项共享零空间方向，构成一个别名组。
        aliases = [non_estimable]
        note = "现有分配存在完全混杂，别名组内各效果无法区分，不单独报效果"
    else:
        aliases = []
        note = "2x2 四个单元均已出现，P、C、P×C 可分别识别"
    return Estimability(
        station=station, layer=layer, cells=cells, rank=rank,
        estimable_terms=estimable, alias_groups=aliases,
        replicated_terms=replicated, note=note,
    )


def all_scopes(bundle) -> dict[tuple[str, str], Estimability]:
    """对每个 (工位, 材料层) 范围输出可估性结论。"""
    summaries = summarize_runs(bundle)
    result = {}
    for scope, runs in sorted(scope_groups(summaries, causal_only=True).items()):
        result[scope] = assess_estimability(*scope, runs)
    return result


def shared_overlap_report(bundle) -> list[dict]:
    """跨 run 母片/同卷关联清单：这些检件不能各算一次独立材料。"""
    summaries = summarize_runs(bundle)
    report = []
    for overlap, run_ids in sorted(bundle.overlap_groups.items()):
        stations = sorted({summaries[rid].station for rid in run_ids
                           if rid in summaries})
        rolls = sorted({summaries[rid].roll for rid in run_ids
                        if rid in summaries})
        report.append({
            "overlap": overlap, "runs": run_ids,
            "stations": stations, "rolls": rolls,
            "cross_station": len(stations) > 1,
        })
    return report
