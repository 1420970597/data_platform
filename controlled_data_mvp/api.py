"""Dependency-free JSON HTTP interface for the controlled data MVP."""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .service import ControlledDataService, PolicyBlockedError, ServiceError, ValidationError


class ApiHandler(BaseHTTPRequestHandler):
    service: ControlledDataService
    server_version = "ControlledDataMVP/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _json(self, status: int, body: dict[str, Any]) -> None:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if not length:
            return {}
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError("request body must be valid JSON") from exc
        if not isinstance(value, dict):
            raise ValidationError("JSON body must be an object")
        return value

    def _identity(self, body: dict[str, Any]) -> tuple[str, str]:
        actor, purpose = str(body.pop("actor", "")), str(body.pop("purpose", ""))
        if not actor or purpose not in {"training_data_preparation", "quality_review", "evaluation"}:
            raise ValidationError("actor and an allowed purpose are required")
        return actor, purpose

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json(200, {"status": "ok", "service": "controlled-data-mvp"})
        elif self.path == "/v1/audit/verify":
            self._json(200, self.service.verify_audit_chain())
        else:
            self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        try:
            body = self._body()
            actor, purpose = self._identity(body)
            path = self.path.rstrip("/")
            if path == "/v1/assets":
                result = self.service.register_asset(body, actor, purpose)
            elif path == "/v1/contracts":
                result = self.service.create_contract(body, actor, purpose)
            elif path == "/v1/datasets":
                result = self.service.freeze_dataset(body, actor, purpose)
            elif path == "/v1/reward-specs":
                result = self.service.create_reward_spec(body, actor, purpose)
            elif path == "/v1/episodes":
                result = self.service.record_episode(str(body.pop("dataset_id", "")), str(body.pop("reward_spec_id", "")), body, actor, purpose)
            elif path.startswith("/v1/assets/") and path.endswith("/pii-findings"):
                result = self.service.record_pii_finding(path.split("/")[3], body, actor, purpose)
            elif path.startswith("/v1/episodes/") and path.endswith("/replay"):
                result = self.service.replay_episode(path.split("/")[3], actor, purpose)
            elif path.startswith("/v1/datasets/") and path.endswith("/exports"):
                result = self.service.export_dataset(path.split("/")[3], str(body.get("kind", "")), actor, purpose)
            else:
                self._json(404, {"error": "not_found"})
                return
            self._json(HTTPStatus.CREATED, result)
        except PolicyBlockedError as exc:
            self._json(HTTPStatus.FORBIDDEN, {"error": "policy_blocked", "details": exc.result})
        except (ValidationError, ServiceError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "validation_error", "message": str(exc)})
        except Exception:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal_error"})


def serve(host: str = "127.0.0.1", port: int = 8080, dsn: str = "controlled_data_mvp.sqlite3") -> None:
    ApiHandler.service = ControlledDataService(dsn)
    server = ThreadingHTTPServer((host, port), ApiHandler)
    print(f"controlled-data-mvp listening on http://{host}:{port}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
