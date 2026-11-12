import tempfile
import unittest
from pathlib import Path
from unittest import mock

from samsung_remote_blocker.config import ConfigError
from samsung_remote_blocker.privileges import drop_root, target_ids


class TargetIdsTest(unittest.TestCase):
    def test_puid_wins(self):
        self.assertEqual(target_ids({"PUID": "998", "PGID": "100"}), (998, 100))
        self.assertEqual(target_ids({"PUID": "998"}), (998, 998))

    def test_bad_puid(self):
        with self.assertRaises(ConfigError):
            target_ids({"PUID": "plex"})

    def test_follows_plex_preferences_owner(self):
        st = mock.Mock(st_uid=998, st_gid=100)
        with mock.patch("os.stat", return_value=st):
            self.assertEqual(target_ids({"PLEX_PREFERENCES": "/plex/Preferences.xml"}), (998, 100))

    def test_root_owned_or_missing_preferences_falls_back(self):
        with mock.patch("os.stat", return_value=mock.Mock(st_uid=0, st_gid=0)):
            self.assertEqual(target_ids({"PLEX_PREFERENCES": "/x"}), (1000, 1000))
        self.assertEqual(target_ids({"PLEX_PREFERENCES": "/missing"}), (1000, 1000))
        self.assertEqual(target_ids({}), (1000, 1000))


class DropRootTest(unittest.TestCase):
    def setUp(self):
        self.data = Path(tempfile.mkdtemp())
        (self.data / "tv-token-1.txt").write_text("t")
        patches = {name: mock.patch(f"os.{name}") for name in ("chown", "setgroups", "setgid", "setuid")}
        self.os = {name: p.start() for name, p in patches.items()}
        for p in patches.values():
            self.addCleanup(p.stop)

    def test_non_root_is_left_alone(self):
        with mock.patch("os.getuid", return_value=1000):
            drop_root({}, self.data)
        self.os["setuid"].assert_not_called()

    def test_root_chowns_data_then_drops(self):
        with mock.patch("os.getuid", return_value=0), self.assertLogs("samsung_remote_blocker.privileges"):
            drop_root({"PUID": "998", "PGID": "100"}, self.data)
        chowned = {Path(c.args[0]) for c in self.os["chown"].call_args_list}
        self.assertEqual(chowned, {self.data, self.data / "tv-token-1.txt"})
        self.os["setgroups"].assert_called_once_with([])
        self.os["setgid"].assert_called_once_with(100)
        self.os["setuid"].assert_called_once_with(998)

    def test_refuses_to_stay_root(self):
        with mock.patch("os.getuid", return_value=0), self.assertRaises(ConfigError):
            drop_root({"PUID": "0"}, self.data)
        self.os["setuid"].assert_not_called()


if __name__ == "__main__":
    unittest.main()
