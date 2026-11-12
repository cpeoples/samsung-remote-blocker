import unittest

import requests
from samsungtvws.exceptions import UnauthorizedError

from samsung_remote_blocker.config import load
from samsung_remote_blocker.watcher import Watcher

NETFLIX, HULU, PLEX = "3201907018807", "3201601007625", "3201512006963"


class FakeTV:
    ip = "192.168.0.110"

    def __init__(self, visible=(), installed=(NETFLIX, HULU, PLEX), paired=True):
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
    def __init__(self, state="playing", status=None):
        self.state = state
        self.status = status

    def player_state(self, ip):
        if self.status:
            response = requests.Response()
            response.status_code = self.status
            raise requests.HTTPError(response=response)
        return self.state


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def watcher(tv, plex=None, **env):
    clock = Clock()
    env = {"BLOCKED_APPS": "netflix,hulu", "ALLOWED_APPS": "plex", **env}
    w = Watcher(load(env), tv, plex, clock=clock, sleep=lambda s: None)
    return w, clock


class WatcherTest(unittest.TestCase):
    def test_returns_to_plex_and_resumes_when_it_was_playing(self):
        tv = FakeTV(visible={PLEX})
        w, _ = watcher(tv, FakePlex("playing"))
        w.step()
        tv.visible = {NETFLIX}
        w.step()
        self.assertEqual(tv.calls, [("launch", PLEX), ("key", "KEY_PLAY"), ("close", NETFLIX)])

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


if __name__ == "__main__":
    unittest.main()
