#!/bin/bash
# Cap Reachy's launchd logs so the osxaudiosrc / libavdevice spam can't bloat
# the disk again (they hit 240-350 MB). Keep the last ~2000 lines if a file
# exceeds 30 MB. Run daily by com.legionstage.reachy-logtrim.
for f in \
  /Users/admin/reachy-twin/panel-mac.err \
  /Users/admin/reachy-twin/panel-mac.log \
  /Users/admin/reachy-twin/daemon-mac.err \
  /Users/admin/reachy-twin/daemon-mac.log \
  /Users/admin/reachy-twin/mlx-server.err \
  /Users/admin/reachy-twin/mlx-server.log \
  /Users/admin/reachy-vision/vision.err \
  /Users/admin/reachy-vision/vision.log ; do
  [ -f "$f" ] || continue
  sz=$(stat -f%z "$f" 2>/dev/null || echo 0)
  if [ "$sz" -gt 31457280 ]; then           # 30 MB
    tmp="$f.trim"
    tail -n 2000 "$f" > "$tmp" 2>/dev/null && cat "$tmp" > "$f" && rm -f "$tmp"
  fi
done
