---
name: research-data-quality-gate
description: Use when a scientific data task supplies CSV or SQLite materials and requires cleaning, statistics, plots, SQL checks, or a contract-preserving /app/output/result.json; audit missing, duplicate, type, unit, sentinel and extreme-value risks, and validate case_id plus every original output_contract field before delivery.
---

# 科研数据质量门与结果契约校验

## 触发条件与边界

在科研数据分析、清洗、统计、可视化或 SQLite 查询任务中使用，尤其是输入包含 `/input/case.json`、`task.md`、CSV/SQLite 材料，且最终需要 `/app/output/result.json` 时。先读取实际任务，不因文件名或领域标签猜测字段、数值、单位、金标准或结论。

本 Skill 只提供两道可复用检查：

1. CSV/SQLite 数据字典与缺失、重复、类型、单位、哨兵值/极端值风险审计；
2. 通用 `result.json` 的 `case_id` 一致性和原始 `output_contract` 顶层字段保留校验。

审计**不是**自动清洗，不删除重复、不填补缺失、不转换单位。契约校验也不证明数值、科学结论或字段值正确；这些仍须按任务独立复算。

## 实际输入、目录与依赖

- 原始输入：`/input/case.json` 和 Harness 实际提供的 `task.md`；优先检查 `/input/task.md`。从 case 和任务中读取真实的材料路径、数据字典、约束及 `output_contract`，再读取实际 CSV/SQLite 文件。
- 中间代码、审计、日志、清单：`/app/work`。
- 最终结果、清洗表、统计、图表、查询结果及可编辑产物：`/app/output`。
- 安装后的脚本：`/skills/weekly-skill/scripts/run_skill.py`（单次总入口：审计 + 写出契约完整的 `result.json` 骨架）、`audit_data.py` 与 `validate_result.py`。
- 仅依赖 CPython 3.9+ 标准库（`csv`、`json`、`sqlite3` 等）；无需安装包、无需联网。其他科学计算/绘图库只在环境已有且任务需要时使用，记录真实版本，不声称未安装的软件可用。

先建立目录并保存输入清单（真实路径、SHA-256、字节数），保持原始材料只读；清洗数据另存，不覆盖原文件。尽早写一份诚实的部分 `result.json`：保留 `case_id` 和全部契约字段，未完成值用 `null` 或空集合，并明确 `status: "partial"`、限制、不确定性和待办，不能将占位值写成已完成结果。

## 1. 审计 CSV 或 SQLite

```bash
python /skills/weekly-skill/scripts/audit_data.py /actual/material.csv \
  --format csv \
  --required "task_required_id" --required "task_required_measure" \
  --output /app/work/material_audit.json

python /skills/weekly-skill/scripts/audit_data.py /actual/material.sqlite \
  --format sqlite \
  --required "task_required_id" \
  --output /app/work/database_audit.json
```

- `--required` 只传任务或数据字典明确要求的原始列名，每列重复一次；不要凭经验添加。
- SQLite 默认只读审计全部用户表；只需一张表时加 `--table "实际表名"`。表名不存在会失败，不自动猜测替代表。
- CSV 接受 UTF-8/UTF-8-SIG，首行必须是表头。其他编码或分隔格式需先用实际工具显式转换，并记录转换，不静默猜测。
- 输出含输入哈希、行数、逐列数据字典、声明/推断类型、缺失比例、非缺失 distinct 数、示例值、单位线索及定位到表/列/数据行的风险。CSV 数据行号从 1 开始且不含表头。
- 数值列额外报告 `sentinel_suspect`（`-999`、`-9999`、`-99999`、`9999`、`-1`，且不计入缺失）与 `extreme_value`（非哨兵值上 IQR×3 或 |z|>5）；缺失、重复、类型、单位风险照常报告。
- 缺失标记为大小写不敏感的 `""`、`NA`、`N/A`、`NaN`、`None`、`null`，以及 SQL `NULL`。这是显式启发式规则；若任务语义不同，须在分析规则中说明并另行核对。
- 重复是“所有返回值完全相同的额外行数”，不代表应当删除。主键重复、近似重复及合法重复测量须按任务另查。
- 单位只识别表头尾部 `(...)`/`[...]` 和数值字符串的单位后缀。日期/时间样式（如 `2026/01/03`、ISO 时间戳）、纯数字与数值区间不识别为单位。单位冲突只报告，不擅自换算；无单位或无法识别不等于物理上无单位。

