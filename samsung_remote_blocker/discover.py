"""Find Samsung TVs by SSDP, or by probing every host in a subnet."""

from __future__ import annotations

import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests

SSDP_ADDR = ("239.255.255.250", 1900)
SEARCH_TARGETS = (
    "urn:samsung.com:device:RemoteControlReceiver:1",
    "urn:dial-multiscreen-org:service:dial:1",
)
MAX_SCAN_HOSTS = 4096


def device_info(ip: str, timeout: float = 1.5) -> dict | None:
    """Identity from the TV's unauthenticated /api/v2/ endpoint, or None if it isn't a Tizen TV."""
    for url in (f"http://{ip}:8001/api/v2/", f"https://{ip}:8002/api/v2/"):
        try:
            r = requests.get(url, timeout=timeout, verify=False)
            device = r.json().get("device") if r.ok else None
        except (requests.RequestException, ValueError):
            continue
        if device:
            return {
                "ip": ip,
                "name": device.get("name"),
                "model": device.get("modelName"),
                "mac": device.get("wifiMac"),
                "power": device.get("PowerState"),
            }
    return None


def location_host(response: str) -> str | None:
    for line in response.splitlines():
        header, _, value = line.partition(":")
        if header.strip().lower() == "location":
            return urlparse(value.strip()).hostname
    return None


def ssdp_hosts(timeout: float = 3.0) -> set[str]:
    hosts = set()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(timeout)
        for target in SEARCH_TARGETS:
            sock.sendto(
                (
                    f"M-SEARCH * HTTP/1.1\r\nHOST: {SSDP_ADDR[0]}:{SSDP_ADDR[1]}\r\n"
                    f'MAN: "ssdp:discover"\r\nMX: 2\r\nST: {target}\r\n\r\n'
                ).encode(),
                SSDP_ADDR,
            )
        try:
            while True:
                data, (sender, _) = sock.recvfrom(65507)
                hosts.add(location_host(data.decode(errors="ignore")) or sender)
        except TimeoutError:
            pass
    return hosts


def subnet_hosts(cidr: str) -> set[str]:
    net = ipaddress.ip_network(cidr, strict=False)
    if net.num_addresses > MAX_SCAN_HOSTS:
        raise ValueError(f"{cidr} is too large to scan; use a /20 or smaller")
    return {str(host) for host in net.hosts()}


def discover(hosts: set[str] | None = None) -> list[dict]:
    """Identify TVs among hosts, or among SSDP responders if hosts is None."""
    with ThreadPoolExecutor(max_workers=64) as pool:
        tvs = [info for info in pool.map(device_info, hosts or ssdp_hosts()) if info]
    return sorted(tvs, key=lambda tv: ipaddress.ip_address(tv["ip"]))
