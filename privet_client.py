"""Client for the Privet-derived scanning protocol used by the Ricoh/Fujitsu fi-8170.

Reverse-engineered from a packet capture of the vendor Windows software talking to
the scanner over plain HTTP on port 80. There is no standard eSCL/WSD compatibility;
the device only exposes two endpoints:

    GET  /api/privet/info
    POST /api/privet/session   (JSON-RPC-style commands, keyed by "method")

A scan job is: createSession -> sendTask -> startCapturing ->
[readImageBlock -> GET image -> releaseImageBlocks]* -> stopCapturing -> closeSession.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any

import requests


class PrivetError(RuntimeError):
    """Raised when the scanner returns a non-success status for a command."""


@dataclass
class ScannedImage:
    sheet_number: int
    source: str  # "front" or "back"
    uri: str
    size: int
    pixel_width: int
    pixel_height: int
    resolution: int
    end_of_scan: bool
    data: bytes = field(repr=False)


def _default_scan_task(resolution: int, width: int, height: int, jpeg_quality: int) -> dict:
    """The exact sendTask body captured from the vendor software, with the few
    fields a caller would plausibly want to vary pulled out as parameters."""
    return {
        "actions": {
            "streams": {
                "sources": {
                    "docAnnotations": {
                        "verticalLine": {
                            "attributes": [{"attribute": "verticalLine", "values": {"value": "disable"}}]
                        }
                    },
                    "feedControls": {
                        "background": {"attributes": [{"attribute": "bgColor", "values": {"value": "forceWhite"}}]},
                        "doubleFeed": {
                            "attributes": [
                                {"attribute": "overlap", "values": {"value": "disable"}},
                                {"attribute": "length", "values": {"value": "disable"}},
                                {"attribute": "response", "values": {"value": "notify"}},
                                {"attribute": "deviceSpecification", "values": {"value": "disable"}},
                                {"attribute": "iOMFLength", "values": {"value": "0"}},
                            ]
                        },
                        "ejection": {
                            "attributes": [
                                {"attribute": "synchronousEjection", "values": {"value": "disable"}},
                                {"attribute": "synchronousNextFeed", "values": {"value": "disable"}},
                            ]
                        },
                        "noSeparation": {
                            "attributes": [{"attribute": "noSeparationControl", "values": {"value": "deviceSpecification"}}]
                        },
                        "numberOfSheets": {"attributes": [{"attribute": "sheetCounts", "values": {"value": "0"}}]},
                        "paperProtection": {
                            "attributes": [
                                {"attribute": "paperProtection", "values": {"value": "deviceSpecification"}},
                                {"attribute": "soundJam", "values": {"value": "deviceSpecification"}},
                                {"attribute": "paperProtection3", "values": {"value": "deviceSpecification"}},
                            ]
                        },
                        "prePick": {"attributes": [{"attribute": "prePickControl", "values": {"value": "enable"}}]},
                    },
                    "pixelFormats": {
                        "attributes": [
                            {"attribute": "resolution", "values": {"value": str(resolution)}},
                            {"attribute": "height", "values": {"value": str(height)}},
                            {"attribute": "width", "values": {"value": str(width)}},
                            {"attribute": "automaticSize", "values": {"value": "disable"}},
                            {"attribute": "offsetWidth", "values": {"value": "0"}},
                            {"attribute": "offsetHeight", "values": {"value": "0"}},
                            {"attribute": "compression", "values": {"value": "jpeg"}},
                            {"attribute": "dropoutColor", "values": {"value": "green"}},
                            {"attribute": "jpgSubSampling", "values": {"value": "422"}},
                            {"attribute": "endOfPageDetection", "values": {"value": "on"}},
                            {"attribute": "overscan", "values": {"value": "on"}},
                            {"attribute": "automaticDeskew", "values": {"value": "disable"}},
                            {"attribute": "cropMargin", "values": {"value": "0.0"}},
                            {"attribute": "tabCropping", "values": {"value": "on"}},
                            {"attribute": "jpegQuality", "values": {"value": str(jpeg_quality)}},
                            {"attribute": "highSpeedMode", "values": {"value": "off"}},
                            {"attribute": "paperWidth", "values": {"value": str(width)}},
                            {"attribute": "paperLength", "values": {"value": str(height)}},
                            {"attribute": "sRGBPattern", "values": {"value": "deviceSpecification"}},
                            {"attribute": "moireRemoval", "values": {"value": "deviceSpecification"}},
                        ],
                        "pixelFormat": "rgb24",
                    },
                    "readControls": {
                        "imageCacheMode": {"attributes": [{"attribute": "imageCacheMode", "values": {"value": "scannerMemory"}}]},
                        "imageTransferMethod": {"attributes": [{"attribute": "imageTransferMethod", "values": {"value": "alternate"}}]},
                    },
                    "source": "feeder",
                }
            }
        }
    }


class PrivetClient:
    def __init__(self, host: str, port: int = 80, timeout: float = 30.0):
        self.base_url = f"http://{host}:{port}"
        self.timeout = timeout
        self.session_id: str | None = None
        self._command_ids = itertools.count(1)
        # A single keep-alive TCP connection, matching the captured traffic.
        self._http = requests.Session()

    def _next_command_id(self) -> str:
        return f"Client{next(self._command_ids):03d}"

    def _post(self, method: str, parameters: dict, command_timeout: str | None = None) -> dict:
        """Returns the full `results` object (may contain sibling keys alongside
        "session", e.g. readImageBlock's "metadata" array)."""
        body: dict[str, Any] = {"commandId": self._next_command_id(), "method": method, "parameters": parameters}
        if command_timeout is not None:
            body["commandTimeOut"] = command_timeout
        resp = self._http.post(f"{self.base_url}/api/privet/session", json=body, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "success":
            raise PrivetError(f"{method} failed: {data}")
        return data["results"]

    def get_info(self) -> dict:
        resp = self._http.get(f"{self.base_url}/api/privet/info", timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def create_session(self, user_id: str = "PFULimited") -> str:
        send_to = [{"id": "0", "locale": "0x0409", "name": "One Push Scan"}]
        send_to += [{"id": str(i), "locale": "0x0409", "name": ""} for i in range(1, 51)]
        result = self._post(
            "createSession",
            {"connect": "PSIP", "sendTo": send_to, "sessionTimeOut": "1", "userId": user_id},
            command_timeout="5",
        )
        self.session_id = result["session"]["sessionId"]
        return self.session_id

    def get_session(self, operation_code: int = 1) -> dict:
        return self._post("getSession", {"operationCode": operation_code, "sessionId": self.session_id})

    def send_task(self, resolution: int = 300, width: int = 5760, height: int = 4576, jpeg_quality: int = 80) -> dict:
        task = _default_scan_task(resolution, width, height, jpeg_quality)
        return self._post("sendTask", {"sessionId": self.session_id, "task": task}, command_timeout="5")

    def start_capturing(self) -> dict:
        return self._post(
            "startCapturing",
            {"ignore_mf_detection": "false", "restartCapturing": "false", "sessionId": self.session_id},
            command_timeout="70",
        )

    def read_image_block(self, image_block_num: int) -> dict:
        return self._post(
            "readImageBlock",
            {
                "duplexMetadata": "enable",
                "imageBlockNum": image_block_num,
                "sessionId": self.session_id,
                "withMetadata": "enable",
            },
        )

    def release_image_blocks(self, image_block_num: int, last_image_block_num: int) -> dict:
        return self._post(
            "releaseImageBlocks",
            {"imageBlockNum": image_block_num, "lastImageBlockNum": last_image_block_num, "sessionId": self.session_id},
            command_timeout="60",
        )

    def stop_capturing(self, pause_scanning: bool = False) -> dict:
        return self._post(
            "stopCapturing",
            {"pauseScanning": "true" if pause_scanning else "false", "sessionId": self.session_id},
            command_timeout="5",
        )

    def close_session(self) -> dict:
        result = self._post("closeSession", {"sessionId": self.session_id}, command_timeout="5")
        self.session_id = None
        return result

    def fetch_image(self, uri: str) -> bytes:
        resp = self._http.get(f"{self.base_url}{uri}", timeout=self.timeout)
        resp.raise_for_status()
        return resp.content

    def scan(self, resolution: int = 300, width: int = 5760, height: int = 4576, jpeg_quality: int = 80):
        """High-level generator: runs a full feeder scan job and yields ScannedImage
        objects (front then back for each sheet) as they come off the scanner."""
        self.create_session()
        try:
            self.send_task(resolution=resolution, width=width, height=height, jpeg_quality=jpeg_quality)
            self.start_capturing()

            block_num = 1
            while True:
                try:
                    result = self.read_image_block(block_num)
                except (requests.exceptions.RequestException, PrivetError):
                    # Feeder ran out of sheets: the device stops answering readImageBlock.
                    break

                metadata = result.get("metadata", [])
                if not metadata:
                    break

                # The device's own address.sheetNumber field is always 0, so it can't
                # be used to distinguish sheets; derive a sheet index from the block
                # sequence instead (one block == one physical sheet, front+back).
                sheet_index = (block_num - 1) // 2 + 1

                reached_end = False
                for entry in metadata:
                    address = entry["address"]
                    image = entry["image"]
                    data = self.fetch_image(address["uri"])
                    yield ScannedImage(
                        sheet_number=sheet_index,
                        source=address["source"],
                        uri=address["uri"],
                        size=image["size"],
                        pixel_width=image["pixelWidth"],
                        pixel_height=image["pixelHeight"],
                        resolution=image["resolution"],
                        end_of_scan=entry.get("status", {}).get("endOfScan") == "true",
                        data=data,
                    )
                    if entry.get("status", {}).get("endOfScan") == "true":
                        reached_end = True

                self.release_image_blocks(block_num, block_num + 1)
                if reached_end:
                    break
                block_num += 2

            self.stop_capturing()
        finally:
            self.close_session()
