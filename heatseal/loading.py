"""交换资料的读入与校验。

只做结构、取值和引用层面的检查，业务判据放在后续模块。
登记表(register)合并新批次时复用同一套校验，保证每轮提交格式一致。
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parents[1] / "sources.json"

# 各数值字段允许的取值/类型，空字符串按"未填"处理而非零。
_INT_FIELDS = {
    "runs": {"p_assign", "c_assign", "p_actual", "c_actual", "stopped"},
    "slots": {"minute", "duration", "p", "c", "maintain", "locked"},
    "rolls": {"available", "reserved", "quantity"},
    "results": {"scrap"},
}
_FLOAT_FIELDS = {"results": {"seal"}}
_BINARY = {"p_assign", "c_assign", "p_actual", "c_actual", "stopped",
           "p", "c", "maintain", "locked", "scrap"}


@dataclass
class Bundle:
    """一次交付批或当前资料库的完整快照。"""

    runs: list[dict]
    slots: list[dict]
    rolls: list[dict]
    results: list[dict]
    rules: list[dict]
    source: str = ""
    overlap_groups: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _read_table(directory: Path, name: str, columns: list[str]) -> list[dict]:
    path = directory / f"{name}.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != columns:
            raise ValueError(f"{name} 的表头与交换约定不同")
        rows = list(reader)
    for line, row in enumerate(rows, 2):
        if None in row or None in row.values():
            raise ValueError(f"{name} 第 {line} 行的字段数不匹配")
    return rows


def _coerce(tables: dict[str, list[dict]]) -> None:
    for table, rows in tables.items():
        for line, row in enumerate(rows, 2):
            for column in _INT_FIELDS.get(table, ()):
                try:
                    row[column] = int(row[column])
                except ValueError as exc:
                    raise ValueError(
                        f"{table} 第 {line} 行 {column} 不是整数") from exc
                if column in _BINARY and row[column] not in (0, 1):
                    raise ValueError(
                        f"{table} 第 {line} 行 {column} 只允许 0/1")
            for column in _FLOAT_FIELDS.get(table, ()):
                try:
                    row[column] = float(row[column])
                except ValueError as exc:
                    raise ValueError(
                        f"{table} 第 {line} 行 {column} 不是数值") from exc
                if row[column] <= 0:
                    raise ValueError(
                        f"{table} 第 {line} 行 {column} 必须为正数")
    for line, row in enumerate(tables["rolls"], 2):
        if row["reserved"] > row["available"]:
            raise ValueError(f"rolls 第 {line} 行保留量超过可用量")
        if row["quantity"] <= 0:
            raise ValueError(f"rolls 第 {line} 行每件用量必须为正")


def _check_identities(tables: dict[str, list[dict]], config: dict) -> None:
    for name, spec in config["tables"].items():
        seen: set[tuple] = set()
        for line, row in enumerate(tables[name], 2):
            key = tuple(row[column] for column in spec["key"])
            if not all(key) or key in seen:
                raise ValueError(f"{name} 第 {line} 行的标识为空或重复")
            seen.add(key)


def _check_links(tables: dict[str, list[dict]], config: dict,
                 allow_partial: bool = False) -> None:
    for source, column, target, target_column in config["links"]:
        known = {row[target_column] for row in tables[target]}
        # 部分批次允许主表（rolls/rules）缺失，此时只跳过相关引用检查，
        # 合并后会对完整快照重新校验。
        if allow_partial and not known:
            continue
        for line, row in enumerate(tables[source], 2):
            value = row[column]
            if value not in (None, "") and value not in known:
                raise ValueError(
                    f"{source} 第 {line} 行: {column} 引用不存在")


def _check_business_refs(tables: dict[str, list[dict]],
                         allow_partial: bool = False) -> list[str]:
    warnings: list[str] = []
    run_ids = {row["id"] for row in tables["runs"]}
    # 注意：runs.roll 是生产卷标识，rolls.csv 只登记认证材料池(slots.roll)，
    # 两者命名空间不同，sources.json 也未建立该引用，故不做跨表校验。
    roll_ids = {row["roll"] for row in tables["rolls"]}

    run_pieces: dict[str, set[str]] = {}
    for line, result in enumerate(tables["results"], 2):
        if result["run"] not in run_ids:
            raise ValueError(f"results 第 {line} 行引用了不存在的 run")
        pieces = run_pieces.setdefault(result["run"], set())
        if result["overlap"] in pieces:
            raise ValueError(
                f"results 第 {line} 行母片 {result['overlap']} 在同一 run 内重复")
        pieces.add(result["overlap"])

    slot_ids = {row["id"] for row in tables["slots"]}
    for line, slot in enumerate(tables["slots"], 2):
        if slot["after"] and slot["after"] not in slot_ids:
            raise ValueError(f"slots 第 {line} 行 after 引用不存在")
        if not allow_partial and slot["roll"] not in roll_ids:
            raise ValueError(f"slots 第 {line} 行引用了不存在的卷")

    for run in tables["runs"]:
        if not any(result["run"] == run["id"] for result in tables["results"]):
            warnings.append(f"run {run['id']} 没有任何检件结果")
    # 同一母片跨 run（含跨工位）出现时记录，供 design 模块关联。
    owners: dict[str, list[str]] = {}
    for result in tables["results"]:
        owners.setdefault(result["overlap"], []).append(result["run"])
    return warnings


def find_overlap_groups(tables: dict[str, list[dict]]) -> dict[str, list[str]]:
    """返回出现在多个 run 中的母片及其所属 run（含跨工位同卷）。"""
    owners: dict[str, set[str]] = {}
    for result in tables["results"]:
        owners.setdefault(result["overlap"], set()).add(result["run"])
    return {overlap: sorted(run_ids)
            for overlap, run_ids in sorted(owners.items())
            if len(run_ids) > 1}


def load_bundle(directory: str | Path, config_path: str | Path | None = None,
                partial: bool = False) -> Bundle:
    """读入一个资料目录并完成全部结构校验。

    partial=True 用于新交付批次：允许 rolls/rules 等主表为空，
    引用检查放宽，合并进完整快照后再执行一次全量校验。
    """
    directory = Path(directory)
    config = json.loads(Path(config_path or CONFIG_PATH).read_text(encoding="utf-8"))
    tables = {name: _read_table(directory, name, spec["columns"])
              for name, spec in config["tables"].items()}
    _coerce(tables)
    _check_identities(tables, config)
    _check_links(tables, config, allow_partial=partial)
    warnings = _check_business_refs(tables, allow_partial=partial)
    return Bundle(
        runs=tables["runs"], slots=tables["slots"], rolls=tables["rolls"],
        results=tables["results"], rules=tables["rules"],
        source=str(directory),
        overlap_groups=find_overlap_groups(tables), warnings=warnings,
    )


def merge_bundles(base: Bundle, incoming: Bundle) -> Bundle:
    """把新批次登记合并进现有快照；重复身份一律拒绝。

    合并后重新执行完整结构与引用校验，避免新批次悄悄破坏既有约定。
    新批次允许只带部分表：runs/results/slots 追加，rolls/rules 以键为准更新。
    """
    for table_name, key_name in (("runs", "id"), ("slots", "id"),
                                 ("results", "id")):
        existing = {row[key_name] for row in getattr(base, table_name)}
        for row in getattr(incoming, table_name):
            if row[key_name] in existing:
                raise ValueError(
                    f"新批次 {table_name} 标识 {row[key_name]} 已存在，拒绝重复登记")

    roll_known = {row["roll"] for row in base.rolls}
    for row in incoming.rolls:
        if row["roll"] in roll_known:
            raise ValueError(
                f"新批次 rolls 标识 {row['roll']} 已存在，资源上限只能整表替换")

    rule_known = {row["layer"] for row in base.rules}
    for row in incoming.rules:
        if row["layer"] in rule_known:
            raise ValueError(
                f"新批次 rules 材料层 {row['layer']} 已存在，备案目标只能整表替换")

    merged = Bundle(
        runs=base.runs + incoming.runs,
        slots=base.slots + incoming.slots,
        rolls=base.rolls + incoming.rolls,
        results=base.results + incoming.results,
        rules=base.rules + incoming.rules,
        source=f"{base.source} + {incoming.source or '新批次'}",
    )
    # 重新校验引用与母片关联（此时是完整快照，不允许 partial）。
    tables = {"runs": merged.runs, "slots": merged.slots,
              "rolls": merged.rolls, "results": merged.results,
              "rules": merged.rules}
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    _check_identities(tables, config)
    _check_links(tables, config)
    merged.warnings = _check_business_refs(tables)
    merged.overlap_groups = find_overlap_groups(tables)
    return merged


def write_bundle(bundle: Bundle, directory: str | Path) -> None:
    """把快照按交换约定写回 CSV，供下一轮继续登记。"""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    for name, spec in config["tables"].items():
        with (directory / f"{name}.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=spec["columns"])
            writer.writeheader()
            for row in getattr(bundle, name):
                writer.writerow(row)
