# RUBRIC — 科研代码生成与执行（`challenge-9ec929fe`）

公开作品：https://github.com/CFZWzsc/bohr-p4-challenge-9ec929fe

本题按组织方评分表打分（实际效果 40 / 使用增益 25 / 创新增益 15 / 可靠体验 10 / 生态贡献 10；
GitHub 公开链接另加最多 5 分）。下表把每一行映射到本包内实际交付的文件或 `result.json` 字段，
**没有覆盖的直接写「未覆盖」，不做自述式加分**。

| 评分维度 | 分值 | 本包对应产物 | 状态 |
|---|---|---|---|

| 实际效果 | 40 | `result.json` 的代码、执行输出、复算结果与 `validate_result.py` 校验 | 覆盖 |
| 使用增益 | 25 | `audit_data.py` 数据审计 + 结果复算一致性 | 部分（无外部 baseline 对照） |
| 创新增益 | 15 | 哨兵/极值风险条目与单位假阳性排除，异常显式进入 `uncertainty` | 覆盖 |
| 可靠体验 | 10 | 异常输入仍 exit 0 并写 `result.json`；`validate_result.py` 独立自检 | 覆盖 |
| 生态贡献 | 10 | `SKILL.md` + `examples/demo-case` + 独立校验脚本 + 本文件 | 覆盖 |

## 已知未覆盖 / 不适用

- 使用增益的 25 分要求与「不加载 Skill 的基础 Agent」在同一输入上做对照；本包只有自身产物与
  可复核证据，**没有平台白板或第三方 baseline 的运行数据**，因此按「部分」记录，不冒充已证明的增益。
- 平台固定用例与隐藏评分器不随仓库发布；包内 `examples/demo-case` 为合成样例，明确不是正式答案。