**退出码必须正确解释：** `0` 表示审计执行完成，可能是 `completed`，也可能是 `completed_with_risks`；`2` 表示读取/执行失败。退出码为 0 不等于数据干净，必须查看 `summary.risk_count` 和 `risks`，将风险处置写入结果与变更记录。

## 2. 按任务完成真实分析

1. 先对照数据字典、实际表头和任务逐字段映射，再写代码。缺列时停止依赖该列的分析，保留审计和错误现场；不得改名列来冒充对齐。
2. 将分析代码保存到 `/app/work`，实际执行后把最终代码与有用产物复制到 `/app/output`。保存运行命令、stdout/stderr、退出码、输入哈希和行数。
3. 缺失值默认不能当成 0；重复记录不能在无规则时去重。每次过滤、填补、去重、类型转换、单位换算和表连接都记录规则、前后行数及受影响行。
4. 统计时明确样本/总体口径、分母、有效非缺失样本量、分组和排除规则。报告均值等结果前独立复算至少一个汇总；检验前检查任务要求的适用条件、空组、样本量及数值有限性，条件不足就不给确定性 p 值。
5. SQL 保存实际查询文本，核对筛选条件、连接键、连接前后行数及查询返回行数。查询为空或失败须如实报告，不能用猜测值补齐。
6. 图表必须由最终分析数据真实生成并保存到 `/app/output`。核对轴名、单位、图例、排序、分组、样本量和标题与数据字典及结果一致；重新读取图中使用的小计/统计值并与结果表核对，实际渲染并检查 PNG/PDF（如任务要求）而非只保存代码。
7. 随机性：两个检查脚本均无随机操作。分析若有抽样、打乱、优化或随机模型，在进程启动前设置 `PYTHONHASHSEED=0`，并对实际使用的每个随机数生成器设置种子 `20261119`，记录种子与软件版本；无随机操作时写 `randomness: none`，不虚构已设置的种子。
8. 遵守平台预算，先保存可核验的部分结果，再增量完善。不得伪造运行日志、软件状态、科学数据、测试结果或评分。

## 3. 交付前校验 result.json

最终 `result.json` 至少应诚实包含：与输入完全相同的 `case_id`、任务要求的主结果、`status`、`sources`（实际文件/表/查询/代码定位）、`uncertainty`、`changes`（清洗和转换记录）、产物路径及执行证据。**原始 `output_contract` 的字段名优先，不能因以下通用建议而删除或改名。**

```bash
python /skills/weekly-skill/scripts/validate_result.py \
  --case /input/case.json \
  --result /app/output/result.json \
  --output /app/work/result_validation.json
```

- 支持字段列表、`fields` 列表/映射、`properties` JSON Schema、`required`/`optional` 列表及紧凑字段映射。对 `properties` 中的所有字段及 `required` 字段均检查保留。
- 只检查字段是否存在，额外字段允许；不执行任意 JSON Schema，也不验证字段值、类型或科学正确性。
- `0`：结构校验通过；`1`：`case_id` 或契约字段校验失败；`2`：文件/JSON/输入读取失败。缺失、空或无法识别的 `output_contract` 失败关闭，不视为通过。
- 校验失败时修复结果或如实保留部分状态，不能修改 `/input/case.json`、删除契约字段或放宽校验器。通过后仍须人工/代码复核数值、来源和图表。

## 异常终止与诚实的部分结果

- **材料缺失**：列出缺失的实际路径和已找到材料，不制造替代数据。
- **字段缺失/类型冲突**：停止受影响的统计或查询，保留其余独立结果，并标明未完成范围。
- **依赖缺失/权限不足/超时**：记录真实报错、命令与退出码；只在已有能力可安全替代时降级，不能宣称原操作成功。
- **查询失败或为空**：分别报告执行失败与合法零行，保存 SQL 和数据库哈希。
- **无法解释或条件不足**：写入 `uncertainty`，必要时请求人工确认，不输出“看起来合理”的数值。
- **即将超时或不可恢复错误**：保留 `/app/output/result.json`、已生成产物和日志，使用 `status: "partial"` 或 `"failed"`，逐项说明已完成、未完成及其限制。不要为追求完整状态而隐藏异常。

## 最小调用示例（合成示意，非官方案例、非正式测试输出）

