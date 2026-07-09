#!/usr/bin/env python3
"""
mirror.py -- best-effort fan-out of panel frames to the web mirror (webapp.py).

register_service.py already builds a frame dict and sends it to the LED wall
(serial) and/or an ESP32_HOST panel. Importing this module and calling
mirror_push(frame) right after that send also POSTs the same frame to a web
mirror, so a browser can show exactly what the wall is showing.

Integration -- two lines in register_service.py:

    from mirror import mirror_push          # near the other imports
    ...
    mirror_push(frame)                       # right after the frame is sent

Enable by setting MIRROR_HOST (host:port of webapp.py, e.g. localhost:8080).
Unset = disabled, a no-op, so dev and standalone runs are unaffected. The POST
runs OFF the event loop and swallows every error -- a slow or dead mirror can
never stall or crash the wall.
"""

import os
import json
import asyncio
import threading
import urllib.request

MIRROR_HOST = os.environ.get("MIRROR_HOST", "").strip()   # e.g. "localhost:8080"; empty = off


def _post_blocking(data: bytes):
    try:
        req = urllib.request.Request(
            f"http://{MIRROR_HOST}/frame", data=data,
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=2).read()
    except Exception:
        pass   # best-effort only -- never let the mirror disturb the wall


def mirror_push(frame: dict):
    """Fire-and-forget the frame to the web mirror if MIRROR_HOST is set.

    Runs the blocking HTTP POST in a thread / executor so the serial + panel
    push path is never blocked by a slow or unreachable mirror.
    """
    if not MIRROR_HOST:
        return
    data = json.dumps(frame).encode()
    try:
        # Inside the async service: hand off to the default executor.
        asyncio.get_running_loop().run_in_executor(None, _post_blocking, data)
    except RuntimeError:
        # Called from a non-async context: spin a throwaway daemon thread.
        threading.Thread(target=_post_blocking, args=(data,), daemon=True).start()
