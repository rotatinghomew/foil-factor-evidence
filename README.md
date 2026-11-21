# 热封参数试验资料交换

`data/` 收录供演练使用的合成记录，不含真实企业或人员资料。资料使用 UTF-8 逗号分隔，首行为字段名，带逗号的单值用双引号括起。日期采用 ISO 表示，空字段表示来源未填或不适用，不代表数值零。`sources.json` 记录各表列名、身份键和可直接核对的引用。

## 业务资料

- `runs.csv`：指派段和实跑设定，压力 P、节拍 C 的水平是低 0、高 1。段用于记录最粗粒度设定，assignment 区分随机和试跑，stage 标示用窗。
- `slots.csv`：候选停机段，crew 与 roll 共用；同一资源同一时刻不可占两次。保留 maintain=1 的窗口不进入试验。
- `rolls.csv`：试验材料上限与正常生产保留量，单位为校验条，每个时段使用 quantity。
- `results.csv`：逐检件成效，seal 为剥离强度牛顿数；scrap 为该检件是否报废，overlap 指取样母片。
- `rules.csv`：按材料组备案的目标，剥离强度以牛顿计，废耗为每百件数量。容忍劣化是相对当前 00 条件。

随机安排发生在段的设定层级。同一母片的检件不能当作独立材料重复。initial 与 followup 是两个交付批，followup 用于模拟后续新数据；停机时点在当天本地时钟，after 表示前一段必须已完成。每段用料按卷件数量扣减；废耗上限以每百件的比例点计。

## 本地核对

需要 Python 3.11。分析环境可使用 `python3 -m pip install -r requirements.txt` 安装锁定的数值依赖。

从仓库根目录执行 `python3 check_sources.py`，返回各表行数；其他同格式资料可用 `python3 check_sources.py 路径` 检查。脚本只读资料并报告结构或引用错误，不给出业务结论。执行 `python3 -m unittest -v` 检查资料约定及拒绝重复身份的行为。
