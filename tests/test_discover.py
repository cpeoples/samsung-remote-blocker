import unittest
from unittest import mock

import requests

from samsung_remote_blocker.discover import device_info, discover, location_host, subnet_hosts

DEVICE = {"name": "Living Room", "modelName": "UN65DU8000FXZA", "wifiMac": "aa:bb", "PowerState": "on"}


def response(body, ok=True):
    r = mock.Mock(ok=ok)
    r.json.return_value = body
    return r


class DiscoverTest(unittest.TestCase):
    def test_location_host(self):
        reply = "HTTP/1.1 200 OK\r\nST: x\r\nLOCATION: http://192.168.0.110:9197/dmr\r\n\r\n"
        self.assertEqual(location_host(reply), "192.168.0.110")
        self.assertIsNone(location_host("HTTP/1.1 200 OK\r\n\r\n"))

    def test_subnet_limit(self):
        self.assertEqual(len(subnet_hosts("10.0.0.0/30")), 2)
        with self.assertRaises(ValueError):
            subnet_hosts("10.0.0.0/8")

    def test_device_info_falls_back_to_https(self):
        with mock.patch(
            "requests.get", side_effect=[requests.ConnectionError(), response({"device": DEVICE})]
        ):
            info = device_info("192.168.0.110")
        self.assertEqual(
            info,
            {
                "ip": "192.168.0.110",
                "name": "Living Room",
                "model": "UN65DU8000FXZA",
                "mac": "aa:bb",
                "power": "on",
            },
        )

    def test_non_tv_hosts_are_ignored(self):
        replies = [response({}, ok=False), mock.Mock(ok=True, json=mock.Mock(side_effect=ValueError))]
        with mock.patch("requests.get", side_effect=replies):
            self.assertIsNone(device_info("192.168.0.1"))

    def test_results_sorted_by_address(self):
        found = {"192.168.0.10": {"ip": "192.168.0.10"}, "192.168.0.9": {"ip": "192.168.0.9"}}
        with mock.patch("samsung_remote_blocker.discover.device_info", side_effect=found.get):
            tvs = discover({"192.168.0.10", "192.168.0.9", "192.168.0.1"})
        self.assertEqual([tv["ip"] for tv in tvs], ["192.168.0.9", "192.168.0.10"])


if __name__ == "__main__":
    unittest.main()
