"""Launchd wrapper for reachy-mini-daemon on macOS.

Under a launchd agent session, GStreamer's avfdeviceprovider enumerates ZERO
video devices even though the same enumeration works from an interactive
shell and the venv python holds the TCC camera grant. Capture itself can
still work — only discovery is blind. So: try real detection first, and only
when it comes back empty fall back to AVFoundation device-index 0 with the
Reachy Mini Lite camera specs (matches what interactive detection returns).

Must patch BEFORE reachy_mini.media.media_server is imported, because it
binds get_video_device at import time.
"""
import sys

from reachy_mini.media import device_detection
from reachy_mini.media.camera_constants import ReachyMiniLiteCamSpecs

_real_get_video_device = device_detection.get_video_device


def get_video_device():
    path, specs = _real_get_video_device()
    if path:
        return path, specs
    device_detection._logger.warning(
        "video enumeration empty (launchd session); falling back to avf index 0 + lite specs"
    )
    return "0", ReachyMiniLiteCamSpecs()


device_detection.get_video_device = get_video_device

from reachy_mini.daemon.app.main import main  # noqa: E402  (after patch, by design)

sys.exit(main())
