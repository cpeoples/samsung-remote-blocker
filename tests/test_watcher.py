import unittest

import requests
from samsungtvws.exceptions import UnauthorizedError

from samsung_remote_blocker.config import load
from samsung_remote_blocker.media import Session
from samsung_remote_blocker.watcher import Watcher

NETFLIX, HULU, PLEX, JELLYFIN = "3201907018807", "3201601007625", "3201512006963", "3202007021208"


class FakeTV:
    ip = "192.168.0.110"

    def __init__(self, visible=(), installed=(NETFLIX, HULU, PLEX, JELLYFIN), paired=True):
        self.visible = set(visible)
        self.installed = set(installed)
        self.calls = []
        self.offline = False
        self.paired = paired
        self.pair_error = None

    def pair(self):
        self.calls.append(("pair",))
        if self.pair_error:
            raise self.pair_error
        self.paired = True

    def app(self, app_id):
        if self.offline:
            raise requests.ConnectionError()
        if app_id not in self.installed:
            return None
        return {"id": app_id, "visible": app_id in self.visible}

    def launch(self, app_id):
        self.calls.append(("launch", app_id))
        self.visible = {app_id}

    def close(self, app_id):
        self.calls.append(("close", app_id))

    def send_key(self, key):
        self.calls.append(("key", key))


class FakePlex:
    name = "Plex"
    auth_hint = "rejected the token; run plex-login again"

    def __init__(self, state="playing", status=None, offset_ms=2_400_000):
        self.state = state
        self.status = status
        self.queue = []  # states reported before `state`
        self.offset_ms = offset_ms
        self.cleared = True  # whether Plex wiped the resume point
        self.restored = []

    def player_state(self, ip):
        if self.status:
            response = requests.Response()
            response.status_code = self.status
            raise requests.HTTPError(response=response)
        return self.queue.pop(0) if self.queue else self.state

    def session(self, ip):
        state = self.player_state(ip)
        return Session(state, "42", self.offset_ms) if state else None

    def restore_position(self, rating_key, offset_ms):
        if not self.cleared:
            return False
        self.restored.append((rating_key, offset_ms))
        return True


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def watcher(tv, plex=None, **env):
    clock = Clock()
    env = {"BLOCKED_APPS": "netflix,hulu", "ALLOWED_APPS": "plex", **env}
    w = Watcher(load(env), tv, {PLEX: plex} if plex else None, clock=clock, sleep=lambda s: None)
    w.plex = plex
    return w, clock


