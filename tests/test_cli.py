import contextlib
import io
import logging
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from samsung_remote_blocker.__main__ import main


class CliTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(os.umask, os.umask(0o022))  # main() sets the process umask
        patcher = mock.patch("logging.basicConfig")  # main() would configure logging for every later test
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *argv, env=None):
        stderr = io.StringIO()
        with (
            mock.patch.dict(os.environ, env or {}, clear=True),
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as exit,
        ):
            main(list(argv))
        return exit.exception.code, stderr.getvalue()

    def test_bad_scan_range_is_a_usage_error(self):
        for cidr in ("10.0.0.0/8", "nonsense"):
            code, err = self.run_cli("discover", "--scan", cidr)
            self.assertEqual(code, 2)
            self.assertIn("--scan", err)

    def test_missing_tv_ip(self):
        code, _ = self.run_cli("app", "netflix")
        self.assertIn("TV_IP is not set", code)

    def test_unreachable_tv_is_one_line_not_a_traceback(self):
        with mock.patch(
            "samsung_remote_blocker.tv.TV.app", side_effect=requests.ConnectionError("No route to host")
        ):
            code, _ = self.run_cli("app", "netflix", env={"TV_IP": "192.168.0.110"})
        self.assertEqual(code, "Error: No route to host")

    def test_files_written_are_owner_only(self):
        data = Path(tempfile.mkdtemp())
        with (
            mock.patch(
                "samsung_remote_blocker.plex.link_device", side_effect=lambda path: path.write_text("tok")
            ),
            mock.patch.dict(os.environ, {"DATA_DIR": str(data)}, clear=True),
        ):
            main(["plex-login"])
        mode = stat.S_IMODE((data / ".plex-token").stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_discover_table_and_empty_hint(self):
        tvs = [
            {
                "ip": "192.168.0.110",
                "name": "Living Room",
                "model": "UN65DU8000FXZA",
                "mac": None,
                "power": "on",
            }
        ]
        out = io.StringIO()
        with (
            mock.patch("samsung_remote_blocker.discover.discover", return_value=tvs),
            contextlib.redirect_stdout(out),
        ):
            main(["discover"])
        self.assertIn("192.168.0.110    Living Room", out.getvalue())
        with mock.patch("samsung_remote_blocker.discover.discover", return_value=[]):
            code, _ = self.run_cli("discover")
        self.assertIn("--scan", code)

    def test_pair_opens_the_channel(self):
        data = tempfile.mkdtemp()
        with (
            mock.patch("samsung_remote_blocker.tv.TV.pair") as pair,
            mock.patch.dict(os.environ, {"TV_IP": "192.168.0.110", "DATA_DIR": data}, clear=True),
            contextlib.redirect_stdout(io.StringIO()) as out,
        ):
            main(["pair"])
        pair.assert_called_once_with()
        self.assertIn("tv-token-192.168.0.110.txt", out.getvalue())

    def test_token_from_0_1_0_is_kept(self):
        data = Path(tempfile.mkdtemp())
        (data / "tv-token.txt").write_text("old")
        with (
            mock.patch("samsung_remote_blocker.tv.TV.pair"),
            mock.patch.dict(os.environ, {"TV_IP": "192.168.0.110", "DATA_DIR": str(data)}, clear=True),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            main(["pair"])
        self.assertEqual((data / "tv-token-192.168.0.110.txt").read_text(), "old")
        self.assertFalse((data / "tv-token.txt").exists())

    def test_unwritable_data_dir_is_a_config_error(self):
        data = Path(tempfile.mkdtemp())
        data.chmod(0o500)
        self.addCleanup(data.chmod, 0o700)
        code, _ = self.run_cli("pair", env={"TV_IP": "192.168.0.110", "DATA_DIR": str(data)})
        self.assertIn("isn't writable", code)

    def test_tv_token_is_never_logged(self):
        with (
            mock.patch("samsung_remote_blocker.plex.link_device"),
            mock.patch.dict(os.environ, {"DATA_DIR": tempfile.mkdtemp()}, clear=True),
        ):
            main(["plex-login"])
        self.addCleanup(logging.getLogger("samsungtvws").setLevel, logging.NOTSET)
        self.assertEqual(logging.getLogger("samsungtvws").level, logging.WARNING)


if __name__ == "__main__":
    unittest.main()
