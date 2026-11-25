"""命令行入口：每轮提交记录、比较安排、查询更改资格。

子命令：

- analyze：输出 ITT/描述两套视图、可估性、区间与双门槛结论；
- decide：扁平化参数更改资格清单；
- plan：候选窗口分级与可行草案比较；
- explain：查询某个目标更改本轮被允许或不能承诺的原因；
- register：把新批次目录合并进资料库并重判待决参数与未锁窗口。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .decisions import analyze_bundle, committable_changes
from .loading import load_bundle, merge_bundles, write_bundle
from .planning import (classify_slot_value, enumerate_feasible_drafts,
                       evaluate_feasibility, explain_change)


def _print(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _add_data_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data", type=Path, default=Path("data"),
                        help="资料目录，默认 data")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="heatseal",
        description="热封工位压力/节拍试验登记、判据与排程工具")
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser("analyze", help="完整分析报告")
    _add_data_arg(analyze)

    decide = sub.add_parser("decide", help="参数更改资格清单")
    _add_data_arg(decide)

    plan = sub.add_parser("plan", help="候选窗口分级与可行草案")
    _add_data_arg(plan)
    plan.add_argument("--draft", nargs="*", default=None,
                      help="指定草案包含的窗口 id，如 W02 W04")

    explain = sub.add_parser("explain", help="查询某更改能否承诺及原因")
    _add_data_arg(explain)
    explain.add_argument("target", help="目标效果，如 压力 / 节拍 / 联合")
    explain.add_argument("--station", default=None, help="工位，如 N / S")
    explain.add_argument("--layer", default=None, help="材料层，如 thin / thick")

    register = sub.add_parser("register", help="登记新批次并重判")
    register.add_argument("--data", type=Path, default=Path("data"),
                          help="现有资料目录")
    register.add_argument("--incoming", type=Path, required=True,
                          help="新批次资料目录")
    register.add_argument("--out", type=Path, default=None,
                          help="合并后写出目录；缺省只打印重判结果不落盘")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        bundle = load_bundle(args.data)
        if args.command == "analyze":
            _print(analyze_bundle(bundle))
        elif args.command == "decide":
            _print(committable_changes(bundle))
        elif args.command == "plan":
            payload = {
                "slot_values": classify_slot_value(bundle),
                "feasible_drafts": enumerate_feasible_drafts(bundle),
            }
            if args.draft is not None:
                payload["selected_draft"] = evaluate_feasibility(bundle, args.draft)
            _print(payload)
        elif args.command == "explain":
            _print(explain_change(bundle, args.target, args.station, args.layer))
        elif args.command == "register":
            incoming = load_bundle(args.incoming, partial=True)
            merged = merge_bundles(bundle, incoming)
            payload = {
                "merged_source": merged.source,
                "warnings": merged.warnings,
                "redecide": committable_changes(merged),
                "slot_values_after": classify_slot_value(merged),
            }
            if args.out is not None:
                write_bundle(merged, args.out)
                payload["written_to"] = str(args.out)
            _print(payload)
    except (ValueError, OSError) as error:
        parser.exit(2, f"错误：{error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
