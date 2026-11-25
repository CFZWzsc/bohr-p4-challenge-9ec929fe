# Runtime contract

Bohrium Playground Period 4 submission — challenge `challenge-9ec929fe` (track: `research-data-analysis`).

This repository holds the complete skill package for the challenge: the skill
definition, its executable pipeline and all supporting documents. No precomputed
answers, private data or credentials are included.

## Skill

- Name: `research-data-quality-gate`
- Summary: Use when a scientific data task supplies CSV or SQLite materials and requires cleaning, statistics, plots, SQL checks, or a contract-preserving /app/output/result.json; audit missing, duplicate, type, unit, sentinel and extreme-value risks, and validate case_id plus every original output_contract field before delivery.

## Contents

| File |
|------|
| `RUBRIC.md` |
| `SKILL.md` |
| `examples/demo-case/case.json` |
| `examples/demo-case/data/sample_measurements.csv` |
| `examples/demo-case/result.json` |
| `scripts/audit_data.py` |
| `scripts/run_skill.py` |
| `scripts/validate_result.py` |
| `README.md` |

## How to run

```bash
python scripts/sk_entry.py --input /input --work /app/work --out /app/output
```

A single deterministic pass reads `/input`, computes the deliverable and writes a
contract-complete `/app/output/result.json` plus the human-readable review files
described in `SKILL.md`. The same input always produces byte-identical output; no
network access is used, and fields the materials cannot support are reported
explicitly instead of being left empty.

## Notes

- Pure Python standard library; no network access required.
- Every claim is anchored to paragraph-level evidence; unknown items are reported
  in `uncertainty` instead of being fabricated.
