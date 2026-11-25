"""候选停机段的可行性、证据价值分级与草案比较。

资源约束（同一资源同一时刻不可占两次，不能按单场收益重复占用）：

1. ``maintain=1`` 的窗口保留给正常订单，不进入试验；
2. ``locked=1`` 的窗口是已锁定试验，资源占用照常扣减，不会因新数据消失，
   也不能当作已经观测到的对照；
3. 班组(crew)与卷料(roll)在时间重叠的窗口间互斥；
4. ``after`` 要求前置窗口在同草案内被选中（或已锁定）且先完成；
5. 每卷按 quantity 扣减，用量上限 = available - reserved。

证据价值分级（按当前 ITT 证据，对每个可行窗口单独判断）：

- ``拆分混杂``：该窗口的设置落在当前缺失单元上，加入后能让至少一个
  完全混杂/不可估的效果变为可估；
- ``仅提高精度``：单元已存在，加入只增加复制、缩短区间，不改变可估性；
- ``不改变决策``：单元已有充分复制，或加入后仍不足以让任何门槛翻案；
- 不可行窗口单独列出阻断原因，不参与草案比较。
"""
from __future__ import annotations

from dataclasses import dataclass

from .design import (CELL_NAMES, all_scopes, design_matrix, scope_groups,
                     summarize_runs)
import numpy as np

MAINTAIN = "maintain"


@dataclass
class Feasibility:
    slot_id: str
    feasible: bool
    blockers: list[str]
    used_crew: list[str]
    used_roll: str
    roll_debit: int


def _overlaps(minute_a: int, duration_a: int, minute_b: int, duration_b: int) -> bool:
    return minute_a < minute_b + duration_b and minute_b < minute_a + duration_a


def _roll_budget(bundle) -> dict[str, dict]:
    return {
        row["roll"]: {
            "cap": int(row["available"]) - int(row["reserved"]),
            "quantity": int(row["quantity"]),
            "spent": 0,
        }
        for row in bundle.rolls
    }


def _check_window(slot: dict, selected: list[dict], locked: list[dict],
                  budgets: dict[str, dict]) -> list[str]:
    blockers: list[str] = []
    if int(slot[MAINTAIN]) == 1:
        blockers.append("maintain=1，窗口保留给正常订单，不进入试验")
        return blockers

    others = selected + locked
    for other in others:
        if other["id"] == slot["id"]:
            continue
        if other["crew"] == slot["crew"] and _overlaps(
                int(slot["minute"]), int(slot["duration"]),
                int(other["minute"]), int(other["duration"])):
            blockers.append(
                f"班组 {slot['crew']} 与窗口 {other['id']} 时间重叠")
        if other["roll"] == slot["roll"] and _overlaps(
                int(slot["minute"]), int(slot["duration"]),
                int(other["minute"]), int(other["duration"])):
            blockers.append(
                f"卷料 {slot['roll']} 与窗口 {other['id']} 时间重叠")

    if slot.get("after"):
        prior_id = slot["after"]
        chosen = {row["id"] for row in selected + locked}
        if prior_id not in chosen:
            blockers.append(f"前置窗口 {prior_id} 未在草案中选中或锁定")
        else:
            prior = next(row for row in others if row["id"] == prior_id)
            if int(prior["minute"]) + int(prior["duration"]) > int(slot["minute"]):
                blockers.append(f"前置窗口 {prior_id} 未能在本窗口开始前完成")

    budget = budgets.get(slot["roll"])
    if budget is None:
        blockers.append(f"卷料 {slot['roll']} 不在 rolls 备案中")
    elif budget["spent"] + budget["quantity"] > budget["cap"]:
        blockers.append(
            f"卷料 {slot['roll']} 预算不足：已用 {budget['spent']}，"
            f"本窗口需 {budget['quantity']}，上限 {budget['cap']}")
    return blockers


