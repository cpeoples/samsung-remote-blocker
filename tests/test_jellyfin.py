import unittest
from unittest import mock

from samsung_remote_blocker.jellyfin import Jellyfin


def response(body):
    r = mock.Mock(status_code=200)
    r.json.return_value = body
    return r


SESSIONS = [
    {"RemoteEndPoint": "192.168.0.75", "NowPlayingItem": {"Id": "web"}, "PlayState": {}},
    {"RemoteEndPoint": "192.168.0.110", "Client": "Jellyfin for Tizen"},
    {
        "RemoteEndPoint": "192.168.0.110:51234",
        "NowPlayingItem": {"Id": "abc"},
        "PlayState": {"IsPaused": True, "PositionTicks": 970_000_000},
    },
]


class JellyfinTest(unittest.TestCase):
    def setUp(self):
        self.jellyfin = Jellyfin("http://jf:8096", "secret")
        self.jellyfin._http.get = mock.Mock(return_value=response(SESSIONS))

    def test_session_for_this_tv_skips_idle_ones(self):
        self.assertEqual(self.jellyfin.session("192.168.0.110"), ("paused", "abc", 97_000))

    def test_playing_when_not_paused(self):
        self.jellyfin._http.get.return_value = response([{**SESSIONS[2], "PlayState": {"IsPaused": False}}])
        self.assertEqual(self.jellyfin.player_state("192.168.0.110"), "playing")

    def test_ip_prefix_is_not_a_match(self):
        self.assertIsNone(self.jellyfin.session("192.168.0.11"))

    def test_key_is_a_header_not_in_the_url(self):
        self.jellyfin.session("192.168.0.110")
        self.assertNotIn("secret", self.jellyfin._http.get.call_args.args[0])
        self.assertIn("secret", self.jellyfin._http.headers["Authorization"])

    def test_nothing_to_restore(self):
        self.assertFalse(self.jellyfin.restore_position("abc", 97_000))


if __name__ == "__main__":
    unittest.main()
