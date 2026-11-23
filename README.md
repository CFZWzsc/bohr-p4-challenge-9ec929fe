# Runtime contract

Bohrium Playground Period 4 submission — challenge `challenge-9ec929fe` (track: `research-data-analysis`).

This repository holds the complete skill package for the challenge: the skill
definition, its executable entry point and all supporting scripts. No precomputed
answers, private data or credentials are included.

## Skill

- Name: `research-data-quality-gate`
- Summary: Use when a scientific data task supplies CSV or SQLite materials and requires cleaning, statistics, plots, SQL checks, or a contract-preserving /app/output/result.json; audit missing, duplicate, type and unit risks, and validate case_id plus every original output_contract field before delivery.

## Contents

| File |
|------|
| `SKILL.md` |
| `scripts/audit_data.py` |
| `scripts/validate_result.py` |
| `README.md` |

## How to run

```bash
python scripts/run_skill.py --case /input/case.json --output /app/output/result.json
```

The entry point is `SKILL.md`; input/output contracts, error handling and a minimal
usage example are documented there.

## Notes

- Pure Python standard library; no network access required.
- The result JSON keeps the input case id, the main results, per-item evidence,
  a run status and an uncertainty section.
