# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

kindle-converter converts EPUB files to MOBI and delivers them to a Kindle 4 wirelessly — despite Amazon ending support for pre-2013 Kindles in May 2026.

The key insight: the Kindle 4's experimental browser can reach the public internet (HTTPS works) but not local network IPs. We bridge that gap with a Cloudflare tunnel, serving books from a local Python HTTP server through a public URL the Kindle can reach.

## Commands

No build step. All scripts run directly with Python 3.10+.

```powershell
# Convert EPUBs to MOBI — pass a directory or individual files
python convert.py books -o books

# Serve books via Cloudflare tunnel
python serve.py books --tunnel

# Serve books locally (no tunnel)
python serve.py books

# Email books to Kindle (newer devices only) — first-time setup
python send.py --setup
python send.py books/*.mobi

# Debug server for testing Kindle 4 browser compatibility
python test_server.py [port]
```

No test suite exists. `test_server.py` is the manual compatibility-testing tool.

### Stable Kindle bookmark (one-time setup)

The Cloudflare tunnel URL changes on every restart. To get a permanent bookmark URL, deploy a Cloudflare Worker that always redirects to the current tunnel:

```powershell
python serve.py --cf-setup
```

Requires a free Cloudflare account, an API token (use the "Edit Cloudflare Workers" template), and your Account ID from the dashboard sidebar. Saves credentials to `config.json`. After setup, `serve.py --tunnel` automatically updates the Worker's KV redirect on each start.

The Worker shows a landing page at `https://kindle.SUBDOMAIN.workers.dev` — the Kindle bookmarks that URL, then taps "Open Library →" to follow through to the current tunnel. The `/go` path does the actual HTTP 302 redirect.

## Architecture

```
EPUB files
  → convert.py (Calibre ebook-convert wrapper, ThreadPoolExecutor)
  → MOBI files
  → serve.py --tunnel (SimpleHTTPRequestHandler subclass + cloudflared)
  → Cloudflare tunnel (public https://*.trycloudflare.com)
  → Cloudflare Worker KV (stable bookmark URL → current tunnel URL)
  → Kindle 4 experimental browser
  → downloaded to Kindle library
```

`send.py` is an alternative delivery path (Gmail SMTP → Kindle email address) for newer devices only.

## Key implementation details

- **cloudflared stderr parsing**: `start_tunnel()` reads cloudflared's stderr line-by-line and extracts the tunnel URL via regex (`https://[a-zA-Z0-9-]+\.trycloudflare\.com`). A background daemon thread drains remaining stderr to prevent the subprocess from blocking.
- **Cloudflare Worker redirect**: `update_worker_redirect()` writes the current tunnel URL to a KV namespace via the Cloudflare API. The Worker script reads KV on each request and returns a 302 redirect from `/go`. The landing page (`/`) stays at the stable workers.dev URL for bookmarking.
- **Worker deployment**: `cf_setup()` uploads an ES module Worker via multipart form (`main_module: worker.js`, `Content-Type: application/javascript+module`). Credentials saved to `config.json` are reused on subsequent runs.
- **Path traversal protection**: `KindleHandler._serve_file()` resolves the requested path and checks it starts with `books_dir` before serving.
- **HTML constraints**: The listing page uses inline styles, tables, and serif font — no CSS3, no JavaScript. Targets ~2011 WebKit on the Kindle 4.
- **Email batching**: `send_batch()` in `send.py` accumulates attachments until the next file would exceed 25 MB, then sends and starts a new batch. Subject is set to `"convert"` when non-.mobi files are present (triggers Kindle conversion service).
- **config.json**: Stores Gmail credentials (from `send.py --setup`) and Cloudflare credentials (from `serve.py --cf-setup`). Created with 0o600 permissions.
- **Windows glob expansion**: `convert.py` expands glob patterns (e.g. `*.epub`) itself since PowerShell doesn't expand them. Directories are expanded to all `.epub` files within.

## Kindle 4 browser constraints

- Cannot reach local/private IPs (192.168.x.x, 10.x.x.x)
- Can reach public HTTPS (google.com, trycloudflare.com, workers.dev)
- URL shorteners (is.gd, tinyurl) block trycloudflare.com — use the Workers landing page instead
- No CSS3, no JavaScript, no flexbox — ~2011 WebKit
- Can download `.mobi` files via links (appear in Kindle library)
- Bookmarks save the current address bar URL — use a landing page (not a redirect) as the bookmark target

## Design decisions

- **Zero pip dependencies** — stdlib only (Python 3.10+). No pip install step.
- **Separate scripts** — each does one thing; compose via shell.
- **Cloudflare tunnel over local network** — Kindle 4 routes browser traffic through a proxy that blocks RFC-1918 addresses; `cloudflared` creates a free, no-account public HTTPS URL.
- **Cloudflare Worker as stable bookmark** — trycloudflare URLs are random per session; a Worker + KV store provides a permanent URL that updates automatically on each server start.
- **Landing page instead of instant redirect** — the Worker serves an HTML landing page at `/` so the Kindle can bookmark the stable URL before tapping through to the library.
