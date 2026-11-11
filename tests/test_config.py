import tempfile
import unittest
from pathlib import Path

from samsung_remote_blocker.config import ConfigError, load, parse_apps

REGISTRY = {
    "netflix": {"name": "Netflix", "ids": {"1": []}},
    "prime": {"name": "Prime Video", "ids": {"2": [], "3": []}},
}


class ParseAppsTest(unittest.TestCase):
    def test_slugs_expand_to_all_ids(self):
        self.assertEqual(
            parse_apps("Netflix, prime", REGISTRY), {"1": "Netflix", "2": "Prime Video", "3": "Prime Video"}
        )

    def test_raw_ids_with_and_without_name(self):
        self.assertEqual(parse_apps("99:Foo,100", REGISTRY), {"99": "Foo", "100": "100"})

    def test_unknown_slug_is_rejected(self):
        with self.assertRaisesRegex(ConfigError, "netflx"):
            parse_apps("netflx", REGISTRY)

    def test_empty_entries_are_skipped(self):
        self.assertEqual(parse_apps("netflix,,", REGISTRY), {"1": "Netflix"})


class LoadTest(unittest.TestCase):
    def test_defaults(self):
        s = load({})
        self.assertIsNone(s.tv_ip)
        self.assertIn("3201907018807", s.blocked)
        self.assertEqual(s.poll_seconds, 0.25)
        with self.assertRaises(ConfigError):
            s.require_tv_ip()

    def test_tv_ip_must_be_an_ip(self):
        self.assertEqual(load({"TV_IP": " 192.168.0.110 "}).tv_ip, "192.168.0.110")
        with self.assertRaisesRegex(ConfigError, "TV_IP"):
            load({"TV_IP": "living-room.lan"})

    def test_numbers_are_validated(self):
        for bad in ("abc", "0", "-1"):
            with self.assertRaisesRegex(ConfigError, "POLL_SECONDS"):
                load({"POLL_SECONDS": bad})

    def test_token_is_not_in_repr(self):
        self.assertNotIn("s3cret", repr(load({"PLEX_TOKEN": "s3cret"})))

    def test_plex_url_trailing_slash(self):
        self.assertEqual(load({"PLEX_URL": "http://pms:32400/"}).plex_url, "http://pms:32400")


class PlexTokenTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def write(self, name, text):
        path = self.dir / name
        path.write_text(text)
        return str(path)

    def token(self, **env):
        return load({"DATA_DIR": str(self.dir), **env}).plex_token

    def test_env_wins(self):
        self.write(".plex-token", "from-file")
        self.assertEqual(self.token(PLEX_TOKEN="from-env"), "from-env")

    def test_saved_file(self):
        self.write(".plex-token", "from-file\n")
        self.assertEqual(self.token(), "from-file")

    def test_secret_file_overrides_default_path(self):
        self.write(".plex-token", "default")
        secret = self.write("secret", "from-secret")
        self.assertEqual(self.token(PLEX_TOKEN_FILE=secret), "from-secret")

    def test_plex_login_writes_where_the_token_is_read(self):
        secret = str(self.dir / "custom")
        s = load({"DATA_DIR": str(self.dir), "PLEX_TOKEN_FILE": secret})
        self.assertEqual(str(s.plex_token_file), secret)

    def test_server_preferences(self):
        prefs = self.write("Preferences.xml", '<Preferences PlexOnlineToken="from-pms"/>')
        self.assertEqual(self.token(PLEX_PREFERENCES=prefs), "from-pms")

    def test_bad_preferences_is_a_config_error(self):
        with self.assertRaises(ConfigError):
            self.token(PLEX_PREFERENCES=str(self.dir / "missing.xml"))

    def test_none(self):
        self.assertIsNone(self.token())


if __name__ == "__main__":
    unittest.main()
