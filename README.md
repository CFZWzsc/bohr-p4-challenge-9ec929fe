# research-data-quality-gate

交付前质量门禁：对 CSV/SQLite 材料做缺失、重复、类型、单位、哨兵值与极值审计，并校验 case_id 与全部 output_contract 字段。

## 安装与运行

- 依赖：Python 3.9+ 标准库，**无第三方依赖**，无需联网。
- 包内自带合成演示案例（`examples/demo-case/`），可直接跑通：

```powershell
python scripts/run_skill.py --case examples/demo-case/case.json --out "$env:TEMP\demo\result.json" --work "$env:TEMP\demo\work"
```

- `--out` 与 `--output` 等价；默认读 `/input/case.json`，写 `/app/output/result.json`。

## 输入

- `case.json`：`case_id`、`output_contract`，以及可选的输入定位键（`input_dir` / `materials` / `files` / `path`）。
- 材料：规则见 `SKILL.md` 的「输入」一节。

## 输出

- result.json
- audit_*.json（逐文件审计）
- risks/sources（内嵌）

同时由 `scripts/report_kit.py` 产出交付自检件：`report.md`、`provenance.json`（输入 sha256/编码/分隔符/参数）、`repro_manifest.json`、`consistency_check`（写入 result.json），以及 `inventory.tsv`（当输入含 CSV/TSV 表时）。

## 保证

- **单次运行完成交付**：不存在两阶段或 `judgement_pending` 状态。
- **确定性**：同一输入连续两次运行，`result.json` 字节一致；无时间戳/随机数进入产物。
- **异常不崩溃**：case.json 损坏、空表、仅表头、非 UTF-8（GB18030）等输入均写出结构化 `result.json` 并以 0 退出，失败以 `status` 字段表达。
- **自检**：- case.json 不可解析 → status=failed 且仍写 result.json（exit 0）
- sources 的 sha256/行数/列数直接来自审计结果

## 限制

- 只做只读审计，不修改源文件；不执行任意 JSON Schema 值校验
- 必需列未声明时跳过该项检查，不猜测

## 可复用性

- `scripts/run_skill.py` 为入口，`scripts/report_kit.py` 为通用交付自检模块，可独立复用到同类数据分析包。
- 维护信息：接口与契约见 `SKILL.md`，评分维度映射见 `RUBRIC.md`，仓库地址见 `GITHUB_URL.txt`。
