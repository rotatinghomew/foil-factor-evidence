"""热封工位压力/节拍试验的登记、判据与排程分析包。

模块划分：

- ``loading``：按 ``sources.json`` 交换约定读入并校验资料；
- ``design``：段级汇总、母片关联识别、2x2 设计的可估性/混杂判断；
- ``inference``：以段为独立单位的区间估计（Welch 双样本 t）；
- ``decisions``：改善/劣化双门槛决策，强度与废耗合取、互不补偿；
- ``planning``：候选停机段的可行性、证据价值分级与草案比较；
- ``cli``：命令行入口。
"""

__all__ = ["loading", "design", "inference", "decisions", "planning", "cli"]
__version__ = "1.0.0"