class WatcherTest(unittest.TestCase):
    def test_returns_to_plex_and_resumes_when_it_was_playing(self):
        tv = FakeTV(visible={PLEX})
        w, _ = watcher(tv, FakePlex("playing"))
        w.step()
        tv.visible = {NETFLIX}
        w.step()
        self.assertEqual(tv.calls, [("launch", PLEX), ("key", "KEY_PLAY"), ("close", NETFLIX)])

    def test_presses_play_again_until_plex_plays(self):
        tv = FakeTV(visible={PLEX})
        w, _ = watcher(tv, FakePlex("playing"))
        w.step()
        w.plex.queue = ["paused", "paused"]
        tv.visible = {NETFLIX}
        w.step()
        self.assertEqual(tv.calls.count(("key", "KEY_PLAY")), 3)

    def test_one_stale_playing_report_is_not_trusted(self):
        tv = FakeTV(visible={PLEX})
        w, _ = watcher(tv, FakePlex("playing"))
        w.step()
        w.plex.queue = ["playing", "paused"]
        tv.visible = {NETFLIX}
        w.step()
        self.assertEqual(tv.calls.count(("key", "KEY_PLAY")), 2)

    def test_resume_can_be_turned_off(self):
        tv = FakeTV(visible={PLEX})
        w, _ = watcher(tv, FakePlex("playing"), RESUME="false")
        w.step()
        tv.visible = {NETFLIX}
        w.step()
        self.assertEqual(tv.calls, [("launch", PLEX), ("close", NETFLIX)])

    def test_gives_up_resuming_after_a_few_presses(self):
        tv = FakeTV(visible={PLEX})
        w, _ = watcher(tv, FakePlex("playing"))
        w.step()
        w.plex.state = "paused"
        tv.visible = {NETFLIX}
        with self.assertLogs("samsung_remote_blocker.watcher", "WARNING") as logs:
            w.step()
        self.assertEqual(tv.calls.count(("key", "KEY_PLAY")), 6)
        self.assertIn("RESUME=false", logs.output[0])
        self.assertEqual(tv.calls[-1], ("close", NETFLIX))

    def test_does_not_resume_when_paused(self):
        tv = FakeTV(visible={PLEX})
        w, _ = watcher(tv, FakePlex("paused"))
        w.step()
        tv.visible = {NETFLIX}
        w.step()
        self.assertEqual(tv.calls, [("launch", PLEX), ("close", NETFLIX)])

    def test_does_not_resume_after_resume_window(self):
        tv = FakeTV(visible={PLEX})
        w, clock = watcher(tv, FakePlex("playing"))
        w.step()
        w.plex.state = "paused"
        clock.now += 10
        tv.visible = {NETFLIX}
        w.step()
        self.assertNotIn(("key", "KEY_PLAY"), tv.calls)

    def test_restores_a_position_plex_cleared_before_pressing_play(self):
        tv = FakeTV(visible={PLEX})
        w, _ = watcher(tv, FakePlex("playing", offset_ms=2_492_000))
        w.step()
        tv.visible = {NETFLIX}
        with self.assertLogs("samsung_remote_blocker.watcher", "INFO") as logs:
            w.step()
        self.assertEqual(w.plex.restored, [("42", 2_492_000)])
        self.assertIn(("key", "KEY_PLAY"), tv.calls)
        self.assertTrue(any("41:32" in line for line in logs.output))

    def test_leaves_the_position_alone_when_plex_kept_it(self):
        tv = FakeTV(visible={PLEX})
        w, _ = watcher(tv, FakePlex("playing"))
        w.plex.cleared = False
        w.step()
        tv.visible = {NETFLIX}
        w.step()
        self.assertEqual(w.plex.restored, [])
        self.assertIn(("key", "KEY_PLAY"), tv.calls)

    def test_failed_restore_still_presses_play(self):
        tv = FakeTV(visible={PLEX})
        w, _ = watcher(tv, FakePlex("playing"))
        w.step()

        def fail(rating_key, offset_ms):
            raise requests.ConnectionError()

        w.plex.restore_position = fail
        tv.visible = {NETFLIX}
        with self.assertLogs("samsung_remote_blocker.watcher", "WARNING"):
            w.step()
        self.assertIn(("key", "KEY_PLAY"), tv.calls)

    def test_resumes_after_a_slow_app_launch(self):
        tv = FakeTV(visible={PLEX})
        w, clock = watcher(tv, FakePlex("playing"))
        w.step()
        w.plex.state = "paused"
        clock.now += 1
        tv.visible = set()  # Plex has backgrounded; the blocked app is still loading
        w.step()
        clock.now += 6
        tv.visible = {NETFLIX}
        w.step()
        self.assertIn(("key", "KEY_PLAY"), tv.calls)

    def test_does_not_resume_long_after_leaving_plex(self):
        tv = FakeTV(visible={PLEX})
        w, clock = watcher(tv, FakePlex("playing"))
        w.step()
        tv.visible = set()
        w.step()
        clock.now += 60
        tv.visible = {NETFLIX}
        w.step()
        self.assertNotIn(("key", "KEY_PLAY"), tv.calls)

    def test_returns_to_jellyfin_and_resumes_with_its_own_server(self):
        tv = FakeTV(visible={JELLYFIN})
        plex, jellyfin = FakePlex("paused"), FakePlex("playing")
        jellyfin.name = "Jellyfin"
        env = {"BLOCKED_APPS": "netflix", "ALLOWED_APPS": "plex,jellyfin"}
        w = Watcher(load(env), tv, {PLEX: plex, JELLYFIN: jellyfin}, clock=Clock(), sleep=lambda s: None)
        w.step()
        tv.visible = {NETFLIX}
        with self.assertLogs("samsung_remote_blocker.watcher", "INFO") as logs:
            w.step()
        self.assertEqual(tv.calls[0], ("launch", JELLYFIN))
        self.assertIn(("key", "KEY_PLAY"), tv.calls)
        self.assertIn("back to Jellyfin (resuming)", logs.output[0])

    def test_switching_apps_forgets_the_old_position(self):
        tv = FakeTV(visible={PLEX})
        plex, jellyfin = FakePlex("playing"), FakePlex("paused")
        w = Watcher(
            load({"ALLOWED_APPS": "plex,jellyfin"}), tv, {PLEX: plex, JELLYFIN: jellyfin}, clock=Clock()
        )
        w.step()
        self.assertIsNotNone(w._position)
        tv.visible = {JELLYFIN}
        w.step()
        self.assertIsNone(w._position)

    def test_allowed_app_without_a_server_comes_back_without_resuming(self):
        tv = FakeTV(visible={JELLYFIN})
        w, _ = watcher(tv, FakePlex("playing"), ALLOWED_APPS="plex,jellyfin")
        w.step()
        tv.visible = {NETFLIX}
        w.step()
        self.assertEqual(tv.calls, [("launch", JELLYFIN), ("close", NETFLIX)])

    def test_goes_home_without_an_allowed_app(self):
        tv = FakeTV(visible={NETFLIX})
        w, _ = watcher(tv)
        w.step()
        self.assertEqual(tv.calls, [("key", "KEY_HOME"), ("close", NETFLIX)])

    def test_failed_recovery_falls_back_to_home(self):
        tv = FakeTV(visible={PLEX})
        w, _ = watcher(tv)
        w.step()

        def fail(app_id):
            raise requests.HTTPError("500")

        tv.launch = fail
        tv.visible = {NETFLIX}
        with self.assertLogs("samsung_remote_blocker.watcher", "WARNING"):
            w.step()
        self.assertEqual(tv.calls, [("key", "KEY_HOME")])

    def test_failed_close_does_not_send_home_from_plex(self):
        tv = FakeTV(visible={PLEX})
        w, _ = watcher(tv)
        w.step()

        def fail(app_id):
            raise requests.ReadTimeout()

        tv.close = fail
        tv.visible = {NETFLIX}
        with self.assertLogs("samsung_remote_blocker.watcher", "WARNING"):
            w.step()
        self.assertEqual(tv.calls, [("launch", PLEX)])

    def test_forgets_uninstalled_ids_only_when_tv_answers(self):
        tv = FakeTV(installed=set())
        w, _ = watcher(tv)
        w.step()  # nothing answered: maybe booting, keep everything
        self.assertEqual(set(w.blocked), {NETFLIX, HULU})

        tv.installed = {NETFLIX, PLEX}
        w.step()
        self.assertEqual(set(w.blocked), {NETFLIX})

    def test_tv_offline_is_raised_to_run_loop(self):
        tv = FakeTV()
        tv.offline = True
        w, _ = watcher(tv)
        with self.assertRaises(requests.RequestException):
            w.step()

    def test_rejected_plex_token_is_reported_as_such(self):
        tv = FakeTV(visible={PLEX})
        w, _ = watcher(tv, FakePlex(status=401))
        with self.assertLogs("samsung_remote_blocker.watcher", "WARNING") as logs:
            w.step()
        self.assertIn("plex-login", logs.output[0])

    def test_run_reports_offline_once_after_repeated_failures(self):
        tv = FakeTV()
        tv.offline = True
        polls = iter(range(5))

        def sleep(seconds):
            if next(polls, None) is None:
                raise KeyboardInterrupt

        w = Watcher(load({}), tv, sleep=sleep)
        with self.assertLogs("samsung_remote_blocker.watcher") as logs, self.assertRaises(KeyboardInterrupt):
            w.run()
        self.assertEqual(sum("unreachable" in line for line in logs.output), 1)

    def test_pairs_once_when_unpaired(self):
        tv = FakeTV(paired=False)
        w, _ = watcher(tv)
        with self.assertLogs("samsung_remote_blocker.watcher") as logs:
            w.ensure_paired()
            w.ensure_paired()
        self.assertEqual(tv.calls, [("pair",)])
        self.assertIn("Paired", logs.output[-1])

    def test_already_paired_does_nothing(self):
        tv = FakeTV()
        w, _ = watcher(tv)
        w.ensure_paired()
        self.assertEqual(tv.calls, [])

    def test_unanswered_prompt_is_retried_after_tv_comes_back(self):
        tv = FakeTV(paired=False)
        tv.pair_error = TimeoutError("timed out")
        polls = iter([True, False, False, False, True, True])  # online, offline x3, back

        def sleep(seconds):
            state = next(polls, None)
            if state is None:
                raise KeyboardInterrupt
            tv.offline = not state

        w = Watcher(load({}), tv, sleep=sleep)
        with self.assertLogs("samsung_remote_blocker.watcher"), self.assertRaises(KeyboardInterrupt):
            w.run()
        self.assertEqual(tv.calls.count(("pair",)), 2)

    def test_denied_pairing_is_logged_not_raised(self):
        tv = FakeTV(paired=False)
        tv.pair_error = UnauthorizedError("denied")
        w, _ = watcher(tv)
        with self.assertLogs("samsung_remote_blocker.watcher", "ERROR") as logs:
            w.ensure_paired()
        self.assertIn("External Device Manager", logs.output[0])

    def test_unsaved_token_is_not_called_a_missed_prompt(self):
        tv = FakeTV(paired=False)
        tv.pair_error = PermissionError("Permission denied: '/data/tv-token.txt'")
        w, _ = watcher(tv)
        with self.assertLogs("samsung_remote_blocker.watcher", "ERROR") as logs:
            w.ensure_paired()
        self.assertIn("can't be saved", logs.output[0])


if __name__ == "__main__":
    unittest.main()
