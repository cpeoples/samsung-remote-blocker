import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from samsung_remote_blocker.tv import PAIR_TIMEOUT, TV


def response(status, body=None):
    r = mock.Mock(status_code=status)
    r.json.return_value = body
    r.raise_for_status.side_effect = requests.HTTPError(str(status)) if status >= 400 else None
    return r


class TVTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("samsung_remote_blocker.tv.SamsungTVWS")
        self.ws = patcher.start().return_value
        self.addCleanup(patcher.stop)
        self.tv = TV("192.168.0.110", Path("/tmp/token"))
        self.http = mock.Mock()
        self.real_http, self.tv._http = self.tv._http, self.http

    def test_app_status(self):
        self.http.get.return_value = response(200, {"visible": True})
        self.assertEqual(self.tv.app("111"), {"visible": True})
        self.http.get.assert_called_once_with(
            "https://192.168.0.110:8002/api/v2/applications/111", timeout=self.tv.timeout
        )

    def test_uninstalled_app_is_none(self):
        self.http.get.return_value = response(404)
        self.assertIsNone(self.tv.app("111"))

    def test_errors_raise(self):
        self.http.get.return_value = response(500)
        with self.assertRaises(requests.HTTPError):
            self.tv.app("111")
        self.http.post.return_value = response(500)
        with self.assertRaises(requests.HTTPError):
            self.tv.launch("111")
        self.http.delete.return_value = response(500)
        with self.assertRaises(requests.HTTPError):
            self.tv.close("111")

    def test_only_idempotent_calls_are_retried(self):
        retry = self.real_http.get_adapter("https://192.168.0.110:8002/").max_retries
        self.assertEqual(retry.total, 1)
        self.assertEqual(set(retry.allowed_methods), {"GET", "DELETE"})

    def test_installed_apps_tolerates_no_answer(self):
        self.ws.app_list.return_value = None
        self.assertEqual(self.tv.installed_apps(), [])

    def test_failed_key_drops_the_socket(self):
        self.ws.send_key.side_effect = OSError("broken pipe")
        with self.assertRaises(OSError):
            self.tv.send_key("KEY_HOME")
        self.ws.close.assert_called_once()

    def test_key(self):
        self.tv.send_key("KEY_PLAY")
        self.ws.send_key.assert_called_once_with("KEY_PLAY")
        self.ws.close.assert_not_called()

    def test_paired_reads_the_token_file(self):
        with tempfile.TemporaryDirectory() as d:
            self.tv.token_file = Path(d) / "tv-token.txt"
            self.assertFalse(self.tv.paired)
            self.tv.token_file.write_text("\n")
            self.assertFalse(self.tv.paired)
            self.tv.token_file.write_text("12345678")
            self.assertTrue(self.tv.paired)

    def test_pair_waits_longer_than_normal_calls(self):
        with mock.patch("samsung_remote_blocker.tv.SamsungTVWS") as ws:
            self.tv.pair()
        self.assertEqual(ws.call_args.kwargs["timeout"], PAIR_TIMEOUT)
        ws.return_value.__enter__.return_value.open.assert_called_once()


if __name__ == "__main__":
    unittest.main()
