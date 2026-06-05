# kindle-converter

Convert EPUB files to MOBI and wirelessly transfer them to your Kindle 4 — even though Amazon dropped support.

Uses a Cloudflare tunnel to bypass the Kindle 4's inability to reach local networks. Zero pip dependencies.

## What's in the box

| Script | What it does |
|---|---|
| `convert.py` | Batch convert EPUB → MOBI using Calibre |
| `serve.py` | Serve books wirelessly via Cloudflare tunnel |

## Prerequisites

- **Python 3.10+**
- **Calibre** — [download here](https://calibre-ebook.com/download)
- **cloudflared** — for wireless transfer to Kindle 4

### Install cloudflared

```powershell
# Windows
winget install cloudflare.cloudflared

# macOS
brew install cloudflared

# Linux — see https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
```

## Quick start

```powershell
# 1. Convert your EPUBs
python convert.py books -o books

# 2. Serve with tunnel
python serve.py books --tunnel
```

The server prints a tunnel URL like `https://abc123.trycloudflare.com`. Open it in your Kindle 4's Experimental Browser and tap a book to download.

**The tunnel URL changes every restart.** For a permanent bookmark, see [Stable URL setup](#stable-url-one-time-setup) below.

## Stable URL (one-time setup)

Set up a Cloudflare Worker that gives your Kindle a URL that never changes:

```powershell
python serve.py --cf-setup
```

You'll need a free Cloudflare account:
1. Sign up at [dash.cloudflare.com](https://dash.cloudflare.com)
2. Create an API token using the **"Edit Cloudflare Workers"** template at [dash.cloudflare.com/profile/api-tokens](https://dash.cloudflare.com/profile/api-tokens)
3. Find your **Account ID** in the right sidebar of the dashboard

After setup, you get a permanent URL like `https://kindle.YOURNAME.workers.dev`. On the Kindle:
1. Navigate to that URL — it shows a landing page
2. Bookmark it from the address bar
3. Tap **"Open Library →"** to reach your books

From then on, `serve.py --tunnel` automatically updates the Worker so your bookmark always works.

## Usage

### Convert

```powershell
python convert.py books                      # convert all EPUBs in a folder
python convert.py books -o books             # output to specific folder
python convert.py book.epub                  # single file
python convert.py books -j 8                 # 8 parallel conversions
```

### Serve wirelessly

```powershell
python serve.py books --tunnel               # start server + tunnel
python serve.py books                        # local only (no tunnel)
python serve.py books --port 9090            # custom port
```

## How it works

The Kindle 4's browser can reach the public internet but not local network IPs. `serve.py --tunnel` starts a Cloudflare tunnel that exposes your local server at a public URL:

```
Kindle browser → workers.dev (stable bookmark)
              → trycloudflare.com tunnel → your PC → serve.py
```

The tunnel URL is temporary and changes each restart. The Cloudflare Worker acts as a stable middleman — `serve.py` updates it with the new URL on every start.

## Notes

- `convert.py` runs 4 parallel conversions by default (`-j` to change).
- `config.json` stores Cloudflare credentials from `serve.py --cf-setup` (auto-created, gitignored, owner-only permissions).
