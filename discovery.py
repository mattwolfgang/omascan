"""Find fi-8170 scanners on the local network.

The scanner doesn't announce itself via mDNS/SSDP/WS-Discovery, so this just
probes every address in each local IPv4 /24 for the Privet info endpoint
(GET /api/privet/info on port 80) and keeps the ones that answer as a
Fujitsu/Ricoh fi-series device.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import requests


@dataclass
class FoundScanner:
    host: str
    manufacturer: str
    model: str
    serial: str

    @property
    def is_fi8170(self) -> bool:
        return "8170" in self.model


def local_networks() -> list[ipaddress.IPv4Network]:
    """The /24 around each of this machine's non-loopback IPv4 addresses. Larger
    subnets are narrowed to /24 so a scan stays at ~254 probes per interface."""
    addrs: list[str] = []
    try:
        out = subprocess.run(["ip", "-j", "-4", "addr"], capture_output=True, text=True, timeout=5).stdout
        for iface in json.loads(out):
            for info in iface.get("addr_info", []):
                if info.get("family") == "inet" and info.get("scope") == "global":
                    addrs.append(info["local"])
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    if not addrs:
        # Fallback: the address of the interface holding the default route.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            try:
                s.connect(("10.255.255.255", 1))
                addrs.append(s.getsockname()[0])
            except OSError:
                pass
    networks = {ipaddress.ip_network(f"{a}/24", strict=False) for a in addrs}
    return sorted(networks)


def probe(host: str, port: int = 80, timeout: float = 0.6) -> FoundScanner | None:
    # A raw TCP connect first is much cheaper than an HTTP request to a dead address.
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except OSError:
        return None
    try:
        resp = requests.get(f"http://{host}:{port}/api/privet/info", timeout=2.0)
        info = resp.json()
    except (requests.RequestException, ValueError):
        return None
    if not isinstance(info, dict):
        return None
    model = str(info.get("model", ""))
    manufacturer = str(info.get("manufacturer", ""))
    if not model.lower().startswith("fi-") and "fujitsu" not in manufacturer.lower() and "ricoh" not in manufacturer.lower():
        return None
    return FoundScanner(host=host, manufacturer=manufacturer, model=model, serial=str(info.get("serialNumber", "")))


def find_scanners(networks: list[ipaddress.IPv4Network] | None = None, port: int = 80) -> list[FoundScanner]:
    networks = networks if networks is not None else local_networks()
    hosts = [str(h) for net in networks for h in net.hosts()]
    with ThreadPoolExecutor(max_workers=128) as pool:
        results = pool.map(lambda h: probe(h, port), hosts)
    found = [r for r in results if r is not None]
    # fi-8170s first, then any other fi-series devices that answered.
    return sorted(found, key=lambda s: (not s.is_fi8170, ipaddress.ip_address(s.host)))
