#!/usr/bin/env python3
"""Keep the proxied app matched to the Kasm session, in both directions.

kasmxproxy maps the fixed-size source X display into the Kasm display:
- Session SMALLER than source: it crops — so resize the app window on
  the source display to the session size at 0,0; the crop is then
  exactly the app, full-bleed, and the page reflows (phone layout).
- Session LARGER than source: it under-fills (black bar) — the source
  Xvfb cannot grow past its launch size, so clamp the session back to
  the source size and let the client scale the image up instead.

Polls every 2s. One instance per app (window matched by name hint).

Configuration (environment):
  WF_SRC        source X display          (default ":103")
  WF_DST        Kasm X display            (default ":110")
  WF_NAME_HINT  substring of the app window's title (default "Greenhouse")
"""
import os
import re
import subprocess
import time

SRC = os.environ.get("WF_SRC", ":103")
DST = os.environ.get("WF_DST", ":110")
NAME_HINT = os.environ.get("WF_NAME_HINT", "Greenhouse")
ENV_SRC = {"DISPLAY": SRC, "PATH": "/usr/bin:/bin"}
ENV_DST = {"DISPLAY": DST, "PATH": "/usr/bin:/bin"}
last = None


def dims(display):
    out = subprocess.run(
        ["xdpyinfo", "-display", display],
        capture_output=True, text=True, timeout=10,
    ).stdout
    m = re.search(r"dimensions:\s+(\d+)x(\d+)", out)
    return (int(m.group(1)), int(m.group(2))) if m else None


def set_fb(display, env, w, h):
    subprocess.run(
        ["xrandr", "--fb", f"{w}x{h}"],
        env=env, capture_output=True, timeout=10,
    )


def app_window():
    out = subprocess.run(
        ["xdotool", "search", "--name", NAME_HINT],
        env=ENV_SRC, capture_output=True, text=True, timeout=10,
    ).stdout.split()
    return out[-1] if out else None


def follow(w, h):
    win = app_window()
    if not win:
        return
    subprocess.run(["xdotool", "windowmove", win, "0", "0"], env=ENV_SRC, timeout=10)
    subprocess.run(["xdotool", "windowsize", win, str(w), str(h)], env=ENV_SRC, timeout=10)
    subprocess.run(["xdotool", "windowraise", win], env=ENV_SRC, timeout=10)


def main():
    global last
    while True:
        try:
            sess = dims(DST)
            src = dims(SRC)
            if sess and src:
                eff = (min(sess[0], src[0]), min(sess[1], src[1]))
                if sess != eff:
                    set_fb(DST, ENV_DST, *eff)
                if eff != last:
                    follow(*eff)
                    last = eff
        except Exception:
            pass
        time.sleep(2)


if __name__ == "__main__":
    main()
