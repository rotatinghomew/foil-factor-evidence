# 热封参数试验资料交换

`data/` 收录供演练使用的合成记录，不含真实企业或人员资料。资料使用 UTF-8 逗号分隔，首行为字段名，带逗号的单值用双引号括起。日期采用 ISO 表示，空字段表示来源未填或不适用，不代表数值零。`sources.json` 记录各表列名、身份键和可直接核对的引用。

## 业务资料

- `runs.csv`：指派段和实跑设定，压力 P、节拍 C 的水平是低 0、高 1。段用于记录最粗粒度设定，assignment 区分随机和试跑，stage 标示用窗。
- `slots.csv`：候选停机段，crew 与 roll 共用；同一资源同一时刻不可占两次。保留 maintain=1 的窗口不进入试验。
- `rolls.csv`：试验材料上限与正常生产保留量，单位为校验条，每个时段使用 quantity。
- `results.csv`：逐检件成效，seal 为剥离强度牛顿数；scrap 为该检件是否报废，overlap 指取样母片。
- `rules.csv`：按材料组备案的目标，剥离强度以牛顿计，废耗为每百件数量。容忍劣化是相对当前 00 条件。

随机安排发生在段的设定层级。同一母片的检件不能当作独立材料重复。initial 与 followup 是两个交付批，followup 用于模拟后续新数据；停机时点在当天本地时钟，after 表示前一段必须已完成。每段用料按卷件数量扣减；废耗上限以每百件的比例点计。

## 分析纪律（heatseal 包）

- **独立单位是段**：随机指派发生在整段设定上，段内检件只折叠为段均值与段废耗率，不增加自由度。同一 `overlap` 母片跨段（含上游同卷跨工位）在 `analyze` 的 `shared_overlaps` 中列出，仅作关联提示，不重复计数。
- **两套视图永不混用**：ITT 视图按 `p_assign/c_assign`，换错设置（E04）与中途停机（E06）保留在指派组并打标，试跑（E09）不进因果视图；描述视图按实际设置、可含试跑，但明确标注不能作为更改生产参数的证据。
- **可估性先行**：对每个（工位, 材料层）用 2x2 设计矩阵判秩，给出可估项、别名组与复制项。完全混杂的效果不单独报效果，只报联合改动（11 vs 00）。
- **区间以段为单位**：Welch 双样本 t / 线性对比的 Satterthwaite 区间；单段单元只给点估计、不给区间，缺单元的效果给 `null` 而非 NaN。
- **双门槛合取、互不补偿**：改善门（强度区间下界 ≥ `min_seal_gain` 且废耗区间上界 ≤ `max_scrap_increase`）与劣化门（强度下界 ≥ 0 且同一废耗上限）分别裁决；区间缺失一律"证据不足"，既不通过也不否定。
- **排程资源不重复占用**：`maintain=1` 不进试验；`locked=1` 先扣班组/卷料预算且不可撤销、不当对照；班组与卷料在时间重叠窗互斥；`after` 要求前置窗同草案选中并先完成；每卷预算 = `available - reserved`。窗口按"拆分混杂 / 仅提高精度 / 不改变决策 / 不可行"分级，再枚举可行草案比较。
- **新批次重判**：`register` 合并新交付批（重复身份拒绝），从合并后的实际支撑重新裁决待决参数与未锁窗口；已锁定试验不消失。

## 本地核对

需要 Python 3.11。分析环境可使用 `python3 -m pip install -r requirements.txt` 安装锁定的数值依赖。

从仓库根目录执行 `python3 check_sources.py`，返回各表行数；其他同格式资料可用 `python3 check_sources.py 路径` 检查。脚本只读资料并报告结构或引用错误，不给出业务结论。执行 `python3 -m unittest discover -s tests -v` 检查资料约定、拒绝重复身份的行为以及分析、门槛、排程、登记逻辑。

## 常用命令

```
python3 -m heatseal analyze                 # 两套视图、可估性、区间、双门槛完整报告
python3 -m heatseal decide                  # 扁平化的参数更改资格清单
python3 -m heatseal plan                    # 窗口证据价值分级与可行草案枚举
python3 -m heatseal plan --draft W02 W04    # 评估指定草案的资源占用与可行性
python3 -m heatseal explain 压力 --station S --layer thick   # 查某更改为何可/不可承诺
python3 -m heatseal register --incoming 新批次目录 --out 合并目录  # 登记并重判
```

输出为 UTF-8 JSON。`register` 的新批次目录可只含追加的 runs/results/slots（主表 rolls/rules 不随批复制）；不带 `--out` 时只打印重判结果不落盘。
