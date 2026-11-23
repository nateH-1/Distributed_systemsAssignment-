import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Site, load_settings
from routing import home_site


SITES = (
    Site("site-a", "10.0.0.11"),
    Site("site-b", "10.0.0.12"),
    Site("site-c", "10.0.0.13"),
)


class RoutingTests(unittest.TestCase):
    def test_same_mailbox_always_selects_the_same_site(self):
        first = home_site("alice", SITES)
        self.assertTrue(all(home_site("alice", SITES) == first for _ in range(25)))

    def test_routing_is_deterministic_across_equivalent_site_lists(self):
        copied_sites = tuple(Site(site.name, site.host) for site in SITES)
        for user in ("alice", "bob", "michael", "team-mailbox", "user@example.edu"):
            self.assertEqual(home_site(user, SITES), home_site(user, copied_sites))

    def test_many_mailboxes_are_spread_across_all_sites(self):
        counts = {site.name: 0 for site in SITES}
        for number in range(600):
            counts[home_site(f"mailbox-{number}", SITES).name] += 1
        self.assertTrue(all(count > 0 for count in counts.values()), counts)
        self.assertLess(max(counts.values()) - min(counts.values()), 120, counts)

    def test_empty_mailbox_is_rejected(self):
        with self.assertRaises(ValueError):
            home_site("", SITES)


class ConfigTests(unittest.TestCase):
    def test_loads_a_shared_site_configuration(self):
        environment = {
            "MAIL_SITE_NAME": "site-b",
            "MAIL_SITES": '[{"name":"site-a","host":"10.0.0.11"},{"name":"site-b","host":"10.0.0.12"}]',
            "MAIL_STORAGE_NODES": "node-2,node-3",
            "MAIL_TIMEOUT_SECONDS": "1.25",
        }
        with patch.dict(os.environ, environment, clear=True):
            settings = load_settings()
        self.assertEqual(settings.local_site, Site("site-b", "10.0.0.12"))
        self.assertEqual(settings.storage_nodes, ("node-2", "node-3"))
        self.assertEqual(settings.timeout_seconds, 1.25)

    def test_rejects_duplicate_site_names(self):
        with patch.dict(os.environ, {"MAIL_SITES": '[{"name":"same","host":"a"},{"name":"same","host":"b"}]'}, clear=True):
            with self.assertRaises(ValueError):
                load_settings()


if __name__ == "__main__":
    unittest.main()
