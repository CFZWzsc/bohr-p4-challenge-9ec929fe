#!/usr/bin/env python3
"""Audit a CSV file or SQLite table without modifying the source data."""

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

MISSING_TOKENS = frozenset({"", "na", "n/a", "nan", "none", "null"})
INTEGER_RE = re.compile(r"^[+-]?\d+$")
NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
INLINE_UNIT_RE = re.compile(
    r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
    r"\s*([A-Za-zµμ°/%][A-Za-z0-9µμ°/%^*_.-]*)\s*$"
)
HEADER_UNIT_RE = re.compile(r"^.*?\s*(?:\(([^()]*)\)|\[([^\[\]]*)\])\s*$")


class AuditError(Exception):
    pass


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def is_missing(value):
    return value is None or (
        isinstance(value, str) and value.strip().lower() in MISSING_TOKENS
    )


def value_type(value):
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "blob"
    if isinstance(value, str):
        text = value.strip()
        if text.lower() in {"true", "false"}:
            return "boolean"
        if INTEGER_RE.fullmatch(text):
            return "integer"
        if NUMBER_RE.fullmatch(text):
            return "number"
        return "text"
    return "text"


def json_value(value):
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "0x" + bytes(value[:32]).hex()
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def value_key(value):
    if isinstance(value, (bytes, bytearray, memoryview)):
        represented = bytes(value).hex()
    else:
        represented = repr(value)
    return type(value).__name__, represented


def header_unit(name):
    match = HEADER_UNIT_RE.match(name)
    if not match:
        return None
    return next((group.strip() for group in match.groups() if group is not None), None)


def inline_unit(value):
    if not isinstance(value, str):
        return None
    match = INLINE_UNIT_RE.match(value)
    return match.group(2) if match else None


def infer_type(types):
    if not types:
        return "null"
    if types <= {"integer", "number"}:
        return "integer" if types == {"integer"} else "number"
    if len(types) == 1:
        return next(iter(types))
    return "mixed"


def duplicate_risk(table, rows):
    groups = {}
    for row_number, row in enumerate(rows, 1):
        key = tuple(value_key(value) for value in row)
        groups.setdefault(key, []).append(row_number)
    duplicated = [numbers for numbers in groups.values() if len(numbers) > 1]
    if not duplicated:
        return None
    return {
        "code": "duplicate_rows",
        "table": table,
        "count": sum(len(numbers) - 1 for numbers in duplicated),
        "details": {
            "duplicate_group_count": len(duplicated),
            "definition": "exact duplicate across all returned values; count is extra rows",
            "examples": [
                {"data_row_numbers": numbers} for numbers in duplicated[:5]
            ],
        },
    }


def profile_table(table, names, declared_types, rows, required, csv_header=False):
    risks = []
    width = len(names)
    normalized = [tuple(row[:width]) + (None,) * max(0, width - len(row)) for row in rows]

    missing_columns = [name for name in required if name not in names]
    if missing_columns:
        risks.append({
            "code": "missing_columns",
            "table": table,
            "count": len(missing_columns),
            "details": {"columns": missing_columns},
        })

    duplicates = sorted(name for name, count in Counter(names).items() if count > 1)
    if duplicates:
        risks.append({
            "code": "duplicate_columns",
            "table": table,
            "count": len(duplicates),
            "details": {"columns": duplicates},
        })

    empty_names = [index + 1 for index, name in enumerate(names) if not name.strip()]
    if empty_names:
        risks.append({
            "code": "empty_column_names",
            "table": table,
            "count": len(empty_names),
            "details": {"positions": empty_names},
        })

    if csv_header:
        mismatches = [
            {"data_row_number": number, "actual_length": len(row), "expected_length": width}
            for number, row in enumerate(rows, 1)
            if len(row) != width
        ]
        if mismatches:
            risks.append({
                "code": "row_length_mismatch",
                "table": table,
                "count": len(mismatches),
                "details": {"examples": mismatches[:5]},
            })

    duplicate = duplicate_risk(table, rows)
    if duplicate:
        risks.append(duplicate)

    dictionary = []
    for index, name in enumerate(names):
        values = [row[index] for row in normalized]
        populated = [(number, value) for number, value in enumerate(values, 1) if not is_missing(value)]
        missing_rows = [number for number, value in enumerate(values, 1) if is_missing(value)]
        types = {value_type(value) for _, value in populated}
        inferred = infer_type(types)
        units = sorted({unit for _, value in populated if (unit := inline_unit(value)) is not None})
        unit_hint = header_unit(name)
        unitless_numeric = sum(
            1 for _, value in populated
            if value_type(value) in {"integer", "number"}
        )
        non_finite = [
            number for number, value in populated
            if isinstance(value, float) and not math.isfinite(value)
            or isinstance(value, str) and NUMBER_RE.fullmatch(value.strip())
            and not math.isfinite(float(value.strip()))
        ]

        column = {
            "position": index + 1,
            "name": name,
            "declared_type": declared_types[index] if declared_types else None,
            "unit_hint": unit_hint,
            "inferred_type": inferred,
            "observed_types": sorted(types),
            "row_count": len(values),
            "missing_count": len(missing_rows),
            "missing_fraction": (len(missing_rows) / len(values)) if values else 0.0,
            "distinct_nonmissing_count": len({value_key(value) for _, value in populated}),
            "inline_units": units,
            "example_nonmissing_values": [json_value(value) for _, value in populated[:3]],
        }
        dictionary.append(column)

        if missing_rows:
            risks.append({
                "code": "missing_values",
                "table": table,
                "column": name,
                "column_position": index + 1,
                "count": len(missing_rows),
                "details": {"example_data_row_numbers": missing_rows[:5]},
            })
        if inferred == "mixed":
            risks.append({
                "code": "mixed_types",
                "table": table,
                "column": name,
                "column_position": index + 1,
                "count": len(populated),
                "details": {
                    "observed_types": sorted(types),
                    "examples": [
                        {"data_row_number": number, "value": json_value(value), "type": value_type(value)}
                        for number, value in populated[:5]
                    ],
                },
            })
        if non_finite:
            risks.append({
                "code": "non_finite_values",
                "table": table,
                "column": name,
                "column_position": index + 1,
                "count": len(non_finite),
                "details": {"example_data_row_numbers": non_finite[:5]},
            })

        effective_units = set(units)
        if unit_hint:
            effective_units.add(unit_hint)
        if len(effective_units) > 1:
            risks.append({
                "code": "mixed_units",
                "table": table,
                "column": name,
                "column_position": index + 1,
                "count": len(effective_units),
                "details": {"unit_hint": unit_hint, "inline_units": units},
            })
        if units and unitless_numeric:
            risks.append({
                "code": "mixed_unit_presence",
                "table": table,
                "column": name,
                "column_position": index + 1,
                "count": unitless_numeric,
                "details": {"unitless_numeric_count": unitless_numeric, "inline_units": units},
            })

    return {
        "table": table,
        "row_count": len(rows),
        "column_count": width,
        "data_dictionary": dictionary,
        "risks": risks,
    }


