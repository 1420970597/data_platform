"""Local, append-oriented governance core using only the Python standard library."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

ALLOWED_USES = {"training_data_preparation", "quality_review", "evaluation"}
PROHIBITED_TERMS = {
    "combat", "operation", "targeting", "weapon", "tactical", "real_time_intelligence",
    "作战", "行动方案", "目标选择", "武器", "战术", "实时情报",
}


class ServiceError(Exception):
    """Base error with stable API semantics."""


class ValidationError(ServiceError):
    pass


class PolicyBlockedError(ServiceError):
    def __init__(self, result: Mapping[str, Any]):
        super().__init__("policy gate blocked the request")
        self.result = dict(result)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    payload = value if isinstance(value, bytes) else canonical(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class CursorAdapter:
    """Normalizes DB-API query placeholders for SQLite and psycopg."""

    def __init__(self, cursor: Any, is_sqlite: bool) -> None:
        self.cursor = cursor
        self.is_sqlite = is_sqlite

    def execute(self, statement: str, params: Sequence[Any] | None = None) -> Any:
        sql = statement if self.is_sqlite else statement.replace("?", "%s")
        return self.cursor.execute(sql, params) if params is not None else self.cursor.execute(sql)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.cursor, name)


class Database:
    """SQLite storage for the zero-dependency MVP; PostgreSQL is a future adapter."""

    def __init__(self, dsn: str = "controlled_data_mvp.sqlite3") -> None:
        self.is_sqlite = not dsn.startswith(("postgres://", "postgresql://"))
        if self.is_sqlite:
            if dsn != ":memory:":
                Path(dsn).parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(dsn, check_same_thread=False)
            self.connection.row_factory = sqlite3.Row
        else:
            try:
                import psycopg  # type: ignore[import-not-found]
                from psycopg.rows import dict_row  # type: ignore[import-not-found]
            except ImportError as exc:
                raise RuntimeError("PostgreSQL DSN requires optional dependency 'psycopg'") from exc
            self.connection = psycopg.connect(dsn, row_factory=dict_row)
        self.initialize()

    @contextmanager
    def transaction(self) -> Iterator[CursorAdapter]:
        cursor = self.connection.cursor()
        adapter = CursorAdapter(cursor, self.is_sqlite)
        try:
            yield adapter
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        finally:
            cursor.close()

    def initialize(self) -> None:
        audit_sequence = "INTEGER PRIMARY KEY" if self.is_sqlite else "BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY"
        statements = [
            """CREATE TABLE IF NOT EXISTS source_assets (
                id TEXT PRIMARY KEY, content_hash TEXT NOT NULL, name TEXT NOT NULL,
                media_type TEXT NOT NULL, authorization_ref TEXT, declared_use TEXT NOT NULL,
                sensitivity TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS policy_decisions (
                id TEXT PRIMARY KEY, subject_type TEXT NOT NULL, subject_id TEXT NOT NULL,
                decision_json TEXT NOT NULL, created_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS pii_findings (
                id TEXT PRIMARY KEY, asset_id TEXT NOT NULL, entity_type TEXT NOT NULL,
                location TEXT NOT NULL, confidence REAL NOT NULL, detector_version TEXT NOT NULL,
                action TEXT NOT NULL, review_status TEXT NOT NULL, created_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS data_contracts (
                id TEXT PRIMARY KEY, version TEXT NOT NULL, lifecycle_state TEXT NOT NULL DEFAULT 'ACTIVE',
                contract_json TEXT NOT NULL, created_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS dataset_versions (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, contract_id TEXT NOT NULL,
                asset_ids_json TEXT NOT NULL, sample_json TEXT NOT NULL, manifest_hash TEXT NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL, frozen_at TEXT)""",
            """CREATE TABLE IF NOT EXISTS reward_specs (
                id TEXT PRIMARY KEY, version TEXT NOT NULL, spec_json TEXT NOT NULL, created_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS episodes (
                id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL, reward_spec_id TEXT NOT NULL,
                episode_json TEXT NOT NULL, created_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS exports (
                id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL, kind TEXT NOT NULL,
                manifest_json TEXT NOT NULL, manifest_hash TEXT NOT NULL, created_at TEXT NOT NULL)""",
            f"""CREATE TABLE IF NOT EXISTS audit_events (
                sequence {audit_sequence}, id TEXT UNIQUE NOT NULL, event_type TEXT NOT NULL,
                actor TEXT NOT NULL, purpose TEXT NOT NULL, subject_id TEXT NOT NULL,
                payload_json TEXT NOT NULL, previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL,
                created_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS lineage_events (
                id TEXT PRIMARY KEY, event_type TEXT NOT NULL, run_id TEXT NOT NULL,
                job_name TEXT NOT NULL, inputs_json TEXT NOT NULL, outputs_json TEXT NOT NULL,
                facets_json TEXT NOT NULL, created_at TEXT NOT NULL)""",
        ]
        with self.transaction() as cursor:
            for statement in statements:
                cursor.execute(statement)
            if self.is_sqlite:
                cursor.execute("PRAGMA table_info(data_contracts)")
                columns = {row[1] for row in cursor.fetchall()}
                if "lifecycle_state" not in columns:
                    cursor.execute("ALTER TABLE data_contracts ADD COLUMN lifecycle_state TEXT NOT NULL DEFAULT 'ACTIVE'")


class ControlledDataService:
    POLICY_VERSION = "mvp-1"

    def __init__(self, dsn: str = "controlled_data_mvp.sqlite3") -> None:
        self.db = Database(dsn)

    def _fetchone(self, statement: str, params: Sequence[Any]) -> dict[str, Any] | None:
        with self.db.transaction() as cursor:
            cursor.execute(statement, params)
            row = cursor.fetchone()
            return dict(row) if row else None

    def _audit(self, event_type: str, actor: str, purpose: str, subject_id: str, payload: Mapping[str, Any]) -> str:
        now, event_id = utc_now(), str(uuid.uuid4())
        with self.db.transaction() as cursor:
            cursor.execute("SELECT event_hash FROM audit_events ORDER BY sequence DESC LIMIT 1")
            previous = cursor.fetchone()
            previous_hash = previous[0] if previous else "GENESIS"
            body = {"id": event_id, "event_type": event_type, "actor": actor, "purpose": purpose,
                    "subject_id": subject_id, "payload": payload, "previous_hash": previous_hash, "created_at": now}
            event_hash = digest(body)
            cursor.execute("INSERT INTO audit_events (id,event_type,actor,purpose,subject_id,payload_json,previous_hash,event_hash,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                           (event_id, event_type, actor, purpose, subject_id, canonical(payload), previous_hash, event_hash, now))
        return event_id

    def _lineage(self, event_type: str, job_name: str, inputs: list[str], outputs: list[str],
                 facets: Mapping[str, Any], run_id: str | None = None) -> str:
        event_id, run_id, now = str(uuid.uuid4()), run_id or str(uuid.uuid4()), utc_now()
        with self.db.transaction() as cursor:
            cursor.execute("INSERT INTO lineage_events VALUES (?,?,?,?,?,?,?,?)",
                           (event_id, event_type, run_id, job_name, canonical(inputs), canonical(outputs), canonical(facets), now))
        return event_id

    def evaluate_policy(self, *, authorized: bool, declared_use: str, sensitivity: str,
                        metadata: Mapping[str, Any], content: str = "", name: str = "") -> dict[str, Any]:
        reasons: list[str] = []
        searchable = " ".join((content, name, declared_use, canonical(metadata))).lower()
        if not authorized:
            reasons.append("UNAUTHORIZED_SOURCE")
        if declared_use not in ALLOWED_USES:
            reasons.append("PROHIBITED_USE")
        if sensitivity.lower() not in {"public", "internal_deidentified", "non_sensitive"}:
            reasons.append("SENSITIVE_SCOPE")
        if any(term in searchable for term in PROHIBITED_TERMS):
            reasons.append("PROHIBITED_DOMAIN_SCOPE")
        return {"decision": "BLOCK" if reasons else "ALLOW", "reason_codes": reasons,
                "evidence_refs": [], "policy_version": self.POLICY_VERSION, "review_required": bool(reasons)}

    def register_asset(self, payload: Mapping[str, Any], actor: str, purpose: str) -> dict[str, Any]:
        content = payload.get("content", "")
        if not isinstance(content, str) or not content:
            raise ValidationError("content must be a non-empty string")
        metadata = dict(payload.get("metadata", {}))
        declared_use, sensitivity = str(payload.get("declared_use", "")), str(payload.get("sensitivity", ""))
        name = str(payload.get("name", "unnamed"))
        decision = self.evaluate_policy(authorized=bool(payload.get("authorized", False)), declared_use=declared_use,
                                        sensitivity=sensitivity, metadata=metadata, content=content, name=name)
        asset_id, now = str(uuid.uuid4()), utc_now()
        status, content_hash = ("BLOCKED" if decision["decision"] == "BLOCK" else "REGISTERED"), hashlib.sha256(content.encode("utf-8")).hexdigest()
        with self.db.transaction() as cursor:
            cursor.execute("INSERT INTO source_assets VALUES (?,?,?,?,?,?,?,?,?,?)", (
                asset_id, content_hash, name, str(payload.get("media_type", "text/plain")),
                payload.get("authorization_ref"), declared_use, sensitivity, status, now, canonical(metadata)))
            cursor.execute("INSERT INTO policy_decisions VALUES (?,?,?,?,?)", (str(uuid.uuid4()), "source_asset", asset_id, canonical(decision), now))
        self._audit("SOURCE_ASSET_REGISTERED", actor, purpose, asset_id, {"status": status, "content_hash": content_hash, "policy": decision})
        lineage_run_id = str(uuid.uuid4())
        self._lineage("START", "source_asset_registration", [], [asset_id], {"policy": decision}, lineage_run_id)
        self._lineage("COMPLETE" if status == "REGISTERED" else "FAIL", "source_asset_registration", [], [asset_id], {"status": status}, lineage_run_id)
        return {"id": asset_id, "status": status, "content_hash": content_hash, "policy": decision}

    def record_pii_finding(self, asset_id: str, finding: Mapping[str, Any], actor: str, purpose: str) -> dict[str, Any]:
        if not self._fetchone("SELECT id FROM source_assets WHERE id=?", (asset_id,)):
            raise ValidationError("source asset does not exist")
        required = {"entity_type", "location", "confidence", "action"}
        missing = required - finding.keys()
        if missing:
            raise ValidationError(f"missing PII finding fields: {', '.join(sorted(missing))}")
        record = {"id": str(uuid.uuid4()), "asset_id": asset_id, "entity_type": str(finding["entity_type"]),
                  "location": str(finding["location"]), "confidence": float(finding["confidence"]),
                  "detector_version": str(finding.get("detector_version", "mvp-1")), "action": str(finding["action"]),
                  "review_status": str(finding.get("review_status", "PENDING")), "created_at": utc_now()}
        with self.db.transaction() as cursor:
            cursor.execute("INSERT INTO pii_findings VALUES (?,?,?,?,?,?,?,?,?)", tuple(record.values()))
        self._audit("PII_FINDING_RECORDED", actor, purpose, record["id"], {"asset_id": asset_id, "action": record["action"]})
        return record

    def create_contract(self, contract: Mapping[str, Any], actor: str, purpose: str) -> dict[str, Any]:
        required = {"version", "required_fields", "max_age_days", "quality_assertions"}
        missing = required - contract.keys()
        if missing:
            raise ValidationError(f"missing contract fields: {', '.join(sorted(missing))}")
        lifecycle_state = str(contract.get("lifecycle_state", "ACTIVE")).upper()
        if lifecycle_state not in {"PENDING", "ACTIVE", "RETIRED"}:
            raise ValidationError("lifecycle_state must be PENDING, ACTIVE, or RETIRED")
        result = {"id": str(uuid.uuid4()), **dict(contract), "lifecycle_state": lifecycle_state, "created_at": utc_now()}
        contract_body = dict(contract)
        contract_body["lifecycle_state"] = lifecycle_state
        with self.db.transaction() as cursor:
            cursor.execute("INSERT INTO data_contracts VALUES (?,?,?,?,?)", (result["id"], str(contract["version"]), lifecycle_state, canonical(contract_body), result["created_at"]))
        self._audit("DATA_CONTRACT_CREATED", actor, purpose, result["id"], {"version": contract["version"], "lifecycle_state": lifecycle_state})
        return result

    def _validate_contract(self, contract: Mapping[str, Any], samples: Sequence[Mapping[str, Any]], assets: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        failures: list[dict[str, Any]] = []
        required_fields = set(contract["required_fields"])
        for index, sample in enumerate(samples):
            missing = required_fields - sample.keys()
            if missing:
                failures.append({"assertion": "schema", "sample": index, "missing": sorted(missing)})
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(contract["max_age_days"]))
        for asset in assets:
            if datetime.fromisoformat(asset["created_at"]) < cutoff:
                failures.append({"assertion": "freshness", "asset_id": asset["id"]})
        for assertion in contract["quality_assertions"]:
            field, minimum = assertion.get("field"), assertion.get("min")
            if field and minimum is not None:
                for index, sample in enumerate(samples):
                    if float(sample.get(field, 0)) < float(minimum):
                        failures.append({"assertion": "quality", "sample": index, "field": field, "expected_min": minimum})
        return failures

    def freeze_dataset(self, payload: Mapping[str, Any], actor: str, purpose: str) -> dict[str, Any]:
        contract = self._fetchone("SELECT * FROM data_contracts WHERE id=?", (str(payload.get("contract_id", "")),))
        if not contract:
            raise ValidationError("data contract does not exist")
        contract_data, samples, asset_ids = json.loads(contract["contract_json"]), list(payload.get("samples", [])), list(payload.get("asset_ids", []))
        if contract["lifecycle_state"] == "RETIRED":
            raise ValidationError("retired data contract cannot be used")
        if not samples or not asset_ids:
            raise ValidationError("samples and asset_ids must be non-empty")
        assets: list[dict[str, Any]] = []
        for asset_id in asset_ids:
            asset = self._fetchone("SELECT * FROM source_assets WHERE id=?", (asset_id,))
            if not asset:
                raise ValidationError(f"source asset does not exist: {asset_id}")
            assets.append(asset)
        contract_failures = self._validate_contract(contract_data, samples, assets)
        failures = list(contract_failures) if contract["lifecycle_state"] == "ACTIVE" else []
        observed_contract_failures = contract_failures if contract["lifecycle_state"] == "PENDING" else []
        for asset in assets:
            policy = self._fetchone("SELECT decision_json FROM policy_decisions WHERE subject_id=? ORDER BY created_at DESC LIMIT 1", (asset["id"],))
            if asset["status"] != "REGISTERED" or not policy or json.loads(policy["decision_json"])["decision"] != "ALLOW":
                failures.append({"assertion": "policy", "asset_id": asset["id"]})
            pii = self._fetchone("SELECT id FROM pii_findings WHERE asset_id=? AND (review_status != 'RESOLVED' OR action = 'ALLOW') LIMIT 1", (asset["id"],))
            if pii:
                failures.append({"assertion": "pii", "asset_id": asset["id"]})
        dataset_id, now = str(uuid.uuid4()), utc_now()
        manifest = {"dataset_id": dataset_id, "name": payload.get("name", "dataset"), "contract_id": contract["id"],
                    "contract_version": contract["version"], "contract_lifecycle_state": contract["lifecycle_state"],
                    "asset_ids": asset_ids, "samples": samples,
                    "checks": {"passed": not failures, "failures": failures,
                               "observed_contract_failures": observed_contract_failures},
                    "policy_version": self.POLICY_VERSION}
        status, manifest_hash = ("FROZEN" if not failures else "BLOCKED"), digest(manifest)
        with self.db.transaction() as cursor:
            cursor.execute("INSERT INTO dataset_versions VALUES (?,?,?,?,?,?,?,?,?)", (dataset_id, manifest["name"], contract["id"], canonical(asset_ids), canonical(samples), manifest_hash, status, now, now if status == "FROZEN" else None))
        self._audit("DATASET_FREEZE_" + status, actor, purpose, dataset_id, {"manifest_hash": manifest_hash, "failures": failures})
        lineage_run_id = str(uuid.uuid4())
        self._lineage("START", "dataset_freeze", asset_ids, [dataset_id], {"contract": contract["version"]}, lineage_run_id)
        self._lineage("COMPLETE" if status == "FROZEN" else "FAIL", "dataset_freeze", asset_ids, [dataset_id], {"failures": failures}, lineage_run_id)
        if failures:
            raise PolicyBlockedError({"dataset_id": dataset_id, "decision": "BLOCK", "reason_codes": [x["assertion"] for x in failures], "failures": failures})
        return {"id": dataset_id, "status": status, "manifest": manifest, "manifest_hash": manifest_hash}

    def create_reward_spec(self, spec: Mapping[str, Any], actor: str, purpose: str) -> dict[str, Any]:
        if not spec.get("version") or not isinstance(spec.get("weights"), Mapping):
            raise ValidationError("RewardSpec requires version and weights")
        result = {"id": str(uuid.uuid4()), **dict(spec), "created_at": utc_now()}
        with self.db.transaction() as cursor:
            cursor.execute("INSERT INTO reward_specs VALUES (?,?,?,?)", (result["id"], str(spec["version"]), canonical(spec), result["created_at"]))
        self._audit("REWARD_SPEC_CREATED", actor, purpose, result["id"], {"version": spec["version"]})
        return result

    def record_episode(self, dataset_id: str, reward_spec_id: str, episode: Mapping[str, Any], actor: str, purpose: str) -> dict[str, Any]:
        if not self._fetchone("SELECT id FROM dataset_versions WHERE id=? AND status='FROZEN'", (dataset_id,)):
            raise ValidationError("episode requires a frozen dataset")
        if not self._fetchone("SELECT id FROM reward_specs WHERE id=?", (reward_spec_id,)):
            raise ValidationError("RewardSpec does not exist")
        if not episode.get("prompt") or not isinstance(episode.get("candidates"), list):
            raise ValidationError("episode requires prompt and candidates")
        result = {"id": str(uuid.uuid4()), "dataset_id": dataset_id, "reward_spec_id": reward_spec_id, "episode": dict(episode), "created_at": utc_now()}
        with self.db.transaction() as cursor:
            cursor.execute("INSERT INTO episodes VALUES (?,?,?,?,?)", (result["id"], dataset_id, reward_spec_id, canonical(episode), result["created_at"]))
        self._audit("EPISODE_RECORDED", actor, purpose, result["id"], {"dataset_id": dataset_id, "reward_spec_id": reward_spec_id})
        return result

    def replay_episode(self, episode_id: str, actor: str, purpose: str) -> dict[str, Any]:
        row = self._fetchone("SELECT * FROM episodes WHERE id=?", (episode_id,))
        if not row:
            raise ValidationError("episode does not exist")
        spec_row = self._fetchone("SELECT spec_json FROM reward_specs WHERE id=?", (row["reward_spec_id"],))
        spec, episode = json.loads(spec_row["spec_json"]), json.loads(row["episode_json"])
        replay = []
        for candidate in episode["candidates"]:
            vector = candidate.get("reward_vector", {})
            score = sum(float(spec["weights"].get(key, 0)) * float(vector.get(key, 0)) for key in spec["weights"])
            replay.append({"candidate_id": candidate.get("id"), "scalar_reward": score, "reward_vector": vector})
        result = {"episode_id": episode_id, "reward_spec_id": row["reward_spec_id"], "replayed_rewards": replay}
        self._audit("EPISODE_REPLAYED", actor, purpose, episode_id, result)
        return result

    def export_dataset(self, dataset_id: str, kind: str, actor: str, purpose: str) -> dict[str, Any]:
        if kind not in {"lora", "grpo"}:
            raise ValidationError("export kind must be lora or grpo")
        dataset = self._fetchone("SELECT * FROM dataset_versions WHERE id=?", (dataset_id,))
        if not dataset or dataset["status"] != "FROZEN":
            raise PolicyBlockedError({"decision": "BLOCK", "reason_codes": ["DATASET_NOT_FROZEN"]})
        contract = self._fetchone("SELECT lifecycle_state FROM data_contracts WHERE id=?", (dataset["contract_id"],))
        if not contract or contract["lifecycle_state"] != "ACTIVE":
            raise PolicyBlockedError({"decision": "BLOCK", "reason_codes": ["CONTRACT_NOT_ACTIVE"]})
        manifest = {"format": "controlled-data-export-manifest/v1", "kind": kind, "dataset_id": dataset_id,
                    "dataset_manifest_hash": dataset["manifest_hash"], "policy_version": self.POLICY_VERSION,
                    "sample_count": len(json.loads(dataset["sample_json"])), "asset_ids": json.loads(dataset["asset_ids_json"]),
                    "created_at": utc_now(), "non_sensitive_authorized_only": True}
        if kind == "grpo":
            manifest["reward_specs"] = self._reward_spec_ids(dataset_id)
        result = {"id": str(uuid.uuid4()), "manifest": manifest, "manifest_hash": digest(manifest)}
        with self.db.transaction() as cursor:
            cursor.execute("INSERT INTO exports VALUES (?,?,?,?,?,?)", (result["id"], dataset_id, kind, canonical(manifest), result["manifest_hash"], utc_now()))
        self._audit("DATASET_EXPORTED", actor, purpose, result["id"], {"dataset_id": dataset_id, "kind": kind, "manifest_hash": result["manifest_hash"]})
        lineage_run_id = str(uuid.uuid4())
        self._lineage("START", "dataset_export", [dataset_id], [result["id"]], {"kind": kind}, lineage_run_id)
        self._lineage("COMPLETE", "dataset_export", [dataset_id], [result["id"]], {"manifest_hash": result["manifest_hash"]}, lineage_run_id)
        return result

    def _reward_spec_ids(self, dataset_id: str) -> list[str]:
        with self.db.transaction() as cursor:
            cursor.execute("SELECT reward_spec_id FROM episodes WHERE dataset_id=?", (dataset_id,))
            return [row[0] for row in cursor.fetchall()]

    def verify_audit_chain(self) -> dict[str, Any]:
        with self.db.transaction() as cursor:
            cursor.execute("SELECT * FROM audit_events ORDER BY sequence")
            events = [dict(row) for row in cursor.fetchall()]
        previous_hash = "GENESIS"
        for event in events:
            body = {"id": event["id"], "event_type": event["event_type"], "actor": event["actor"], "purpose": event["purpose"],
                    "subject_id": event["subject_id"], "payload": json.loads(event["payload_json"]), "previous_hash": previous_hash, "created_at": event["created_at"]}
            if event["previous_hash"] != previous_hash or event["event_hash"] != digest(body):
                return {"valid": False, "sequence": event["sequence"]}
            previous_hash = event["event_hash"]
        return {"valid": True, "events": len(events), "head_hash": previous_hash}
