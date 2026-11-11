"""Rules for apps.json, the file most contributions touch."""

import json
import re
import unittest
from pathlib import Path

from samsung_remote_blocker import config

PATH = Path(config.__file__).with_name("apps.json")
SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
APP_ID = re.compile(r"^\d{9,13}$")  # Tizen store IDs, e.g. 3201907018807 (Netflix), 111299001912 (YouTube)
MODEL = re.compile(r"^[A-Z0-9]{6,20}$")  # e.g. UN65DU8000FXZA


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.registry = json.loads(PATH.read_text())

    def test_no_duplicate_slugs(self):
        pairs = json.loads(PATH.read_text(), object_pairs_hook=lambda p: p)
        slugs = [slug for slug, _ in pairs]
        self.assertEqual(len(slugs), len(set(slugs)), "duplicate slug in apps.json")

    def test_entries(self):
        for slug, app in self.registry.items():
            with self.subTest(slug=slug):
                self.assertRegex(slug, SLUG)
                self.assertEqual(set(app), {"name", "ids"})
                self.assertIsInstance(app["name"], str)
                self.assertTrue(app["name"].strip())
                self.assertIsInstance(app["ids"], dict)
                self.assertTrue(app["ids"], "an app needs at least one ID")
                for app_id, models in app["ids"].items():
                    self.assertRegex(app_id, APP_ID)
                    self.assertIsInstance(models, list)
                    self.assertEqual(len(models), len(set(models)), f"duplicate model under {app_id}")
                    for model in models:
                        self.assertRegex(model, MODEL)

    def test_ids_belong_to_one_app(self):
        seen = {}
        for slug, app in self.registry.items():
            for app_id in app["ids"]:
                self.assertNotIn(app_id, seen, f"{app_id} is listed under {seen.get(app_id)} and {slug}")
                seen[app_id] = slug

    def test_defaults_resolve(self):
        for slug in (config.DEFAULT_BLOCKED + "," + config.DEFAULT_ALLOWED).split(","):
            self.assertIn(slug, self.registry)

    def test_names_are_unique(self):
        names = [app["name"].lower() for app in self.registry.values()]
        self.assertEqual(len(names), len(set(names)))


if __name__ == "__main__":
    unittest.main()