def evaluate_feasibility(bundle, chosen_ids: list[str] | None = None) -> dict:
    """评估一组候选窗口（连同锁定窗）的可行性与资源占用。"""
    chosen_ids = chosen_ids or []
    locked = [row for row in bundle.slots if int(row["locked"]) == 1]
    maintain = [row["id"] for row in bundle.slots if int(row[MAINTAIN]) == 1]
    selected = [row for row in bundle.slots if row["id"] in chosen_ids]
    missing = set(chosen_ids) - {row["id"] for row in bundle.slots}
    if missing:
        raise ValueError(f"草案引用了不存在的窗口: {sorted(missing)}")

    budgets = _roll_budget(bundle)
    # 锁定窗口先扣预算，且其占用对所有草案生效。
    for slot in locked:
        budgets[slot["roll"]]["spent"] += budgets[slot["roll"]]["quantity"]

    findings = []
    for slot in selected:
        blockers = _check_window(slot, selected, locked, budgets)
        if not blockers:
            budgets[slot["roll"]]["spent"] += budgets[slot["roll"]]["quantity"]
        findings.append(Feasibility(
            slot_id=slot["id"], feasible=not blockers, blockers=blockers,
            used_crew=[slot["crew"]], used_roll=slot["roll"],
            roll_debit=budgets.get(slot["roll"], {"quantity": 0})["quantity"],
        ))

    roll_status = {roll: {"cap": info["cap"], "spent": info["spent"],
                          "remaining": info["cap"] - info["spent"]}
                   for roll, info in budgets.items()}
    return {
        "draft_ids": chosen_ids,
        "locked_ids": [row["id"] for row in locked],
        "maintain_ids": maintain,
        "feasible": all(item.feasible for item in findings),
        "windows": [item.__dict__ for item in findings],
        "roll_status": roll_status,
    }


def _present_cells(bundle, station: str, layer: str) -> set[tuple[int, int]]:
    summaries = summarize_runs(bundle)
    groups = scope_groups(summaries, causal_only=True)
    runs = groups.get((station, layer), [])
    return {run.cell_assign for run in runs}


def _terms_recovered(present: set[tuple[int, int]],
                     added: tuple[int, int]) -> list[str]:
    """加入 added 单元后，新变为可估的非截距项。"""
    before = _estimable_terms(present)
    after = _estimable_terms(present | {added})
    return sorted(after - before)


def _estimable_terms(cells: set[tuple[int, int]]) -> set[str]:
    if not cells:
        return set()
    matrix = np.vstack([design_matrix(cell) for cell in cells])
    rank_terms = set()
    for index, term in enumerate(("intercept", "p", "c", "pc")):
        basis = np.zeros(4)
        basis[index] = 1.0
        projection = matrix.T @ np.linalg.pinv(matrix @ matrix.T) @ matrix
        if np.linalg.norm(projection @ basis - basis) < 1e-8:
            rank_terms.add(term)
    rank_terms.discard("intercept")
    return rank_terms


