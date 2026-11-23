#!/usr/bin/env python3
"""Validate result.json case identity and presence of output_contract fields."""

import argparse
import hashlib
import json
import sys
from pathlib import Path


class InputError(Exception):
    pass


def load_json(path):
    path = Path(path)
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            value = json.load(handle)
    except FileNotFoundError as exc:
        raise InputError(f"JSON file not found: {path}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputError(f"Cannot read JSON object: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise InputError(f"JSON root must be an object: {path}")
    return value


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def named_fields(items):
    if not isinstance(items, list):
        raise InputError("Contract field collection must be a list")
    fields = []
    for item in items:
        if isinstance(item, str) and item:
            fields.append(item)
        elif isinstance(item, dict):
            name = item.get("name", item.get("field"))
            if not isinstance(name, str) or not name:
                raise InputError("Contract field entries need a non-empty name")
            fields.append(name)
        else:
            raise InputError("Unsupported contract field entry")
    return fields


def contract_fields(contract):
    """Extract top-level fields from common mappings, lists, or JSON Schema."""
    if isinstance(contract, list):
        fields = named_fields(contract)
    elif not isinstance(contract, dict):
        raise InputError("output_contract must be an object or field list")
    elif "properties" in contract:
        if not isinstance(contract["properties"], dict):
            raise InputError("output_contract.properties must be an object")
        fields = list(contract["properties"])
        if "required" in contract:
            fields += named_fields(contract["required"])
    elif "fields" in contract:
        container = contract["fields"]
        if isinstance(container, dict):
            fields = list(container)
        else:
            fields = named_fields(container)
    elif "required" in contract or "optional" in contract:
        fields = []
        if "required" in contract:
            fields += named_fields(contract["required"])
        if "optional" in contract:
            fields += named_fields(contract["optional"])
    elif (
        contract.get("type") == "object"
        or isinstance(contract.get("additionalProperties"), (bool, dict))
        or any(key in contract for key in ("$schema", "$defs", "definitions"))
    ):
        raise InputError("JSON Schema output_contract has no properties or required fields")
    else:
        # Compact mapping may legitimately declare fields named "type" or
        # "additionalProperties"; only unambiguous schema markers fail closed.
        fields = list(contract)

    fields = list(dict.fromkeys(fields))
    if not fields or any(not isinstance(field, str) or not field for field in fields):
        raise InputError("output_contract declares no usable fields")
    return fields


def validate(case, result):
    errors = []
    if "case_id" not in case:
        errors.append({"code": "missing_input_case_id", "path": "/input/case.json"})
    if "case_id" not in result:
        errors.append({"code": "missing_result_case_id", "path": "result.json"})
    elif "case_id" in case:
        same = type(case["case_id"]) is type(result["case_id"]) and case["case_id"] == result["case_id"]
        if not same:
            errors.append({
                "code": "case_id_mismatch",
                "path": "result.json/case_id",
                "expected": case["case_id"],
                "actual": result["case_id"],
            })

    fields = None
    if "output_contract" not in case:
        errors.append({"code": "missing_output_contract", "path": "/input/case.json"})
    else:
        try:
            fields = contract_fields(case["output_contract"])
        except InputError as exc:
            errors.append({"code": "invalid_output_contract", "message": str(exc)})
        else:
            missing = [field for field in fields if field not in result]
            if missing:
                errors.append({
                    "code": "missing_output_fields",
                    "path": "result.json",
                    "fields": missing,
                })

    return {
        "schema_version": "1.0",
        "valid": not errors,
        "checks": {
            "case_id_present": "case_id" in case and "case_id" in result,
            "case_id_exact_match": not any(error["code"] in {
                "missing_input_case_id", "missing_result_case_id", "case_id_mismatch"
            } for error in errors),
            "output_contract_field_count": len(fields) if fields is not None else None,
            "output_contract_fields": fields,
            "extra_result_fields_allowed": True,
            "field_values_or_schemas_validated": False,
        },
        "errors": errors,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, help="Input case.json path")
    parser.add_argument("--result", required=True, help="Candidate result.json path")
    parser.add_argument("--output", required=True, help="Validation report JSON path")
    args = parser.parse_args(argv)
    try:
        case = load_json(args.case)
        result = load_json(args.result)
        report = validate(case, result)
        report["evidence"] = {
            "case_path": str(Path(args.case).resolve()),
            "case_sha256": sha256(args.case),
            "result_path": str(Path(args.result).resolve()),
            "result_sha256": sha256(args.result),
        }
        exit_code = 0 if report["valid"] else 1
    except (InputError, OSError) as exc:
        report = {
            "schema_version": "1.0",
            "valid": False,
            "errors": [{"code": "input_error", "message": str(exc)}],
        }
        exit_code = 2
    try:
        write_json(args.output, report)
    except OSError as exc:
        print(json.dumps({
            "valid": False,
            "error": str(exc),
            "output": args.output,
        }, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"valid": report["valid"], "output": args.output}, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
