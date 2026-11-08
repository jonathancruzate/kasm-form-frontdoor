# kasm-form-frontdoor

A form-login front door for [KasmVNC](https://kasmweb.com/kasmvnc): a standard HTML sign-in form and signed session-cookie gate in front of a KasmVNC server, with HTTP/WebSocket proxying. Pure Python standard library — no dependencies.

## Why

KasmVNC gates its web client with the browser's **native HTTP basic-auth prompt**. That prompt is a dead end for a lot of legitimate tooling:

- Password managers and browser saved-login flows fill *forms*; there is no form on a native prompt.
- Browser automation (including AI browser agents) cannot type into the native prompt — it often doesn't even render in an automated session, leaving a blank page.

The usual temptation is to disable Kasm's basic auth. Don't. This front door solves the login UX **without weakening authentication**:

1. The door serves a normal HTML login form (`username` / `password` fields) that any password manager or saved-login flow can fill.
2. It verifies the **same credential** KasmVNC uses, and only then issues a signed, expiring (30-day) session cookie.
3. Authenticated traffic is proxied to KasmVNC — HTTP and WebSocket — presenting Kasm's credential server-side. KasmVNC's own authentication stays fully enabled behind the door; the door is an additional gate, not a replacement.

Unauthenticated requests are redirected to the form; wrong credentials get a 401; nothing reaches Kasm without passing the door.

## Setup

```bash
mkdir -p ~/.kasm-frontdoor
# The password for the door — use the same one as your KasmVNC user:
printf '%s' 'your-kasm-password' > ~/.kasm-frontdoor/password.txt
chmod 600 ~/.kasm-frontdoor/password.txt
```

Run it (point it at your KasmVNC web port):

```bash
FD_USERNAME=your-kasm-user \
FD_UPSTREAM_HOST=127.0.0.1 \
FD_UPSTREAM_PORT=8443 \
FD_UPSTREAM_HTTPS=1 \
python3 frontdoor.py
```

It listens on `127.0.0.1:8952` by default. Put it behind something that terminates TLS and makes it reachable — [Tailscale Funnel](https://tailscale.com/kb/1223/funnel), a Cloudflare tunnel, or your own reverse proxy — and keep the KasmVNC port itself off the public internet.

```bash
# Example: publish the door through Tailscale Funnel
sudo tailscale funnel --bg --https=443 http://127.0.0.1:8952
```

A systemd unit template is included (`kasm-frontdoor.service`).

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `FD_USERNAME` | `user` | Login username (your KasmVNC user) |
| `FD_PASSWORD_FILE` | `~/.kasm-frontdoor/password.txt` | File containing the shared password |
| `FD_SECRET_FILE` | `~/.kasm-frontdoor/secret.txt` | Cookie HMAC secret (auto-created, 0600) |
| `FD_UPSTREAM_HOST` | `127.0.0.1` | KasmVNC host |
| `FD_UPSTREAM_PORT` | `8443` | KasmVNC web port |
| `FD_UPSTREAM_HTTPS` | `0` | Set `1` when Kasm serves HTTPS with a self-signed cert |
| `FD_LISTEN_HOST` | `127.0.0.1` | Listen address |
| `FD_LISTEN_PORT` | `8952` | Listen port |

The door reached Kasm over plain HTTP in its first deployment (Kasm bound to a private tailnet address); use `FD_UPSTREAM_HTTPS=1` for a stock HTTPS KasmVNC — the self-signed upstream certificate is accepted because that hop never leaves your own machine/network and the browser-facing side is covered by your TLS front.

## Notes

- Sessions are stateless HMAC cookies (`fd_session`, HttpOnly, Secure, SameSite=Lax). Rotating the secret file logs everyone out.
- Login attempts are compared in constant time. Put rate limiting at your TLS front if you expose it widely.
- Built for a single-user/small-team KasmVNC. For multi-user SSO, look at Kasm Workspaces or an OAuth proxy instead.

## License

MIT
