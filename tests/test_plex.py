import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from samsung_remote_blocker import plex
from samsung_remote_blocker.plex import Plex


def response(status=200, body=None):
    r = mock.Mock(status_code=status)
    r.json.return_value = body or {}
    return r


SESSIONS = {
    "MediaContainer": {
        "Metadata": [
            {"Player": {"address": "192.168.0.50", "state": "paused"}},
            {"Player": {"address": "192.168.0.110", "state": "playing"}},
        ]
    }
}


class PlexTest(unittest.TestCase):
    def setUp(self):
        self.plex = Plex("http://pms:32400", "secret")
        self.plex._http.get = mock.Mock(return_value=response(body=SESSIONS))

    def test_state_for_this_tv(self):
        self.assertEqual(self.plex.player_state("192.168.0.110"), "playing")

    def test_no_session_for_this_tv(self):
        self.assertIsNone(self.plex.player_state("192.168.0.99"))
        self.plex._http.get.return_value = response(body={"MediaContainer": {"size": 0}})
        self.assertIsNone(self.plex.player_state("192.168.0.110"))

    def test_token_is_a_header_not_in_the_url(self):
        self.plex.player_state("192.168.0.110")
        url = self.plex._http.get.call_args.args[0]
        self.assertNotIn("secret", url)
        self.assertEqual(self.plex._http.headers["X-Plex-Token"], "secret")

    def test_session_has_the_item_and_position(self):
        SESSIONS["MediaContainer"]["Metadata"][1].update(ratingKey="42", viewOffset=2_492_000)
        try:
            self.assertEqual(self.plex.session("192.168.0.110"), ("playing", "42", 2_492_000))
        finally:
            for k in ("ratingKey", "viewOffset"):
                SESSIONS["MediaContainer"]["Metadata"][1].pop(k)

    def test_restores_a_cleared_position(self):
        self.plex._http.get.side_effect = [
            response(body={"MediaContainer": {"Metadata": [{"viewCount": 1}]}}),
            response(),
            response(),
        ]
        self.assertTrue(self.plex.restore_position("42", 2_492_000))
        unscrobble, progress = self.plex._http.get.call_args_list[1:]
        self.assertTrue(unscrobble.args[0].endswith("/:/unscrobble"))
        self.assertEqual(progress.kwargs["params"]["time"], 2_492_000)
        self.assertEqual(progress.kwargs["params"]["key"], "42")

    def test_leaves_a_kept_position_alone(self):
        kept = {"MediaContainer": {"Metadata": [{"viewOffset": 5}]}}
        self.plex._http.get.return_value = response(body=kept)
        self.assertFalse(self.plex.restore_position("42", 2_492_000))
        self.plex._http.get.assert_called_once()


class LinkDeviceTest(unittest.TestCase):
    def link(self, polls):
        token_file = Path(tempfile.mkdtemp()) / "sub" / ".plex-token"
        with (
            mock.patch("requests.post", return_value=response(body={"id": 7, "code": "ABCD"})),
            mock.patch("requests.get", side_effect=polls) as get,
            mock.patch("time.sleep"),
            contextlib.redirect_stdout(io.StringIO()) as out,
        ):
            plex.link_device(token_file)
        return token_file, out.getvalue(), get

    def test_waits_for_approval_then_saves_token(self):
        token_file, out, get = self.link([response(), response(body={"authToken": "tok"})])
        self.assertIn("ABCD", out)
        self.assertEqual(get.call_count, 2)
        self.assertEqual(token_file.read_text(), "tok\n")

    def test_expired_pin(self):
        with self.assertRaises(SystemExit) as exit:
            self.link([response(status=404)])
        self.assertIn("expired", str(exit.exception))


if __name__ == "__main__":
    unittest.main()
