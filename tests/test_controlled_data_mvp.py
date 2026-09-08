from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from controlled_data_mvp import ControlledDataService, PolicyBlockedError, ValidationError


ACTOR = "local-test-runner"
PURPOSE = "training_data_preparation"


class ControlledDataServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.service = ControlledDataService(str(Path(self.temp_dir.name) / "mvp.sqlite3"))

    def tearDown(self) -> None:
        self.service.db.connection.close()
        self.temp_dir.cleanup()

    def register_allowed_asset(self) -> dict[str, object]:
        return self.service.register_asset(
            {
                "name": "public-technical-glossary.txt",
                "content": "A public glossary explains archive metadata and document version labels.",
                "media_type": "text/plain",
                "authorized": True,
                "authorization_ref": "public-reference-example",
                "declared_use": PURPOSE,
                "sensitivity": "public",
                "metadata": {"source_kind": "public_reference", "scope": "non_sensitive_knowledge"},
            },
            ACTOR,
            PURPOSE,
        )

    def create_contract(self) -> dict[str, object]:
        return self.service.create_contract(
            {
                "version": "test-1",
                "required_fields": ["input", "evidence", "quality_score"],
                "max_age_days": 30,
                "quality_assertions": [{"field": "quality_score", "min": 0.8}],
            },
            ACTOR,
            PURPOSE,
        )

    def freeze_allowed_dataset(self) -> dict[str, object]:
        asset = self.register_allowed_asset()
        contract = self.create_contract()
        return self.service.freeze_dataset(
            {
                "name": "public-glossary-v1",
                "contract_id": contract["id"],
                "asset_ids": [asset["id"]],
                "samples": [
                    {
                        "input": "What does a document version label identify?",
                        "evidence": "A public glossary explains archive metadata and document version labels.",
                        "quality_score": 0.95,
                    }
                ],
            },
            ACTOR,
            PURPOSE,
        )

    def test_unauthorized_asset_is_blocked_and_audited(self) -> None:
        result = self.service.register_asset(
            {
                "name": "missing-authorization.txt",
                "content": "A public educational example with no authorization record.",
                "authorized": False,
                "declared_use": PURPOSE,
                "sensitivity": "public",
                "metadata": {"scope": "non_sensitive_knowledge"},
            },
            ACTOR,
            PURPOSE,
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("UNAUTHORIZED_SOURCE", result["policy"]["reason_codes"])
        self.assertTrue(self.service.verify_audit_chain()["valid"])

    def test_prohibited_operational_scope_is_blocked(self) -> None:
        result = self.service.register_asset(
            {
                "name": "out-of-scope.txt",
                "content": "Public educational text.",
                "authorized": True,
                "declared_use": PURPOSE,
                "sensitivity": "public",
                "metadata": {"scope": "tactical reference"},
            },
            ACTOR,
            PURPOSE,
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("PROHIBITED_DOMAIN_SCOPE", result["policy"]["reason_codes"])

    def test_prohibited_operational_scope_in_content_is_blocked(self) -> None:
        result = self.service.register_asset(
            {
                "name": "out-of-scope-content.txt",
                "content": "这是一段包含作战行动方案的文本，不得进入训练数据。",
                "authorized": True,
                "declared_use": PURPOSE,
                "sensitivity": "public",
                "metadata": {"scope": "non_sensitive_knowledge"},
            },
            ACTOR,
            PURPOSE,
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("PROHIBITED_DOMAIN_SCOPE", result["policy"]["reason_codes"])

    def test_unresolved_high_risk_pii_blocks_dataset_freeze(self) -> None:
        asset = self.register_allowed_asset()
        contract = self.create_contract()
        self.service.record_pii_finding(
            str(asset["id"]),
            {
                "entity_type": "government_identifier",
                "location": "paragraph:1",
                "confidence": 0.99,
                "detector_version": "test-pii-1",
                "action": "quarantine",
                "review_status": "PENDING",
            },
            ACTOR,
            PURPOSE,
        )

        with self.assertRaises(PolicyBlockedError) as caught:
            self.service.freeze_dataset(
                {
                    "name": "blocked-pii",
                    "contract_id": contract["id"],
                    "asset_ids": [asset["id"]],
                    "samples": [{"input": "x", "evidence": "y", "quality_score": 0.95}],
                },
                ACTOR,
                PURPOSE,
            )

        self.assertIn("pii", caught.exception.result["reason_codes"])

    def test_data_contract_schema_and_quality_assertions_block_freeze(self) -> None:
        asset = self.register_allowed_asset()
        contract = self.create_contract()

        with self.assertRaises(PolicyBlockedError) as caught:
            self.service.freeze_dataset(
                {
                    "name": "contract-failure",
                    "contract_id": contract["id"],
                    "asset_ids": [asset["id"]],
                    "samples": [{"input": "x", "quality_score": 0.4}],
                },
                ACTOR,
                PURPOSE,
            )

        self.assertIn("schema", caught.exception.result["reason_codes"])
        self.assertIn("quality", caught.exception.result["reason_codes"])

    def test_pending_contract_observes_failures_without_blocking(self) -> None:
        asset = self.register_allowed_asset()
        contract = self.service.create_contract(
            {
                "version": "pending-1",
                "lifecycle_state": "PENDING",
                "required_fields": ["input", "evidence", "quality_score"],
                "max_age_days": 30,
                "quality_assertions": [{"field": "quality_score", "min": 0.8}],
            },
            ACTOR,
            PURPOSE,
        )
        dataset = self.service.freeze_dataset(
            {
                "name": "pending-contract-observation",
                "contract_id": contract["id"],
                "asset_ids": [asset["id"]],
                "samples": [{"input": "x", "quality_score": 0.4}],
            },
            ACTOR,
            PURPOSE,
        )
        self.assertEqual("FROZEN", dataset["status"])
        self.assertFalse(dataset["manifest"]["checks"]["observed_contract_failures"] == [])

    def test_retired_contract_cannot_freeze(self) -> None:
        asset = self.register_allowed_asset()
        contract = self.service.create_contract(
            {
                "version": "retired-1",
                "lifecycle_state": "RETIRED",
                "required_fields": ["input"],
                "max_age_days": 30,
                "quality_assertions": [],
            },
            ACTOR,
            PURPOSE,
        )
        with self.assertRaises(ValidationError):
            self.service.freeze_dataset(
                {"name": "retired", "contract_id": contract["id"], "asset_ids": [asset["id"]], "samples": [{"input": "x"}]},
                ACTOR,
                PURPOSE,
            )

    def test_frozen_lora_manifest_is_complete_and_auditable(self) -> None:
        dataset = self.freeze_allowed_dataset()
        exported = self.service.export_dataset(str(dataset["id"]), "lora", ACTOR, PURPOSE)
        manifest = exported["manifest"]

        self.assertEqual("FROZEN", dataset["status"])
        self.assertTrue(dataset["manifest"]["checks"]["passed"])
        self.assertEqual("lora", manifest["kind"])
        self.assertEqual(dataset["manifest_hash"], manifest["dataset_manifest_hash"])
        self.assertTrue(manifest["non_sensitive_authorized_only"])
        self.assertTrue(self.service.verify_audit_chain()["valid"])

    def test_grpo_manifest_references_reward_spec_and_replay_is_deterministic(self) -> None:
        dataset = self.freeze_allowed_dataset()
        reward_spec = self.service.create_reward_spec(
            {"version": "reward-test-1", "weights": {"evidence": 0.7, "boundary": 0.3}},
            ACTOR,
            PURPOSE,
        )
        episode = self.service.record_episode(
            str(dataset["id"]),
            str(reward_spec["id"]),
            {
                "prompt": "Summarize the public glossary in one sentence.",
                "candidates": [
                    {"id": "candidate-a", "reward_vector": {"evidence": 1.0, "boundary": 1.0}},
                    {"id": "candidate-b", "reward_vector": {"evidence": 0.5, "boundary": 1.0}},
                ],
            },
            ACTOR,
            PURPOSE,
        )

        replay = self.service.replay_episode(str(episode["id"]), ACTOR, PURPOSE)
        exported = self.service.export_dataset(str(dataset["id"]), "grpo", ACTOR, PURPOSE)

        self.assertEqual(1.0, replay["replayed_rewards"][0]["scalar_reward"])
        self.assertAlmostEqual(0.65, replay["replayed_rewards"][1]["scalar_reward"])
        self.assertEqual([reward_spec["id"]], exported["manifest"]["reward_specs"])

    def test_audit_chain_and_openlineage_lifecycle_events_are_recorded(self) -> None:
        dataset = self.freeze_allowed_dataset()
        self.service.export_dataset(str(dataset["id"]), "lora", ACTOR, PURPOSE)

        with self.service.db.transaction() as cursor:
            cursor.execute("SELECT event_type, run_id, job_name FROM lineage_events ORDER BY created_at, id")
            events = [tuple(row) for row in cursor.fetchall()]

        self.assertTrue(self.service.verify_audit_chain()["valid"])
        self.assertTrue(any(event[0] == "START" and event[2] == "source_asset_registration" for event in events))
        self.assertTrue(any(event[0] == "COMPLETE" and event[2] == "source_asset_registration" for event in events))
        self.assertTrue(any(event[0] == "START" and event[2] == "dataset_freeze" for event in events))
        self.assertTrue(any(event[0] == "COMPLETE" and event[2] == "dataset_freeze" for event in events))
        self.assertTrue(any(event[0] == "START" and event[2] == "dataset_export" for event in events))
        self.assertTrue(any(event[0] == "COMPLETE" and event[2] == "dataset_export" for event in events))
        for job_name in {event[2] for event in events}:
            lifecycle = [event for event in events if event[2] == job_name]
            starts = [event[1] for event in lifecycle if event[0] == "START"]
            terminals = [event[1] for event in lifecycle if event[0] in {"COMPLETE", "FAIL", "ABORT"}]
            if starts and terminals:
                self.assertEqual(starts[0], terminals[-1])


if __name__ == "__main__":
    unittest.main()
