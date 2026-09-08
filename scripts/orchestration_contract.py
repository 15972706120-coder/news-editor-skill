#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate News-Editor subagent task packets and filesystem handoffs.

The orchestrator owns the user request and final publication. Children exchange
small JSON contracts plus files in the run workspace; they do not pass raw logs
or rewrite another role's output directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ContractError(ValueError):
    pass


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError(f"cannot read JSON {path}: {error}") from error
    if not isinstance(data, dict):
        raise ContractError(f"JSON root must be an object: {path}")
    return data


def _config() -> dict[str, Any]:
    data = _load_json(CONFIG_PATH)
    orchestration = data.get("orchestration")
    if not isinstance(orchestration, dict) or not orchestration.get("enabled"):
        raise ContractError("config.json orchestration is missing or disabled")
    return orchestration


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _required_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{key} must be a non-empty string")
    return value


def _validate_id(value: str, label: str) -> None:
    if not ID_RE.fullmatch(value):
        raise ContractError(f"{label} must match {ID_RE.pattern}")


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _workspace_path(work_root: Path, value: str, *, must_exist: bool) -> Path:
    raw = Path(value)
    if raw.is_absolute() or ".." in raw.parts:
        raise ContractError(f"workspace path must be relative and traversal-free: {value}")
    resolved = (work_root / raw).resolve()
    if not _inside(resolved, work_root):
        raise ContractError(f"workspace path escapes the run root: {value}")
    if must_exist and not resolved.is_file():
        raise ContractError(f"workspace file does not exist: {value}")
    return resolved


def _role_root(work_root: Path, role: str, config: dict[str, Any]) -> Path:
    roots = config.get("role_output_roots")
    if not isinstance(roots, dict) or role not in roots:
        raise ContractError(f"unsupported role: {role}")
    return _workspace_path(work_root, str(roots[role]), must_exist=False)


def _verify_file_record(record: Any, work_root: Path, label: str) -> tuple[str, Path]:
    if not isinstance(record, dict):
        raise ContractError(f"{label} entry must be an object")
    relative = _required_string(record, "path")
    expected = _required_string(record, "sha256").lower()
    if not SHA256_RE.fullmatch(expected):
        raise ContractError(f"{label} sha256 is invalid: {relative}")
    path = _workspace_path(work_root, relative, must_exist=True)
    actual = sha256_file(path)
    if actual != expected:
        raise ContractError(f"{label} sha256 mismatch: {relative}")
    return relative, path


