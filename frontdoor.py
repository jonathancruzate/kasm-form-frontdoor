#!/usr/bin/env python3
"""Form-login front door for KasmVNC.

KasmVNC gates its web client with the browser's native HTTP basic-auth
prompt. That prompt cannot be filled by password managers' form fillers,
browser automation, or saved-login flows — the page never exposes a form.
This small proxy fixes the login UX without weakening authentication:

- It serves a standard HTML form login (username/password fields) that any
  password manager or browser saved-login flow can fill.
- On success it issues a signed, expiring session cookie.
- It then proxies HTTP and WebSocket traffic to your KasmVNC server,
  presenting Kasm's own credential server-side.

KasmVNC's authentication stays fully enabled behind the door. The door is
an additional gate in front of it, not a replacement, and it checks the
same credential before letting any traffic through.

Pure Python standard library (asyncio). No dependencies.

Configuration is via environment variables:

  FD_USERNAME        Login username                (default: "user")
  FD_PASSWORD_FILE   File holding the password     (default: ~/.kasm-frontdoor/password.txt)
  FD_SECRET_FILE     File holding the cookie HMAC secret (created if absent)
                     (default: ~/.kasm-frontdoor/secret.txt)
  FD_UPSTREAM_HOST   KasmVNC host                  (default: 127.0.0.1)
  FD_UPSTREAM_PORT   KasmVNC web port              (default: 8443)
  FD_UPSTREAM_HTTPS  "1" if Kasm serves HTTPS with a self-signed cert
                     (default: 0)
  FD_LISTEN_HOST     Listen address                (default: 127.0.0.1)
  FD_LISTEN_PORT     Listen port                   (default: 8952)

Put it behind a TLS-terminating public front (e.g. Tailscale Funnel,
a Cloudflare tunnel, or a reverse proxy) and keep the Kasm port itself
off the public internet.
"""
import asyncio
import base64
import hashlib
import hmac
import os
import secrets
import ssl
import time
from urllib.parse import parse_qs


def env(name, default):
    return os.environ.get(name, default)


USER = env("FD_USERNAME", "user")
PW_FILE = os.path.expanduser(env("FD_PASSWORD_FILE", "~/.kasm-frontdoor/password.txt"))
SECRET_FILE = os.path.expanduser(env("FD_SECRET_FILE", "~/.kasm-frontdoor/secret.txt"))
UP_HOST = env("FD_UPSTREAM_HOST", "127.0.0.1")
UP_PORT = int(env("FD_UPSTREAM_PORT", "8443"))
UP_HTTPS = env("FD_UPSTREAM_HTTPS", "0") == "1"
LISTEN_HOST = env("FD_LISTEN_HOST", "127.0.0.1")
LISTEN_PORT = int(env("FD_LISTEN_PORT", "8952"))
COOKIE = "fd_session"
TTL = 30 * 24 * 3600

with open(PW_FILE) as f:
    KASM_PW = f.read().strip()

if not os.path.exists(SECRET_FILE):
    os.makedirs(os.path.dirname(SECRET_FILE), exist_ok=True)
    with open(SECRET_FILE, "w") as f:
        f.write(secrets.token_hex(32))
    os.chmod(SECRET_FILE, 0o600)
with open(SECRET_FILE) as f:
    SECRET = f.read().strip().encode()

BASIC = "Basic " + base64.b64encode(f"{USER}:{KASM_PW}".encode()).decode()

