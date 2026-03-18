"""
HAR (HTTP Archive) capture via CDP Network domain.

When enabled via CAPTURE_HAR=true, records all network traffic during a
checker run and writes a HAR file to logs/. These can be opened in
Chrome DevTools (Network tab → Import) or https://toolbox.googleapps.com/apps/har_analyzer/

Only active when the env flag is set — zero overhead otherwise.
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import nodriver.cdp.network as cdp_network

from src.config import LOG_DIR

log = logging.getLogger(__name__)


class HarCapture:
    """Collects network events and writes a HAR 1.2 file on flush."""

    def __init__(self):
        self._entries: dict[str, dict] = {}   # request_id → partial entry
        self._start_time = datetime.now(timezone.utc)

    def attach(self, page):
        """Register CDP event handlers on a page/tab."""
        page.add_handler(cdp_network.RequestWillBeSent, self._on_request)
        page.add_handler(cdp_network.ResponseReceived, self._on_response)
        log.info("HAR capture enabled — recording network traffic")

    def _on_request(self, event: cdp_network.RequestWillBeSent, _connection=None):
        """Handle Network.requestWillBeSent event."""
        req = event.request
        entry = {
            "startedDateTime": datetime.now(timezone.utc).isoformat(),
            "request": {
                "method": str(req.method),
                "url": str(req.url),
                "httpVersion": "HTTP/1.1",
                "headers": self._headers_to_list(req.headers),
                "queryString": [],
                "cookies": [],
                "headersSize": -1,
                "bodySize": len(req.post_data) if req.post_data else 0,
                "postData": {
                    "mimeType": "application/x-www-form-urlencoded",
                    "text": str(req.post_data),
                } if req.post_data else None,
            },
            "response": None,
            "cache": {},
            "timings": {"send": 0, "wait": 0, "receive": 0},
            "time": 0,
        }
        # Remove None postData
        if entry["request"]["postData"] is None:
            del entry["request"]["postData"]

        self._entries[str(event.request_id)] = entry

    def _on_response(self, event: cdp_network.ResponseReceived, _connection=None):
        """Handle Network.responseReceived event."""
        req_id = str(event.request_id)
        entry = self._entries.get(req_id)
        if not entry:
            return

        resp = event.response
        entry["response"] = {
            "status": int(resp.status),
            "statusText": str(resp.status_text),
            "httpVersion": str(resp.protocol) if resp.protocol else "HTTP/1.1",
            "headers": self._headers_to_list(resp.headers),
            "cookies": [],
            "content": {
                "size": int(resp.encoded_data_length) if resp.encoded_data_length else -1,
                "mimeType": str(resp.mime_type) if resp.mime_type else "unknown",
            },
            "redirectURL": "",
            "headersSize": -1,
            "bodySize": int(resp.encoded_data_length) if resp.encoded_data_length else -1,
        }

        # Calculate timing if available
        if resp.timing:
            entry["timings"] = {
                "send": max(0, resp.timing.send_end - resp.timing.send_start) if resp.timing.send_end and resp.timing.send_start else 0,
                "wait": max(0, resp.timing.receive_headers_end - resp.timing.send_end) if resp.timing.receive_headers_end and resp.timing.send_end else 0,
                "receive": 0,
            }
            entry["time"] = sum(entry["timings"].values())

    @staticmethod
    def _headers_to_list(headers) -> list[dict]:
        """Convert CDP Headers (dict-like) to HAR header list."""
        if not headers:
            return []
        result = []
        # CDP headers come as a dict or dict-like object
        try:
            items = headers.items() if hasattr(headers, "items") else []
            for name, value in items:
                result.append({"name": str(name), "value": str(value)})
        except Exception:
            pass
        return result

    def flush(self) -> Path | None:
        """Write collected entries to a HAR file. Returns the file path."""
        # Filter out entries without responses
        complete = [e for e in self._entries.values() if e.get("response")]

        if not complete:
            log.info("HAR capture: no entries to write")
            return None

        har = {
            "log": {
                "version": "1.2",
                "creator": {"name": "DVSA Checker", "version": "1.0"},
                "entries": complete,
            }
        }

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        har_path = LOG_DIR / f"checker_{timestamp}.har"
        har_path.write_text(json.dumps(har, indent=2, default=str), encoding="utf-8")
        log.info(f"HAR file written: {har_path} ({len(complete)} entries)")
        return har_path