def validate_packet(
    packet_path: Path,
    work_root: Path,
    run_manifest_path: Path | None = None,
    run_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    config = _config()
    if (run_manifest_path is None) != (run_manifest_sha256 is None):
        raise ContractError("run manifest path and SHA-256 must be supplied together")
    work_root = work_root.resolve()
    packet_path = packet_path.resolve()
    if not _inside(packet_path, work_root):
        raise ContractError("task packet must be stored inside the run workspace")
    packet = _load_json(packet_path)
    if packet.get("schema") != config.get("task_packet_schema"):
        raise ContractError("task packet schema is invalid")

    run_id = _required_string(packet, "run_id")
    child_id = _required_string(packet, "child_id")
    topic_id = _required_string(packet, "topic_id")
    role = _required_string(packet, "role")
    skill_sha = _required_string(packet, "skill_sha").lower()
    _validate_id(run_id, "run_id")
    _validate_id(child_id, "child_id")
    _validate_id(topic_id, "topic_id")
    if not SHA1_RE.fullmatch(skill_sha):
        raise ContractError("skill_sha must be a 40-character commit SHA")

    inputs = packet.get("inputs")
    if not isinstance(inputs, list):
        raise ContractError("inputs must be a list")
    input_paths: list[str] = []
    for record in inputs:
        relative, _ = _verify_file_record(record, work_root, "input")
        if relative in input_paths:
            raise ContractError(f"duplicate input path: {relative}")
        input_paths.append(relative)

    references = packet.get("required_references")
    if not isinstance(references, list) or not all(isinstance(v, str) for v in references):
        raise ContractError("required_references must be a string list")
    for relative in references:
        raw = Path(relative)
        resolved = (ROOT / raw).resolve()
        if raw.is_absolute() or ".." in raw.parts or not _inside(resolved, ROOT) or not resolved.is_file():
            raise ContractError(f"required reference is invalid or missing: {relative}")

    role_root = _role_root(work_root, role, config)
    allowed = packet.get("allowed_outputs")
    if not isinstance(allowed, list) or not allowed or not all(isinstance(v, str) for v in allowed):
        raise ContractError("allowed_outputs must be a non-empty string list")
    normalized_outputs: list[str] = []
    for relative in allowed:
        output_path = _workspace_path(work_root, relative, must_exist=False)
        if not _inside(output_path, role_root):
            raise ContractError(f"role {role} cannot write output: {relative}")
        normalized = output_path.relative_to(work_root).as_posix()
        if normalized in normalized_outputs:
            raise ContractError(f"duplicate allowed output: {normalized}")
        normalized_outputs.append(normalized)

    constraints = packet.get("constraints")
    stop_conditions = packet.get("stop_conditions")
    if not isinstance(constraints, dict):
        raise ContractError("constraints must be an object")
    if not isinstance(stop_conditions, list) or not all(isinstance(v, str) and v.strip() for v in stop_conditions):
        raise ContractError("stop_conditions must be a non-empty string list")

    if run_manifest_path is not None:
        run_manifest_path = run_manifest_path.resolve()
        expected_manifest_hash = str(run_manifest_sha256 or "").lower()
        if not SHA256_RE.fullmatch(expected_manifest_hash):
            raise ContractError("run manifest SHA-256 is required and invalid")
        if sha256_file(run_manifest_path) != expected_manifest_hash:
            raise ContractError("run manifest SHA-256 mismatch")
        manifest = _load_json(run_manifest_path)
        if manifest.get("schema") != config.get("run_manifest_schema"):
            raise ContractError("run manifest schema is invalid")
        if (manifest.get("gate_status") != "LATEST_READY"
                or manifest.get("run_id") != run_id
                or str(manifest.get("active_sha", "")).lower() != skill_sha
                or str(manifest.get("remote_sha", "")).lower() != skill_sha):
            raise ContractError("task packet does not match the parent run manifest")

    return {
        "schema": "news-editor-contract-validation/v1",
        "status": "PACKET_READY",
        "packet": str(packet_path),
        "packet_sha256": sha256_file(packet_path),
        "run_id": run_id,
        "child_id": child_id,
        "topic_id": topic_id,
        "role": role,
        "skill_sha": skill_sha,
        "input_count": len(input_paths),
        "allowed_outputs": normalized_outputs,
    }


def validate_handoff(handoff_path: Path, packet_path: Path, work_root: Path) -> dict[str, Any]:
    config = _config()
    work_root = work_root.resolve()
    packet_result = validate_packet(packet_path, work_root)
    handoff_path = handoff_path.resolve()
    if not _inside(handoff_path, work_root):
        raise ContractError("handoff must be stored inside the run workspace")
    handoff = _load_json(handoff_path)
    if handoff.get("schema") != config.get("handoff_schema"):
        raise ContractError("handoff schema is invalid")
    for key in ("run_id", "child_id", "topic_id", "role"):
        if handoff.get(key) != packet_result[key]:
            raise ContractError(f"handoff {key} does not match the task packet")
    if str(handoff.get("input_packet_sha256", "")).lower() != packet_result["packet_sha256"]:
        raise ContractError("handoff input_packet_sha256 does not match the task packet")

    status = handoff.get("status")
    if status not in ("READY", "BLOCKED"):
        raise ContractError("handoff status must be READY or BLOCKED")
    outputs = handoff.get("outputs")
    if not isinstance(outputs, list) or (status == "READY" and not outputs):
        raise ContractError("READY handoff must contain at least one output")
    allowed = set(packet_result["allowed_outputs"])
    output_paths: list[str] = []
    for record in outputs:
        relative, path = _verify_file_record(record, work_root, "output")
        normalized = path.relative_to(work_root).as_posix()
        if normalized not in allowed:
            raise ContractError(f"handoff output was not authorized by the task packet: {relative}")
        if normalized in output_paths:
            raise ContractError(f"duplicate handoff output: {normalized}")
        output_paths.append(normalized)

    decisions = handoff.get("decisions")
    risks = handoff.get("risks")
    next_stage = handoff.get("next_stage")
    max_decisions = config.get("max_handoff_decisions")
    if not isinstance(decisions, list) or not all(isinstance(v, str) and v.strip() for v in decisions):
        raise ContractError("decisions must be a string list")
    if not isinstance(max_decisions, int) or len(decisions) > max_decisions:
        raise ContractError(f"handoff may contain at most {max_decisions} decisions")
    if not isinstance(risks, list) or not all(isinstance(v, str) and v.strip() for v in risks):
        raise ContractError("risks must be a string list")
    if not isinstance(next_stage, str) or not next_stage.strip():
        raise ContractError("next_stage must be a non-empty string")

    return {
        "schema": "news-editor-contract-validation/v1",
        "status": "HANDOFF_READY" if status == "READY" else "HANDOFF_BLOCKED",
        "handoff": str(handoff_path),
        "handoff_sha256": sha256_file(handoff_path),
        "run_id": packet_result["run_id"],
        "child_id": packet_result["child_id"],
        "topic_id": packet_result["topic_id"],
        "role": packet_result["role"],
        "output_count": len(output_paths),
        "outputs": output_paths,
        "next_stage": next_stage,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    packet = subparsers.add_parser("validate-packet")
    packet.add_argument("--packet", type=Path, required=True)
    packet.add_argument("--work-root", type=Path, required=True)
    packet.add_argument("--run-manifest", type=Path)
    packet.add_argument("--run-manifest-sha256")
    handoff = subparsers.add_parser("validate-handoff")
    handoff.add_argument("--handoff", type=Path, required=True)
    handoff.add_argument("--packet", type=Path, required=True)
    handoff.add_argument("--work-root", type=Path, required=True)
    digest = subparsers.add_parser("sha256")
    digest.add_argument("path", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate-packet":
            result = validate_packet(
                args.packet, args.work_root, args.run_manifest, args.run_manifest_sha256,
            )
        elif args.command == "validate-handoff":
            result = validate_handoff(args.handoff, args.packet, args.work_root)
        else:
            path = args.path.resolve()
            if not path.is_file():
                raise ContractError(f"file does not exist: {path}")
            result = {"path": str(path), "sha256": sha256_file(path)}
    except ContractError as error:
        print(json.dumps({"status": "BLOCKED_CONTRACT", "message": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
