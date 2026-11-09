# Surfacing a live app window in an AI agent's browser

A complete, replicable recipe for putting a window that is already running
on a Linux server into an AI browser agent's native browser — as a live,
takeover-able view the user can watch and drive from their phone, exactly
like any web page the agent is working on.

Built and verified live in November 2026 with two real apps: a job-board
web app in its own Chrome/Xvfb pair, and a desktop Chrome profile window
(an Instagram login that had defeated every other route — see
"Why this exists"). Everything below is the distilled procedure, including
the failure modes, which are the valuable part.

## Why this exists

AI browser agents can only open public-internet URLs in their own browser.
A window on a server (a signed-in web app, a desktop browser profile) is
invisible to them, and handing the user a remote-desktop link inverts the
relationship: the *user* has to go find the window, sign in again, and
drive a desktop UI squeezed onto a phone.

This stack fixes that by making the server window behave like a website:

- the agent opens one URL in its own browser and signs in with a saved
  login, like any other site;
- the user sees the live window surfaced in the agent's app, phone-sized,
  and can take over and tap/click in it directly;
- the app itself is never restarted, re-profiled, or re-logged-in — the
  stack mirrors its existing X display.

## Architecture

```
┌─ Server (Linux, X11) ─────────────────────────────────────────────┐
│                                                                    │
│  App's real Chrome ── runs on its own X display (e.g. :103,        │
│  (untouched)          or the shared desktop :101)                  │
│        │                                                           │
│        ▼  kasmxproxy -a :<src> -v :<dst>                           │
│  Kasm display (:110) ── served by KasmVNC server                   │
│        ▲                                                           │
│        │  window-follow.py: resizes the app window on the          │
│        └─ source display to the session size (both directions)     │
│                                                                    │
│  frontdoor.py (this repo) ── form login + HMAC session cookie,     │
│  proxies HTTP+WebSocket to Kasm, presenting Kasm's own            │
│  credential server-side. Kasm auth stays ON behind it.             │
└────────┬───────────────────────────────────────────────────────────┘
         │  TLS front that makes the door publicly reachable
         │  (Tailscale Funnel, Cloudflare Tunnel, reverse proxy…)
         ▼
  Agent's browser ── opens the door URL, saved-login signs in,
  Kasm client connects → the app window, live.
         │
         ▼  surfaced into the user's app as a native browser view
  User's phone ── watches, and takes over with real taps/clicks.
```

Components:

| Piece | Role |
|---|---|
| App Chrome + its X display | The thing being surfaced. Never restart or re-profile it. |
| `kasmxproxy` | Mirrors the existing source display into a Kasm display. Ships with KasmVNC. |
| KasmVNC server (1.4.x) | Serves the Kasm display; bind it to a private interface only. |
| `window-follow.py` (this repo) | One instance per app; keeps the app window exactly session-sized so phone clients get a reflowed app, not a cropped desktop. |
| `frontdoor.py` (this repo) | Form-login gate in front of Kasm (see the README for why the native basic-auth prompt is unusable). |
| TLS front | Publishes **only the door**. The Kasm port itself stays private. |
| Saved login for the door URL | Lets the agent sign in "by default" — the user should see the work, never a login page. |

## Per-app setup

For each app you want to surface:

1. **Identify the app's existing X display** and leave its Chrome alone.
   Note its size (`DISPLAY=:<src> xdpyinfo | grep dimensions`) — that
   size is a hard cap (an Xvfb cannot grow past its launch geometry).
2. **Allocate a Kasm display** (e.g. `:110`) and start KasmVNC on it:
   `kasmvncserver :110 -geometry 1440x1000 -depth 24`, with
   `desktop.allow_resize: true` in `~/.vnc/kasmvnc.yaml` and the server
   bound to a private (e.g. tailnet) interface.
3. **Mirror the app:** `kasmxproxy -a :<src> -v :110`. Order matters:
   the Kasm server first (it creates the display), then the proxy.