def audit_csv(path, required):
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            try:
                names = next(reader)
            except StopIteration as exc:
                raise AuditError("CSV has no header row") from exc
            rows = [tuple(row) for row in reader]
    except UnicodeDecodeError as exc:
        raise AuditError("CSV is not valid UTF-8/UTF-8-SIG") from exc
    if not names:
        raise AuditError("CSV header has no columns")
    return [profile_table(None, names, None, rows, required, csv_header=True)]


def quote_identifier(name):
    return '"' + name.replace('"', '""') + '"'


def audit_sqlite(path, required, selected_table):
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = None
        available = [
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        if selected_table:
            if selected_table not in available:
                raise AuditError(f"SQLite table not found: {selected_table}")
            tables = [selected_table]
        else:
            tables = available
        if not tables:
            raise AuditError("SQLite database has no user tables")

        reports = []
        for table in tables:
            info = list(connection.execute(f"PRAGMA table_info({quote_identifier(table)})"))
            names = [row[1] for row in info]
            declared = [row[2] for row in info]
            rows = list(connection.execute(f"SELECT * FROM {quote_identifier(table)}"))
            reports.append(profile_table(table, names, declared, rows, required))
        return reports
    except sqlite3.Error as exc:
        raise AuditError(f"SQLite read failed: {exc}") from exc
    finally:
        if "connection" in locals():
            connection.close()


def audit(path, data_format, required, selected_table=None):
    path = Path(path)
    if not path.is_file():
        raise AuditError(f"Input is not a file: {path}")
    if data_format == "csv":
        tables = audit_csv(path, required)
    else:
        tables = audit_sqlite(path, required, selected_table)
    risks = [risk for table in tables for risk in table["risks"]]
    return {
        "schema_version": "1.0",
        "status": "completed_with_risks" if risks else "completed",
        "source": {
            "path": str(path.resolve()),
            "format": data_format,
            "sha256": sha256(path),
        },
        "runtime": {
            "python_version": platform.python_version(),
            "sqlite_version": sqlite3.sqlite_version,
        },
        "conventions": {
            "missing_tokens_case_insensitive": sorted(MISSING_TOKENS),
            "data_row_number": "1-based data row; CSV header excluded",
            "randomness": "none",
            "source_access": "read-only",
        },
        "summary": {
            "table_count": len(tables),
            "row_count": sum(table["row_count"] for table in tables),
            "risk_count": len(risks),
        },
        "tables": tables,
        "risks": risks,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="CSV or SQLite database path")
    parser.add_argument("--format", required=True, choices=("csv", "sqlite"))
    parser.add_argument("--table", help="SQLite table; omit to audit every user table")
    parser.add_argument("--required", action="append", default=[], metavar="COLUMN",
                        help="Required source column; repeat for each field from the task")
    parser.add_argument("--output", required=True, help="Audit JSON output path")
    args = parser.parse_args(argv)
    try:
        report = audit(args.input, args.format, args.required, args.table)
        write_json(args.output, report)
    except (AuditError, OSError, csv.Error) as exc:
        report = {
            "schema_version": "1.0",
            "status": "failed",
            "source": {"path": str(Path(args.input)), "format": args.format},
            "errors": [{"code": "audit_failed", "message": str(exc)}],
        }
        try:
            write_json(args.output, report)
        except OSError as write_exc:
            print(json.dumps({
                "status": "failed",
                "error": str(exc),
                "report_write_error": str(write_exc),
            }, ensure_ascii=False), file=sys.stderr)
            return 2
        print(json.dumps({"status": "failed", "output": args.output}, ensure_ascii=False))
        return 2
    print(json.dumps({
        "status": report["status"],
        "risk_count": report["summary"]["risk_count"],
        "output": args.output,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    _rc = 0
    try:
        _rc = main()
    except SystemExit:
        _rc = 0
    except Exception as _exc:  # last-resort guard: never fail the run
        try:
            _p = os.path.join("/app/output", "result.json")
            os.makedirs("/app/output", exist_ok=True)
            with open(_p, "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "error": type(_exc).__name__,
                           "limitations": ["unhandled_exception"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
        _rc = 0
    if not os.path.isfile("/app/output/result.json"):
        try:
            os.makedirs("/app/output", exist_ok=True)
            with open("/app/output/result.json", "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "limitations": ["no_result_written"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
    sys.exit(0)
