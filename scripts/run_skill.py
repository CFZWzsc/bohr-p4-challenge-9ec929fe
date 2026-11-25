#!/usr/bin/env python3
"""One-shot entry: audit every CSV/SQLite material in a case and write result.json.

This wrapper performs the two *mechanical* checks the skill owns — a data-quality
audit and a contract-preserving result — in a single deterministic pass. It does
not invent analysis: the narrative fields it fills are counts and locations that
were actually measured in the input files. Substantive statistics, plots and
conclusions remain the calling agent's job and are reported as not computed here.

Usage:
    python scripts/run_skill.py --input /input --work /app/work --out /app/output
    python scripts/run_skill.py --case /input/case.json --out /app/output/result.json
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from audit_data import AuditError, audit  # noqa: E402

MATERIAL_SUFFIXES = {".csv": "csv", ".sqlite": "sqlite", ".sqlite3": "sqlite", ".db": "sqlite"}
BASE_KEYS = ("case_id", "status", "answer", "risks", "sources")


def load_case(target):
    """Return (case_dict, case_path, case_dir, parse_failed)."""
    path = Path(target)
    if path.is_dir():
        candidate = path / "case.json"
        if candidate.is_file():
            try:
                return json.loads(candidate.read_text(encoding="utf-8-sig")), candidate, path, False
            except (OSError, ValueError):
                return {}, candidate, path, True
        return {}, None, path, False
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8-sig")), path, path.parent, False
        except (OSError, ValueError):
            return {}, path, path.parent, True
    return {}, None, path, False


def contract_keys(case):
    contract = case.get("output_contract") if isinstance(case, dict) else None
    if isinstance(contract, dict):
        return [str(key) for key in contract]
    if isinstance(contract, list):
        return [str(key) for key in contract]
    fields = case.get("fields") if isinstance(case, dict) else None
    if isinstance(fields, dict):
        return [str(key) for key in fields]
    if isinstance(fields, list):
        return [str(key) for key in fields]
    return []


def materials(case_dir, case_path):
    """Every CSV/SQLite file under the case directory, in stable relative order."""
    found = []
    for root, dirs, files in os.walk(case_dir):
        dirs[:] = sorted(d for d in dirs if d not in {"__pycache__", ".git"})
        for name in sorted(files):
            full = Path(root) / name
            fmt = MATERIAL_SUFFIXES.get(full.suffix.lower())
            if not fmt or (case_path and full.resolve() == case_path.resolve()):
                continue
            found.append((full, fmt))
    return found


def relative(path, case_dir):
    try:
        return Path(path).resolve().relative_to(Path(case_dir).resolve()).as_posix()
    except ValueError:
        return Path(path).name


def flatten_risks(reports, sources):
    out = []
    for report, source in zip(reports, sources):
        for table in report["tables"]:
            for risk in table["risks"]:
                item = {
                    "code": risk["code"],
                    "source": source["path"],
                    "count": risk.get("count"),
                }
                if risk.get("column"):
                    item["column"] = risk["column"]
                if table.get("table"):
                    item["table"] = table["table"]
                item["details"] = risk.get("details", {})
                out.append(item)
    return out


def describe(sources, risks):
    if not sources:
        return ("No CSV or SQLite material was found under the supplied case directory, "
                "so no audit could be run. Supply the actual data files, then repeat the "
                "audit; no values are inferred in their absence.")
    parts = []
    for source in sources:
        parts.append("%s holds %d row(s) across %d column(s)"
                     % (source["path"], source["row_count"], source["column_count"]))
    codes = {}
    for risk in risks:
        codes[risk["code"]] = codes.get(risk["code"], 0) + 1
    if codes:
        detail = ", ".join("%s x%d" % (code, codes[code]) for code in sorted(codes))
        tail = ("Audit recorded %d risk item(s) (%s). These are reported, not repaired: "
                "duplicates are not dropped and missing values are not imputed without a "
                "task-defined rule. Statistical analysis, plots and conclusions are not "
                "computed by this audit and still have to be produced from the data."
                % (len(risks), detail))
    else:
        tail = ("Audit found no missing-value, duplicate, type or unit conflict under the "
                "documented heuristics. That is not proof the data are analysis-ready: "
                "statistics, plots and conclusions are not computed here.")
    return "; ".join(parts) + ". " + tail


def main(argv=None):
    parser = argparse.ArgumentParser(description="Audit case materials and write result.json.")
    parser.add_argument("--input", "--case", dest="case", required=True,
                        help="case directory or path to case.json")
    parser.add_argument("--work", default="", help="working directory for audit artifacts")
    parser.add_argument("--out", "--output", dest="out", default="",
                        help="output directory or result.json path")
    args = parser.parse_args(argv)

    case, case_path, case_dir, case_parse_failed = load_case(args.case)
    keys = contract_keys(case)
    case_id = case.get("case_id") if isinstance(case, dict) else None

    out_arg = args.out or os.path.join(args.work or ".", "result.json")
    out_path = Path(out_arg)
    if out_path.suffix.lower() != ".json" or out_path.is_dir():
        out_path = out_path / "result.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    work_dir = Path(args.work) if args.work else out_path.parent / "work"
    work_dir.mkdir(parents=True, exist_ok=True)

    if case_parse_failed:
        result = {"case_id": case_id, "status": "failed",
                  "answer": "case.json could not be parsed as JSON; no materials were audited.",
                  "risks": [], "sources": [],
                  "uncertainty": ["malformed case.json: %s" % (case_path.name if case_path else "unknown")],
                  "changes": [{"action": "none", "detail": "no file was opened for auditing"}],
                  "artifacts": []}
        for key in keys:
            result.setdefault(key, None)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                            encoding="utf-8", newline="\n")
        print(json.dumps({"status": "failed", "materials": 0, "risks": 0,
                          "result": out_path.as_posix()}, ensure_ascii=False))
        try:
            import report_kit
            report_kit.augment(str(out_path), getattr(args, "case", None), [])
        except Exception:
            pass
        return 0

    reports, sources, failures = [], [], []
    for full, data_format in materials(case_dir, case_path):
        rel = relative(full, case_dir)
        try:
            report = audit(full, data_format, [])
        except (AuditError, OSError, ValueError) as exc:
            failures.append({"path": rel, "error": str(exc)})
            continue
        reports.append(report)
        table = report["tables"][0] if report["tables"] else {}
        sources.append({
            "path": rel,
            "format": data_format,
            "sha256": report["source"]["sha256"],
            "row_count": sum(t["row_count"] for t in report["tables"]),
            "column_count": table.get("column_count", 0),
            "risk_count": len(report["risks"]),
        })
        artifact = work_dir / ("audit_" + full.stem.replace(" ", "_") + ".json")
        artifact.write_text(
            json.dumps({"path": rel, "tables": report["tables"]},
                       ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8", newline="\n")

    risks = flatten_risks(reports, sources)
    if not sources:
        status = "insufficient_input"
    elif risks or failures:
        status = "partial"
    else:
        status = "success"

    uncertainty = []
    if failures:
        uncertainty.extend("could not audit %s: %s" % (f["path"], f["error"]) for f in failures)
    uncertainty.append("Required columns were not declared by the case, so required-column "
                       "checks were skipped rather than guessed.")
    result = {
        "case_id": case_id,
        "status": status,
        "answer": describe(sources, risks),
        "risks": risks,
        "sources": sources,
        "uncertainty": uncertainty,
        "changes": [{"action": "none", "detail": "source files were opened read-only and "
                                                 "were neither modified nor rewritten"}],
        "artifacts": sorted(p.name for p in work_dir.glob("audit_*.json")),
    }
    for key in keys:
        result.setdefault(key, None)

    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                        encoding="utf-8", newline="\n")
    print(json.dumps({"status": status, "materials": len(sources), "risks": len(risks),
                      "result": out_path.as_posix()}, ensure_ascii=False))
    try:
        import report_kit
        report_kit.augment(str(out_path), getattr(args, "case", None), [])
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
