"""Node-1 adapter for the storage service owned by the storage team.

Storage-node contract (JSON over HTTP, port 9000 by default):
  POST   /internal/mail                 stores the complete message
  GET    /internal/mailbox/<user>       returns {"messages": [summary, ...]}
  GET    /internal/mailbox/<user>/<id>  returns the complete message or 404
  PUT    /internal/mailbox/<user>/<id>  receives {"read": bool}; returns summary
  DELETE /internal/mailbox/<user>/<id>  returns 200 or 404

Both node-2 and node-3 should implement this interface. Replication and
catch-up remain the storage service's responsibility; node-1 merely uses an
available storage node and never retains mailbox data locally.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from config import Settings


class StorageUnavailable(Exception):
    pass


@dataclass
class StorageResponse:
    status: int
    body: dict[str, Any]


class StorageClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> StorageResponse:
        last_error: Exception | None = None
        for host in self.settings.storage_nodes:
            url = f"http://{host}:{self.settings.storage_port}{path}"
            data = None if body is None else json.dumps(body).encode("utf-8")
            request = Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
            try:
                with urlopen(request, timeout=self.settings.timeout_seconds) as response:
                    return StorageResponse(response.status, _read_json(response.read()))
            except HTTPError as exc:
                return StorageResponse(exc.code, _read_json(exc.read()))
            except (URLError, TimeoutError, OSError) as exc:
                last_error = exc
        raise StorageUnavailable("all local storage nodes are unreachable") from last_error

    def store(self, message: dict[str, Any]) -> StorageResponse:
        return self.request("POST", "/internal/mail", message)

    def list_mailbox(self, user: str) -> StorageResponse:
        return self.request("GET", f"/internal/mailbox/{quote(user, safe='')}")

    def get_message(self, user: str, message_id: str) -> StorageResponse:
        return self.request("GET", f"/internal/mailbox/{quote(user, safe='')}/{quote(message_id, safe='')}")

    def set_read(self, user: str, message_id: str, read: bool) -> StorageResponse:
        return self.request("PUT", f"/internal/mailbox/{quote(user, safe='')}/{quote(message_id, safe='')}", {"read": read})

    def delete_message(self, user: str, message_id: str) -> StorageResponse:
        return self.request("DELETE", f"/internal/mailbox/{quote(user, safe='')}/{quote(message_id, safe='')}")


def _read_json(raw: bytes) -> dict[str, Any]:
    if not raw:
        return {}
    decoded = json.loads(raw.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("storage service returned a non-object JSON response")
    return decoded
