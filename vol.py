"""Set Reachy's speaker volume. Usage: python vol.py 40   (0-100). No arg -> show current."""
import json
import sys
import urllib.request

BASE = "http://localhost:8000/api/volume"


def get():
    return urllib.request.urlopen(BASE + "/current", timeout=6).read().decode()


def set_(v):
    req = urllib.request.Request(
        BASE + "/set",
        data=json.dumps({"volume": int(v)}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return urllib.request.urlopen(req, timeout=6).read().decode()


if __name__ == "__main__":
    print(set_(sys.argv[1]) if len(sys.argv) > 1 else get())
