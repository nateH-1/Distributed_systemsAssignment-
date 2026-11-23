import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Settings, Site
from frontend import FrontendApplication
from storage_client import StorageResponse


class FakeStorage:
    def __init__(self):
        self.messages = {}

    def store(self, message):
        self.messages.setdefault(message["to"], {})[message["id"]] = message
        return StorageResponse(200, {})

    def list_mailbox(self, user):
        messages = self.messages.get(user, {}).values()
        return StorageResponse(200, {"messages": [{key: message[key] for key in ("id", "from", "subject", "read")} for message in messages]})

    def get_message(self, user, message_id):
        message = self.messages.get(user, {}).get(message_id)
        return StorageResponse(200, message) if message else StorageResponse(404, {"error": "absent"})

    def set_read(self, user, message_id, read):
        message = self.messages.get(user, {}).get(message_id)
        if not message:
            return StorageResponse(404, {"error": "absent"})
        message["read"] = read
        return StorageResponse(200, {key: message[key] for key in ("id", "from", "subject", "read")})

    def delete_message(self, user, message_id):
        if message_id not in self.messages.get(user, {}):
            return StorageResponse(404, {"error": "absent"})
        del self.messages[user][message_id]
        return StorageResponse(200, {})


class FrontendTests(unittest.TestCase):
    def setUp(self):
        settings = Settings("site-a", (Site("site-a", "127.0.0.1"),), ("node-2", "node-3"))
        self.app = FrontendApplication(settings, FakeStorage())

    def test_mail_lifecycle(self):
        created = self.app.dispatch("POST", "/mail", {"from": "bob", "to": "alice", "subject": "hi", "body": "hello"})
        self.assertEqual(created.status, 200)
        message_id = created.body["id"]

        listed = self.app.dispatch("GET", "/mailbox/alice")
        self.assertEqual(listed.body["messages"][0]["read"], False)
        self.assertEqual(self.app.dispatch("PUT", f"/mailbox/alice/{message_id}", {"read": True}).status, 200)
        self.assertEqual(self.app.dispatch("GET", f"/mailbox/alice/{message_id}").body["body"], "hello")
        self.assertEqual(self.app.dispatch("DELETE", f"/mailbox/alice/{message_id}").status, 200)
        self.assertEqual(self.app.dispatch("GET", f"/mailbox/alice/{message_id}").status, 404)

    def test_rejects_invalid_message(self):
        response = self.app.dispatch("POST", "/mail", {"from": "bob", "to": "alice"})
        self.assertEqual(response.status, 400)


if __name__ == "__main__":
    unittest.main()