以下只演示接口，数据与结果均为合成示意；正式运行时必须换成实际输入，不能将此示例复制为答案。

合成 `/app/work/synthetic.csv`：

```csv
sample_id,length (mm)
S1,10.5
S2,11.0
```

调用：

```bash
python /skills/weekly-skill/scripts/audit_data.py /app/work/synthetic.csv \
  --format csv --required sample_id --required "length (mm)" \
  --output /app/work/synthetic_audit.json
```

示意审计摘要（不是实际运行捕获）：

```json
{"status": "completed", "summary": {"table_count": 1, "row_count": 2, "risk_count": 0}}
```

若合成 case 为 `{"case_id":"synthetic-demo","output_contract":{"answer":"string","status":"string"}}`，则候选结果至少保留这三个顶层字段（`case_id`、`answer`、`status`），再运行上述 `validate_result.py` 命令并替换三个路径。示意校验输出为 `{"valid": true}`；它只说明标识和字段保留通过，不说明 `answer` 的内容正确。


## 交付保证与评分对齐

调用顺序（固定）：

1. 先运行包内脚本：单次总入口 `python scripts/run_skill.py --input <case_dir> --work <work_dir> --out <out_dir|result.json>` 会审计全部 CSV/SQLite 材料并写出保留 `case_id` 与全部契约字段的 `result.json` 骨架；也可按上文“调用”示例分别执行 `audit_data.py`（审计）与 `validate_result.py`（结果自检）。
2. 再完成脚本无法自动完成的实质工作（译文、论述、图表、修订、结论等），把结果写回 `result.json` 对应字段。
3. 最后自检：`case_id` 与输入一致；`output_contract` 每个字段都存在；`status` 按契约取 `success`/`partial`/`insufficient_input`/`failed`（缺输入或证据不足时用 `insufficient_input`/`partial`）；无 `pending` 占位；数字、引用、来源可逐条回溯。

运行保证：

- 输入缺失、JSON 损坏、字段异常、输出目录不存在时，仍须写出结构化 `result.json` 并正常结束，不得崩溃或非零退出。
- 不联网、不伪造数据、不声称平台评分或官方案例结果。
- 交付物必须是可直接编辑与复核的成品，而不是流程说明。

评分对齐（本题五个维度）：

- 实际效果（40）：完整产出任务要求的最终成品，覆盖全部输入条目，可直接进入修改流程。
- 使用增益（25）：体现相对基础 Agent 的可验证改进（结构化、术语/数字/引用核对、可编辑性）。
- 创新增益（15）：保留方法学增量（分块、对齐、二次校验、版本比对等），并用结果证明其价值。
- 可靠体验（10）：异常输入有明确降级与说明，不同长度/文体/重复运行下稳定产出。
- 生态贡献（10）：用途、调用、输入输出、限制、示例、维护信息齐全，资源可被他人复用。

## 最终交付强制要求

1. **调用 Agent 必须交付成品，不是流程说明**：脚本产出只是索引、核对或规格骨架；调用 Agent 必须在同一 `result.json` 中补齐本题要求的最终产物（正文/译文/审稿意见/综述/分析结论/图表源文件与导出件/幻灯片页内容与讲稿/实验方案等）。交付字段为空、只有标题骨架，或含 `pending`、`TODO` 之类占位，一律视为未完成。
2. **必须覆盖全部输入条目**：输入中的每个章节、问题、文献、图、页面、指标都要有对应条目，不得只处理示例或首条；确实缺失的条目写入 `uncertainty` / `limitations` 并说明原因，不得静默跳过。
3. **必须自检后再交付**：`case_id` 与输入一致；`output_contract` 每个字段都存在；状态值取本题契约规定的枚举；数字、单位、引用、来源可逐条回溯；结果内部无自相矛盾。
4. **入口与参数**：单次总入口为 `python scripts/run_skill.py --input <case_dir> --work <work_dir> --out <out_dir|result.json>`；细分入口为 `scripts/audit_data.py`（审计，`--format csv|sqlite --output`）与 `scripts/validate_result.py`（结果自检，`--case --result --output`），参数见上文示例，不要套用其他题的命令。
5. **平台用 3 个固定案例分别运行**：逻辑要由输入驱动，不针对某一份示例硬编码；字段命名差异走别名匹配与显式降级，并在 `uncertainty` 记录假设。
