"""HTTP front end for node-1 of the distributed mailbox service."""

from __future__ import annotations

from dataclasses import dataclass
import json
import socket
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import uuid

from config import Settings, load_settings
from routing import home_site
from storage_client import StorageClient, StorageResponse, StorageUnavailable


@dataclass
class Response:
    status: int
    body: dict[str, Any]


class MailStorage(Protocol):
    def store(self, message: dict[str, Any]) -> StorageResponse: ...
    def list_mailbox(self, user: str) -> StorageResponse: ...
    def get_message(self, user: str, message_id: str) -> StorageResponse: ...
    def set_read(self, user: str, message_id: str, read: bool) -> StorageResponse: ...
    def delete_message(self, user: str, message_id: str) -> StorageResponse: ...


class FrontendApplication:
    def __init__(self, settings: Settings, storage: MailStorage | None = None):
        self.settings = settings
        self.storage = storage or StorageClient(settings)

    def dispatch(self, method: str, path: str, body: dict[str, Any] | None = None) -> Response:
        if path == "/status" and method == "GET":
            return self._status()
        segments = [part for part in path.split("/") if part]
        if not segments:
            return Response(404, {"error": "not found"})

        try:
            user = self._request_user(method, segments, body)
        except ValueError as exc:
            return Response(400, {"error": str(exc)})

        destination = home_site(user, self.settings.sites)
        if destination.name != self.settings.site_name:
            return self._forward(destination.host, method, path, body)

        try:
            return self._handle_local(method, segments, body)
        except StorageUnavailable:
            return Response(503, {"error": "local storage nodes are unreachable"})
        except ValueError as exc:
            return Response(400, {"error": str(exc)})

    def _request_user(self, method: str, segments: list[str], body: dict[str, Any] | None) -> str:
        if method == "POST" and segments == ["mail"]:
            if not isinstance(body, dict):
                raise ValueError("request body must be a JSON object")
            recipient = body.get("to")
            if not isinstance(recipient, str) or not recipient:
                raise ValueError("field 'to' must be a non-empty string")
            return recipient
        if len(segments) >= 2 and segments[0] == "mailbox":
            return segments[1]
        raise ValueError("unknown endpoint")

    def _handle_local(self, method: str, segments: list[str], body: dict[str, Any] | None) -> Response:
        if method == "POST" and segments == ["mail"]:
            message = self._validated_message(body)
            message["id"] = uuid.uuid4().hex
            result = self.storage.store(message)
            if result.status != 200:
                return Response(result.status, result.body)
            return Response(200, {"id": message["id"], "to": message["to"], "site": self.settings.site_name})

        if len(segments) == 2 and segments[0] == "mailbox" and method == "GET":
            result = self.storage.list_mailbox(segments[1])
            if result.status != 200:
                return Response(result.status, result.body)
            return Response(200, {"user": segments[1], "site": self.settings.site_name, "messages": result.body.get("messages", [])})

        if len(segments) == 3 and segments[0] == "mailbox":
            user, message_id = segments[1], segments[2]
            if method == "GET":
                result = self.storage.get_message(user, message_id)
            elif method == "PUT":
                if not isinstance(body, dict) or type(body.get("read")) is not bool:
                    raise ValueError("body must be exactly {'read': true} or {'read': false}")
                result = self.storage.set_read(user, message_id, body["read"])
            elif method == "DELETE":
                result = self.storage.delete_message(user, message_id)
            else:
                return Response(404, {"error": "not found"})
            return Response(result.status, result.body)

        return Response(404, {"error": "not found"})

    @staticmethod
    def _validated_message(body: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise ValueError("request body must be a JSON object")
        required = ("from", "to", "subject", "body")
        if set(body) != set(required) or any(not isinstance(body[key], str) for key in required):
            raise ValueError("body must contain only string fields: from, to, subject, body")
        if not body["from"] or not body["to"]:
            raise ValueError("from and to must not be empty")
        return {**body, "read": False}

    def _forward(self, host: str, method: str, path: str, body: dict[str, Any] | None) -> Response:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(
            f"http://{host}:{self.settings.peer_port}{path}", data=data, method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.settings.timeout_seconds) as result:
                return Response(result.status, _json_object(result.read()))
        except HTTPError as exc:
            return Response(exc.code, _json_object(exc.read()))
        except (URLError, TimeoutError, OSError):
            return Response(503, {"error": f"home site at {host} is unreachable"})

    def _status(self) -> Response:
        sites = {site.name: _reachable(site.host, self.settings.peer_port, self.settings.timeout_seconds) for site in self.settings.sites}
        storage_nodes = {host: _reachable(host, self.settings.storage_port, self.settings.timeout_seconds) for host in self.settings.storage_nodes}
        return Response(200, {"site": self.settings.site_name, "sites": sites, "storage_nodes": storage_nodes})


def _reachable(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _json_object(raw: bytes) -> dict[str, Any]:
    if not raw:
        return {}
    value = json.loads(raw.decode("utf-8"))
    return value if isinstance(value, dict) else {"error": "invalid JSON response from peer"}


if __name__ == "__main__":
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    application = FrontendApplication(load_settings())

    class Handler(BaseHTTPRequestHandler):
        def _respond(self, response: Response) -> None:
            encoded = json.dumps(response.body).encode("utf-8")
            self.send_response(response.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _body(self) -> dict[str, Any] | None:
            length = int(self.headers.get("Content-Length", "0"))
            if not length:
                return None
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except json.JSONDecodeError:
                return {"__invalid_json__": True}

        def _dispatch(self) -> None:
            self._respond(application.dispatch(self.command, self.path, self._body()))

        do_GET = _dispatch
        do_POST = _dispatch
        do_PUT = _dispatch
        do_DELETE = _dispatch

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("0.0.0.0", application.settings.peer_port), Handler)
    print(f"front end for {application.settings.site_name} listening on port {application.settings.peer_port}")
    server.serve_forever()