LOGIN_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in</title>
<style>body{font-family:system-ui,sans-serif;background:#0b0e14;color:#e6e6e6;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}main{background:#151a24;padding:2rem;border-radius:12px;width:min(320px,90vw)}h1{font-size:1.1rem;margin:0 0 1rem}label{display:block;font-size:.85rem;margin:.8rem 0 .3rem}input{width:100%;padding:.6rem;border-radius:8px;border:1px solid #2a3242;background:#0b0e14;color:#e6e6e6;box-sizing:border-box}button{margin-top:1.2rem;width:100%;padding:.65rem;border:0;border-radius:8px;background:#3b82f6;color:#fff;font-weight:600;cursor:pointer}.err{color:#f87171;font-size:.85rem;min-height:1em}</style>
</head><body><main>
<h1>Sign in</h1>
<div class="err">{err}</div>
<form method="post" action="/__login">
<label for="username">Username</label>
<input id="username" name="username" type="text" autocomplete="username" required>
<label for="password">Password</label>
<input id="password" name="password" type="password" autocomplete="current-password" required>
<button type="submit">Sign in</button>
</form></main></body></html>"""


def make_cookie():
    exp = str(int(time.time()) + TTL)
    msg = f"{USER}:{exp}"
    sig = hmac.new(SECRET, msg.encode(), hashlib.sha256).hexdigest()
    return f"{msg}:{sig}"


def valid_cookie(value):
    try:
        user, exp, sig = value.split(":")
        msg = f"{user}:{exp}"
        good = hmac.new(SECRET, msg.encode(), hashlib.sha256).hexdigest()
        return (
            user == USER
            and hmac.compare_digest(sig, good)
            and int(exp) > int(time.time())
        )
    except Exception:
        return False


def session_ok(headers):
    for part in headers.get("cookie", "").split(";"):
        part = part.strip()
        if part.startswith(COOKIE + "="):
            return valid_cookie(part[len(COOKIE) + 1:])
    return False


async def read_request(reader):
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = await reader.read(4096)
        if not chunk:
            return None, b""
        buf += chunk
        if len(buf) > 65536:
            return None, b""
    head, rest = buf.split(b"\r\n\r\n", 1)
    lines = head.decode("latin1").split("\r\n")
    method, path, _ = lines[0].split(" ", 2)
    headers = {}
    raw_headers = []
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
            raw_headers.append((k.strip(), v.strip()))
    body = rest
    clen = int(headers.get("content-length", "0") or "0")
    while len(body) < clen:
        chunk = await reader.read(min(65536, clen - len(body)))
        if not chunk:
            break
        body += chunk
    return (method, path, headers, raw_headers), body


async def reply(writer, status, body=b"", ctype="text/html; charset=utf-8", extra=None):
    reasons = {200: "OK", 302: "Found", 401: "Unauthorized", 502: "Bad Gateway"}
    out = f"HTTP/1.1 {status} {reasons.get(status, 'OK')}\r\n"
    out += f"Content-Type: {ctype}\r\nContent-Length: {len(body)}\r\n"
    for k, v in (extra or []):
        out += f"{k}: {v}\r\n"
    out += "Connection: close\r\n\r\n"
    writer.write(out.encode() + body)
    await writer.drain()


async def pipe(src, dst):
    try:
        while True:
            data = await src.read(65536)
            if not data:
                break
            dst.write(data)
            await dst.drain()
    except Exception:
        pass
    try:
        dst.close()
    except Exception:
        pass


async def handle(reader, writer):
    try:
        req, body = await read_request(reader)
        if req is None:
            writer.close()
            return
        method, path, headers, raw_headers = req
        clean_path = path.split("?")[0]

        if clean_path == "/__login":
            if method == "GET":
                await reply(writer, 200, LOGIN_HTML.replace("{err}", "").encode())
            elif method == "POST":
                form = parse_qs(body.decode("utf-8", "replace"))
                user = (form.get("username") or [""])[0]
                pw = (form.get("password") or [""])[0]
                if user == USER and hmac.compare_digest(pw, KASM_PW):
                    cookie = (
                        f"{COOKIE}={make_cookie()}; Path=/; HttpOnly; Secure; "
                        f"SameSite=Lax; Max-Age={TTL}"
                    )
                    await reply(
                        writer, 302, b"",
                        extra=[("Location", "/"), ("Set-Cookie", cookie)],
                    )
                else:
                    await reply(
                        writer, 401,
                        LOGIN_HTML.replace("{err}", "Invalid username or password.").encode(),
                    )
            else:
                await reply(writer, 401, b"method not allowed", "text/plain")
            writer.close()
            return

        if not session_ok(headers):
            await reply(writer, 302, b"", extra=[("Location", "/__login")])
            writer.close()
            return

        # Authenticated: proxy to Kasm, presenting its credential server-side.
        try:
            if UP_HTTPS:
                ctx = ssl._create_unverified_context()
                up_reader, up_writer = await asyncio.open_connection(
                    UP_HOST, UP_PORT, ssl=ctx, server_hostname=UP_HOST
                )
            else:
                up_reader, up_writer = await asyncio.open_connection(UP_HOST, UP_PORT)
        except Exception:
            await reply(writer, 502, b"upstream unavailable", "text/plain")
            writer.close()
            return
        out_lines = [f"{method} {path} HTTP/1.1"]
        for k, v in raw_headers:
            lk = k.lower()
            if lk in ("authorization", "host"):
                continue
            out_lines.append(f"{k}: {v}")
        out_lines.append(f"Host: {UP_HOST}:{UP_PORT}")
        out_lines.append(f"Authorization: {BASIC}")
        up_writer.write(("\r\n".join(out_lines) + "\r\n\r\n").encode() + body)
        await up_writer.drain()
        await asyncio.gather(pipe(reader, up_writer), pipe(up_reader, writer))
    except Exception:
        try:
            writer.close()
        except Exception:
            pass


async def main():
    server = await asyncio.start_server(handle, LISTEN_HOST, LISTEN_PORT)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