def classify_slot_value(bundle) -> list[dict]:
    """对每个非保留、非锁定候选窗口给证据价值分级（单独加入视角）。"""
    scopes = all_scopes(bundle)
    output = []
    for slot in bundle.slots:
        if int(slot[MAINTAIN]) == 1:
            output.append({"slot": slot["id"], "value": "不进入试验",
                           "reason": "maintain=1 保留窗口"})
            continue
        if int(slot["locked"]) == 1:
            output.append({"slot": slot["id"], "value": "已锁定",
                           "reason": "锁定试验的占用与结果不可撤销，不作为待决窗口"})
            continue

        station, layer = slot["station"], slot["layer"]
        added = (int(slot["p"]), int(slot["c"]))
        present = _present_cells(bundle, station, layer)
        estimability = scopes.get((station, layer))
        recovered = _terms_recovered(present, added)

        feasibility = evaluate_feasibility(bundle, [slot["id"]])
        window_result = next(item for item in feasibility["windows"]
                             if item["slot_id"] == slot["id"])
        hard_blockers = [text for text in window_result["blockers"]
                         if "前置窗口" not in text]
        prereq_blockers = [text for text in window_result["blockers"]
                           if "前置窗口" in text]
        if hard_blockers:
            value = "不可行"
            reason = "；".join(hard_blockers)
        elif recovered:
            value = "拆分混杂"
            reason = (f"当前单元 {sorted(CELL_NAMES[c] for c in present)} 中 "
                      f"{', '.join(recovered)} 不可估；加入 {CELL_NAMES[added]} 后可识别")
        else:
            # 单元已存在：检查复制是否仍受限。
            cell_info = estimability.cells[CELL_NAMES[added]] if estimability else None
            if cell_info and cell_info.n_runs >= 2 and estimability.replicated_terms:
                value = "不改变决策"
                reason = "对应单元已有 2 个以上段，追加只增加冗余复制"
            else:
                value = "仅提高精度"
                reason = (f"单元 {CELL_NAMES[added]} 已存在但复制不足，"
                          "追加段可缩短区间，不改变可估性")
        if prereq_blockers:
            reason += "；注意：" + "；".join(prereq_blockers) + \
                "（仅在前置窗口同草案选中时成立，本身不是硬资源阻断）"
        output.append({
            "slot": slot["id"], "station": station, "layer": layer,
            "setting": CELL_NAMES[added], "value": value, "reason": reason,
            "requires_after": slot.get("after") or None,
            "feasibility_blockers": window_result["blockers"],
        })
    return output


def enumerate_feasible_drafts(bundle) -> list[dict]:
    """枚举非锁定窗口的可行草案（预算/互斥/先后约束下的子集）。

    规模受候选窗口数限制（演练数据为个位数），直接枚举并按价值汇总。
    """
    open_slots = [row for row in bundle.slots
                  if int(row[MAINTAIN]) == 0 and int(row["locked"]) == 0]
    value_lookup = {item["slot"]: item for item in classify_slot_value(bundle)}
    drafts: list[dict] = []
    for mask in range(1, 1 << len(open_slots)):
        ids = [slot["id"] for index, slot in enumerate(open_slots)
               if mask & (1 << index)]
        feasibility = evaluate_feasibility(bundle, ids)
        if not feasibility["feasible"]:
            continue
        values = [value_lookup[slot_id]["value"] for slot_id in ids]
        drafts.append({
            "draft": ids,
            "splits_alias": values.count("拆分混杂"),
            "precision_only": values.count("仅提高精度"),
            "no_decision_change": values.count("不改变决策"),
            "roll_status": feasibility["roll_status"],
        })
    drafts.sort(key=lambda item: (-item["splits_alias"], -item["precision_only"],
                                  item["draft"]))
    return drafts


def explain_change(bundle, target: str, station: str | None = None,
                   layer: str | None = None) -> dict:
    """查出本轮为什么允许某个更改，或为什么仍不能承诺。"""
    from .decisions import committable_changes

    decisions = committable_changes(bundle)
    match = [item for item in decisions
             if (target in item["target"] or target == item["target"])
             and (station is None or item["station"] == station)
             and (layer is None or item["layer"] == layer)]
    if not match:
        raise ValueError(
            f"未找到目标更改: {target}"
            + (f"（工位 {station}）" if station else "")
            + (f"（材料层 {layer}）" if layer else ""))
    if len(match) > 1:
        candidates = ", ".join(
            f"{item['station']}/{item['layer']}/{item['target']}" for item in match)
        raise ValueError(f"目标 {target} 命中多个范围，请用工位和材料层消歧: {candidates}")
    item = match[0]
    allowed = item["can_change_production"]
    return {
        "target": item["target"], "station": item["station"],
        "layer": item["layer"],
        "allowed_now": allowed,
        "improvement_gate": item["improvement"],
        "no_degradation_gate": item["no_degradation"],
        "limiting_evidence": item["limiting_evidence"],
        "explanation": (
            "改善门与劣化门均通过，可按对应适用范围更改生产参数"
            if allowed else
            "当前证据不满足合取门槛或效果不可单报；见 limiting_evidence 中的区间与缺口"),
    }