4. **Install a window-follow instance** for the app
   (`kasm-window-follow.service` in this repo): set `WF_SRC`,
   `WF_DST`, and `WF_NAME_HINT` (a substring of the app window's title).
5. **Put the front door in front** (README has the full setup) and
   publish only the door through your TLS front.
6. **Enroll a saved login** for the door's origin in whatever credential
   store your agent's browser uses, so sign-in is automatic.
7. **Open the door URL in the agent's browser**, let it sign in, connect
   the Kasm session, and set the client's **Scaling Mode → Remote
   Resizing** (Control Panel → Settings). It persists per browser
   profile; re-verify after outages or profile changes.
8. **Surface the agent's browser window to the user** through your
   agent platform's native "open this browser view" mechanism — the
   view, not a link or a description of one.

## Phone sizing: all three parts are required

A phone-sized, full-bleed app (no letterbox, no cropped controls) needs:

1. **Client:** Scaling Mode = Remote Resizing, so the session adopts the
   client area (on a phone, the phone's size).
2. **Server:** `desktop.allow_resize: true` in `kasmvnc.yaml`.
3. **Window-follow running for that app.** Without it, a smaller session
   *crops* the fixed source display: centered controls — a login form, a
   password prompt — land off-screen and the user can see the app but
   cannot use it. A larger session *under-fills* (black bar), because
   the source display cannot grow; follow clamps the session to the
   source size and lets the client scale the image instead.

Verify both directions before calling a surface done:

- **Shrink (phone behavior):** `DISPLAY=:<dst> xrandr --fb 500x930`
  (an X error line from xrandr is normal — judge by read-back). After
  ~4s, `xdpyinfo` on the Kasm display must read 500x930 and the app
  window (`DISPLAY=:<src> xdotool search --name <hint>` →
  `getwindowgeometry`) must read exactly 500x930 at 0,0.
- **Clamp (big-screen behavior):** with a large client connected,
  confirm the session never exceeds the source size and the window
  matches it exactly.
- **Visually:** the app fills the whole view in the browser — no crop,
  no black bar, no letterbox.
- Leave the resting size at the source geometry when done testing.

## Operating lessons (the expensive ones)

- **Never disable Kasm's own authentication to make sign-in easier.**
  The front door is an *additional* gate; Kasm auth stays fully enabled
  behind it.
- **Browser-UI prompts live only in the framebuffer.** A password
  manager's account chooser, a passkey sheet, and similar native Chrome
  prompts render in the desktop/Kasm image but *never* in the page DOM
  or in automation/CDP reads. A page-side read that shows an empty
  password field does **not** mean no saved password exists — in the
  Instagram deployment, the password manager's "Sign in to
  www.instagram.com" chooser sat open on the desktop with the right
  entry selected while every page read reported an empty field, which
  sent the whole session down a password-reset detour. Before
  concluding anything about autofill, prompts, or what is "blocking
  the view", screenshot the Kasm display server-side
  (`DISPLAY=:<dst> scrot out.png`) and look at it.
- **A surfacing error from the client app is not proof of failure.**
  The app may report "not on screen" for a view that did in fact land.
  Treat that result as *unconfirmed*, reconcile with what the user
  reports seeing, and keep re-surfacing on later exchanges rather than
  declaring failure — or success — from the status code alone.
  Conversely, a success status proves the view is on screen, not that
  what's in it works: check the page/session state before surfacing.
- **Takeover is only proven by the user's input landing** (their tap
  moving something in the app), not by the window rendering.
- **Judge process health by sustained measurement, not snapshots.**
  `ps %CPU` is a lifetime average — a sub-second process caught in its
  first instant shows triple digits with 0:00 CPU time. On
  browser-heavy boxes, load average also counts uninterruptible (D)
  state tasks, so load ≠ CPU burn. Measure over a full run before
  declaring anything is spinning.
- **Session verification should read the browser's own state.**
  Cookie stores live in different paths across Chrome versions
  (`<profile>/Cookies` vs `<profile>/Network/Cookies`); check both
  before concluding a session is absent, and confirm against what the
  page itself shows.

## Security posture

- KasmVNC binds to a private interface only; the only public surface is
  the front door, behind TLS.
- The door checks the same credential as Kasm and adds its own signed,
  expiring session cookie. Wrong credentials → 401; unauthenticated
  requests never reach Kasm.
- Credential and secret files are `0600`, never committed, never logged.
  This repo contains no real hosts, addresses, usernames, or passwords —
  keep it that way when you adapt it.
- Surfacing is a view into a live, signed-in session. Treat anyone who
  can open the door as sitting at that machine's keyboard, because
  they are.

## The Instagram deployment (worked example)

The second app surfaced with this stack was a desktop Chrome profile
window on the shared desktop display — a separate Meta account whose
login had failed for days through every conventional route: the
account's password was saved in that profile's own password manager,
but the chooser prompt that offers it is invisible to automation, the
profile's browser has no automation port, and other browsers hit the
platform's device-checkpoint walls.

With the stack above: the desktop display was mirrored into the Kasm
display, a follow instance was pointed at the Instagram window, the
agent opened the door in its browser (signed in by default), surfaced
the view phone-sized, and the user tapped the password manager's
**Sign in** and the page's **Log in** in the live view — the two taps
automation could never make. Total new infrastructure: one proxy
process and one follow instance; the app Chrome was never restarted.
