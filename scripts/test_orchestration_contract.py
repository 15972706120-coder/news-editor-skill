#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import orchestration_contract as contract


class OrchestrationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="news-editor-orchestration-")
        self.work = Path(self.temp.name).resolve()
        (self.work / "shared").mkdir()
        (self.work / "lanes" / "facts").mkdir(parents=True)
        self.input_path = self.work / "shared" / "source-manifest.json"
        self.input_path.write_text('{"ok":true}\n', encoding="utf-8")
        self.packet_path = self.work / "facts-task.json"
        self.packet = {
            "schema": "news-editor-task-packet/v1",
            "run_id": "run-20260908-a",
            "skill_sha": "a" * 40,
            "child_id": "facts-01",
            "role": "facts",
            "topic_id": "topic-01",
            "inputs": [{
                "path": "shared/source-manifest.json",
                "sha256": contract.sha256_file(self.input_path),
            }],
            "required_references": ["references/editorial-sop.md"],
            "allowed_outputs": ["lanes/facts/fact-pack.md"],
            "constraints": {"excluded_sources": ["国家级媒体"]},
            "stop_conditions": ["核心事实无法双来源核验时阻塞"],
        }
        self._write(self.packet_path, self.packet)

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _write(path: Path, data: dict) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _ready_handoff(self) -> tuple[Path, dict]:
        output = self.work / "lanes" / "facts" / "fact-pack.md"
        output.write_text("# fact pack\n", encoding="utf-8")
        handoff_path = self.work / "facts-handoff.json"
        handoff = {
            "schema": "news-editor-handoff/v1",
            "run_id": self.packet["run_id"],
            "child_id": self.packet["child_id"],
            "role": self.packet["role"],
            "topic_id": self.packet["topic_id"],
            "status": "READY",
            "input_packet_sha256": contract.sha256_file(self.packet_path),
            "outputs": [{
                "path": "lanes/facts/fact-pack.md",
                "sha256": contract.sha256_file(output),
            }],
            "decisions": ["采用两个独立来源锁定事件时间"],
            "risks": [],
            "next_stage": "copy_pacing",
        }
        self._write(handoff_path, handoff)
        return handoff_path, handoff

    def test_valid_packet_and_handoff(self) -> None:
        packet_result = contract.validate_packet(self.packet_path, self.work)
        self.assertEqual(packet_result["status"], "PACKET_READY")
        handoff_path, _ = self._ready_handoff()
        handoff_result = contract.validate_handoff(handoff_path, self.packet_path, self.work)
        self.assertEqual(handoff_result["status"], "HANDOFF_READY")

    def test_packet_must_match_run_manifest(self) -> None:
        manifest = self.work / "run-manifest.json"
        self._write(manifest, {
            "schema": "news-editor-orchestrated-run/v1",
            "run_id": self.packet["run_id"],
            "gate_status": "LATEST_READY",
            "active_sha": "b" * 40,
            "remote_sha": "b" * 40,
        })
        with self.assertRaisesRegex(contract.ContractError, "does not match"):
            contract.validate_packet(
                self.packet_path, self.work, manifest, contract.sha256_file(manifest),
            )

    def test_run_manifest_hash_is_required(self) -> None:
        manifest = self.work / "run-manifest.json"
        self._write(manifest, {
            "schema": "news-editor-orchestrated-run/v1",
            "run_id": self.packet["run_id"],
            "gate_status": "LATEST_READY",
            "active_sha": self.packet["skill_sha"],
            "remote_sha": self.packet["skill_sha"],
        })
        with self.assertRaisesRegex(contract.ContractError, "supplied together"):
            contract.validate_packet(self.packet_path, self.work, manifest)

    def test_bad_input_hash_is_rejected(self) -> None:
        self.packet["inputs"][0]["sha256"] = "0" * 64
        self._write(self.packet_path, self.packet)
        with self.assertRaisesRegex(contract.ContractError, "sha256 mismatch"):
            contract.validate_packet(self.packet_path, self.work)

    def test_path_escape_is_rejected(self) -> None:
        self.packet["allowed_outputs"] = ["../outside.md"]
        self._write(self.packet_path, self.packet)
        with self.assertRaisesRegex(contract.ContractError, "traversal-free"):
            contract.validate_packet(self.packet_path, self.work)

    def test_role_cannot_write_another_lane(self) -> None:
        self.packet["allowed_outputs"] = ["lanes/video/fact-pack.md"]
        self._write(self.packet_path, self.packet)
        with self.assertRaisesRegex(contract.ContractError, "cannot write"):
            contract.validate_packet(self.packet_path, self.work)

    def test_handoff_output_requires_authorization(self) -> None:
        handoff_path, handoff = self._ready_handoff()
        extra = self.work / "lanes" / "facts" / "extra.md"
        extra.write_text("extra\n", encoding="utf-8")
        handoff["outputs"] = [{"path": "lanes/facts/extra.md", "sha256": contract.sha256_file(extra)}]
        self._write(handoff_path, handoff)
        with self.assertRaisesRegex(contract.ContractError, "not authorized"):
            contract.validate_handoff(handoff_path, self.packet_path, self.work)

    def test_handoff_decision_budget_is_enforced(self) -> None:
        handoff_path, handoff = self._ready_handoff()
        handoff["decisions"] = [f"decision-{index}" for index in range(6)]
        self._write(handoff_path, handoff)
        with self.assertRaisesRegex(contract.ContractError, "at most"):
            contract.validate_handoff(handoff_path, self.packet_path, self.work)


if __name__ == "__main__":
    unittest.main()
