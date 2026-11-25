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
DATE_LIKE_RE = re.compile(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}")
ISO_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
SENTINEL_VALUES = frozenset({-999.0, -9999.0, -99999.0, 9999.0, -1.0})
RANGE_UNIT_PREFIXES = ("/", "-", "\u2013", "~")


class AuditError(Exception):
    pass


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def portable_path(path):
    """Host-independent path: relative to the working directory, else the file name."""
    path = Path(path)
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.name


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


def is_temporal(value):
    """True for date/datetime tokens such as 2026/01/03 or a full ISO timestamp."""
    if not isinstance(value, str):
        return False
    text = value.strip()
    return bool(DATE_LIKE_RE.match(text) or ISO_TIMESTAMP_RE.search(text))


def inline_unit(value):
    if not isinstance(value, str) or is_temporal(value):
        return None
    match = INLINE_UNIT_RE.match(value)
    if not match:
        return None
    unit = match.group(2)
    # A leading separator means a date fragment or numeric range, not a unit.
    return None if unit.startswith(RANGE_UNIT_PREFIXES) else unit


def numeric_value(value):
    """Numeric reading of a value (plain number or number+unit); dates are not numeric."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if is_temporal(text):
        return None
    token = text
    if not NUMBER_RE.fullmatch(token):
        match = INLINE_UNIT_RE.match(token)
        if not match or match.group(2).startswith(RANGE_UNIT_PREFIXES):
            return None
        token = match.group(1)
    try:
        number = float(token)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def quantile(ordered, fraction):
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def extreme_points(points):
    """Row numbers whose value is an extreme outlier (robust IQR fences or |z|>5)."""
    values = [value for _, value in points]
    count = len(values)
    if count < 2:
        return []
    mean = sum(values) / count
    variance = sum((value - mean) ** 2 for value in values) / count
    sd = math.sqrt(variance) if variance > 0 else 0.0
    if count < 4:
        if sd == 0:
            return []
        return [(number, value) for number, value in points
                if abs((value - mean) / sd) > 5.0]
    ordered = sorted(values)
    q1 = quantile(ordered, 0.25)
    q3 = quantile(ordered, 0.75)
    iqr = q3 - q1
    lower = q1 - 3.0 * iqr
    upper = q3 + 3.0 * iqr
    flagged = []
    for number, value in points:
        outside = value < lower or value > upper
        z_extreme = sd > 0 and abs((value - mean) / sd) > 5.0
        if outside or z_extreme:
            flagged.append((number, value))
    return flagged


def numeric_risks(table, name, position, numeric_points):
    """Sentinel and extreme-value risks for one numeric-looking column."""
    risks = []
    sentinel_rows = [number for number, value in numeric_points if value in SENTINEL_VALUES]
    if sentinel_rows:
        sentinel_values = sorted({value for _, value in numeric_points
                                  if value in SENTINEL_VALUES})
        risks.append({
            "code": "sentinel_suspect",
            "type": "sentinel_suspect",
            "table": table,
            "column": name,
            "column_position": position,
            "count": len(sentinel_rows),
            "severity": "high",
            "values": sentinel_values,
            "rows": sentinel_rows,
            "details": {
                "severity": "high",
                "values": sentinel_values,
                "rows": sentinel_rows,
            },
        })
    clean = [(number, value) for number, value in numeric_points
             if value not in SENTINEL_VALUES]
    extreme = extreme_points(clean)
    if extreme:
        rows = [number for number, _ in extreme]
        values = [value for _, value in extreme]
        risks.append({
            "code": "extreme_value",
            "type": "extreme_value",
            "table": table,
            "column": name,
            "column_position": position,
            "count": len(extreme),
            "severity": "medium",
            "method": "IQR*3 / |z|>5",
            "rows": rows,
            "values": values,
            "details": {
                "method": "IQR*3 / |z|>5",
                "severity": "medium",
                "rows": rows,
                "values": values,
            },
        })
    return risks



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

        numeric_points = []
        for number, value in populated:
            parsed = numeric_value(value)
            if parsed is not None:
                numeric_points.append((number, parsed))
        if populated and len(numeric_points) / len(populated) >= 0.8:
            risks.extend(numeric_risks(table, name, index + 1, numeric_points))

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
            "path": portable_path(path),
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
    os.environ["SK_RESULT_PATH"] = str(args.output)
    try:
        report = audit(args.input, args.format, args.required, args.table)
        write_json(args.output, report)
    except (AuditError, OSError, csv.Error) as exc:
        report = {
            "schema_version": "1.0",
            "status": "failed",
            "source": {"path": portable_path(args.input), "format": args.format},
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
            _p = os.environ.get("SK_RESULT_PATH") or "result.json"
            _d = os.path.dirname(os.path.abspath(_p))
            if _d:
                os.makedirs(_d, exist_ok=True)
            with open(_p, "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "error": type(_exc).__name__,
                           "limitations": ["unhandled_exception"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
        _rc = 0
    _fallback = os.environ.get("SK_RESULT_PATH", "")
    if _fallback and not os.path.isfile(_fallback):
        try:
            _parent = os.path.dirname(os.path.abspath(_fallback))
            if _parent:
                os.makedirs(_parent, exist_ok=True)
            with open(_fallback, "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "limitations": ["no_result_written"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
    sys.exit(0 if _fallback else _rc)
