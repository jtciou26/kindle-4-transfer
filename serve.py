#!/usr/bin/env python3
"""Book server for Kindle 4 — serves ebooks over a Cloudflare tunnel.

Usage:
    python serve.py ./books --tunnel      # start server + tunnel (recommended)
    python serve.py ./books               # local-only (if LAN access works)
    python serve.py ./books --port 9090   # custom port
"""

import argparse
import datetime
import html
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

EBOOK_EXTENSIONS = {".mobi", ".azw", ".azw3", ".pdf", ".txt", ".epub"}
CONFIG_PATH = Path(__file__).parent / "config.json"


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_config(config: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
    try:
        CONFIG_PATH.chmod(0o600)
    except Exception:
        pass  # chmod not meaningful on Windows


_WORKER_SCRIPT = """\
export default {
  async fetch(request, env) {
    const dest = await env.KINDLE_KV.get('url')
    const path = new URL(request.url).pathname
    if (path === '/go') {
      if (!dest) return new Response('Server not running. Start serve.py --tunnel first.', {status: 503})
      return Response.redirect(dest, 302)
    }
    const status = dest ? 'Server is running. Tap below to open.' : 'Server not running. Start serve.py --tunnel first.'
    const link = dest ? '<p><a href="/go" style="font-size:22px;">Open Library &rarr;</a></p>' : ''
    return new Response(
      '<!DOCTYPE html><html><head><meta charset="utf-8"><title>Kindle Library</title></head>' +
      '<body style="margin:15px;font-family:serif;">' +
      '<h3>Kindle Library</h3>' +
      '<p style="color:#666;font-size:14px;">Bookmark THIS page, then tap the link.</p>' +
      '<p>' + status + '</p>' + link +
      '</body></html>',
      {headers: {'Content-Type': 'text/html'}}
    )
  }
}
"""


def _cf_request(method: str, path: str, token: str, body: bytes = None, content_type: str = None) -> dict:
    req = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4{path}", data=body, method=method
    )
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("User-Agent", "kindle-converter")
    if body and content_type:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        # Cloudflare always returns JSON even on errors — return it so callers can check error codes
        try:
            return json.loads(e.read())
        except Exception:
            raise RuntimeError(f"HTTP {e.code}")


def cf_setup() -> None:
    """One-time setup: deploy a Cloudflare Worker for a permanent Kindle redirect URL."""
    print()
    print("  Cloudflare Workers Setup")
    print("  ─────────────────────────────────────────────")
    print("  1. Account ID — shown on the right of https://dash.cloudflare.com/")
    print("  2. API token  — create at https://dash.cloudflare.com/profile/api-tokens")
    print("       Use template 'Edit Cloudflare Workers' or add permissions:")
    print("         Account > Workers Scripts > Edit")
    print("         Account > Workers KV Storage > Edit")
    print()

    saved = _load_config()
    token = saved.get("cf_api_token", "")
    account_id = saved.get("cf_account_id", "")
    worker_name = saved.get("cf_worker_name", "kindle")

    if token and account_id:
        print(f"  Using saved credentials (Account ID: {account_id[:8]}...)")
        new_name = input(f"  Worker name [{worker_name}]: ").strip()
        if new_name:
            worker_name = new_name
    else:
        token = input("  API token : ").strip()
        account_id = input("  Account ID: ").strip()
        worker_name = input(f"  Worker name [{worker_name}]: ").strip() or worker_name

    if not token or not account_id:
        print("  Cancelled.")
        return

    # Create KV namespace (or reuse if it already exists)
    print("\n  Creating KV namespace...")
    try:
        result = _cf_request("POST", f"/accounts/{account_id}/storage/kv/namespaces",
                             token, json.dumps({"title": "kindle"}).encode(),
                             "application/json")
    except RuntimeError as e:
        print(f"  ERROR: {e}")
        sys.exit(1)

    if result.get("success"):
        namespace_id = result["result"]["id"]
    elif any(e.get("code") == 10014 for e in result.get("errors", [])):
        # Already exists — find it by listing namespaces
        print("  (namespace already exists, reusing it)")
        try:
            list_result = _cf_request("GET", f"/accounts/{account_id}/storage/kv/namespaces", token)
        except RuntimeError as e:
            print(f"  ERROR listing namespaces: {e}")
            sys.exit(1)
        ns = next((n for n in list_result.get("result", []) if n["title"] == "kindle"), None)
        if not ns:
            print("  ERROR: could not find existing 'kindle' namespace.")
            sys.exit(1)
        namespace_id = ns["id"]
    else:
        print(f"  ERROR: {result.get('errors')}")
        sys.exit(1)

    # Upload Worker with KV binding
    print("  Deploying Worker...")
    metadata = json.dumps({
        "main_module": "worker.js",
        "bindings": [{"type": "kv_namespace", "name": "KINDLE_KV", "namespace_id": namespace_id}]
    })
    boundary = "KindleWorkerBoundary"
    multipart = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="metadata"\r\n'
        f"Content-Type: application/json\r\n\r\n"
        f"{metadata}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="worker.js"; filename="worker.js"\r\n'
        f"Content-Type: application/javascript+module\r\n\r\n"
        f"{_WORKER_SCRIPT}\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")

    try:
        result = _cf_request(
            "PUT", f"/accounts/{account_id}/workers/scripts/{worker_name}",
            token, multipart, f"multipart/form-data; boundary={boundary}"
        )
    except RuntimeError as e:
        print(f"  ERROR deploying Worker: {e}")
        sys.exit(1)
    if not result.get("success"):
        print(f"  ERROR: {result.get('errors')}")
        sys.exit(1)

    # Enable the workers.dev public URL for this script
    print("  Enabling workers.dev URL...")
    _cf_request("POST", f"/accounts/{account_id}/workers/scripts/{worker_name}/subdomain",
                token, json.dumps({"enabled": True}).encode(), "application/json")

    # Get workers.dev subdomain name for the account
    worker_url = f"https://{worker_name}.YOUR-SUBDOMAIN.workers.dev"
    try:
        sub = _cf_request("GET", f"/accounts/{account_id}/workers/subdomain", token)
        if sub.get("success") and sub.get("result", {}).get("subdomain"):
            worker_url = f"https://{worker_name}.{sub['result']['subdomain']}.workers.dev"
    except Exception:
        pass

    # Save config
    config = _load_config()
    config["cf_api_token"] = token
    config["cf_account_id"] = account_id
    config["cf_kv_namespace_id"] = namespace_id
    config["cf_worker_name"] = worker_name
    _save_config(config)

    print()
    print("  Done! Saved to config.json.")
    print()
    print("  BOOKMARK THIS URL ON YOUR KINDLE (it never changes):")
    print(f"  {worker_url}")
    if "YOUR-SUBDOMAIN" in worker_url:
        print("  (Replace YOUR-SUBDOMAIN with your workers.dev subdomain from the Cloudflare dashboard)")
    print()
    print("  Run  python serve.py books --tunnel  to start.")
    print("  The Worker instantly redirects to the current tunnel URL.")


def update_worker_redirect(account_id: str, token: str, namespace_id: str, tunnel_url: str) -> None:
    """Write the current tunnel URL into Cloudflare KV so the Worker redirects to it."""
    _cf_request(
        "PUT",
        f"/accounts/{account_id}/storage/kv/namespaces/{namespace_id}/values/url",
        token,
        tunnel_url.encode("utf-8"),
        "text/plain",
    )


def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"



def find_cloudflared() -> str | None:
    """Check if cloudflared is installed."""
    if shutil.which("cloudflared"):
        return "cloudflared"
    # Common Windows install paths
    for path in [
        Path.home() / ".cloudflared" / "cloudflared.exe",
        Path(os.environ.get("PROGRAMFILES", "")) / "cloudflared" / "cloudflared.exe",
    ]:
        if path.exists():
            return str(path)
    return None


def start_tunnel(port: int) -> tuple[subprocess.Popen, str]:
    """Start a Cloudflare quick tunnel (random URL) and return (process, tunnel_url)."""
    cf = find_cloudflared()
    if not cf:
        print("  ERROR: cloudflared not found.")
        print("  Install it:")
        print("    Windows:  winget install cloudflare.cloudflared")
        print("    macOS:    brew install cloudflared")
        print("    Linux:    https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/")
        sys.exit(1)

    proc = subprocess.Popen(
        [cf, "tunnel", "--url", f"http://localhost:{port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # cloudflared logs the tunnel URL to stderr — wait up to 30s for it
    tunnel_url = None
    url_pattern = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
    deadline = time.time() + 30

    while time.time() < deadline:
        line = proc.stderr.readline().decode("utf-8", errors="replace")
        if not line:
            if proc.poll() is not None:
                print("  ERROR: cloudflared exited unexpectedly.")
                remaining = proc.stderr.read().decode("utf-8", errors="replace")
                if remaining:
                    print(f"  {remaining[:500]}")
                sys.exit(1)
            continue

        match = url_pattern.search(line)
        if match:
            tunnel_url = match.group(0)
            break

    if not tunnel_url:
        proc.terminate()
        print("  ERROR: Could not get tunnel URL (timed out after 30s).")
        sys.exit(1)

    return proc, tunnel_url


def start_serveo_tunnel(port: int, name: str) -> tuple[subprocess.Popen, str]:
    """Start a serveo.net SSH tunnel with a stable custom subdomain.

    Requires: ssh client (built into Windows 10+, macOS, Linux).
    Returns (process, tunnel_url) where tunnel_url is always https://{name}.serveo.net.
    """
    ssh = shutil.which("ssh")
    if not ssh:
        print("  ERROR: ssh not found.")
        print("  On Windows: Settings > Apps > Optional Features > Add OpenSSH Client")
        sys.exit(1)

    proc = subprocess.Popen(
        [ssh,
         "-R", f"{name}:80:localhost:{port}",
         "-o", "StrictHostKeyChecking=no",
         "-o", "ServerAliveInterval=60",
         "-o", "ExitOnForwardFailure=yes",
         "serveo.net"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,  # merge so we see all output in one stream
    )

    # serveo prints "Forwarding HTTP traffic from https://name.serveo.net" on success
    deadline = time.time() + 30
    confirmed = False

    while time.time() < deadline:
        line = proc.stdout.readline().decode("utf-8", errors="replace")
        if not line:
            if proc.poll() is not None:
                print(f"  ERROR: SSH exited (subdomain '{name}' may be taken, or serveo.net is down).")
                sys.exit(1)
            continue
        if "Forwarding" in line or "serveo.net" in line:
            confirmed = True
            break

    if not confirmed:
        proc.terminate()
        print("  ERROR: serveo.net tunnel timed out after 30s.")
        sys.exit(1)

    return proc, f"https://{name}.serveo.net"


class KindleHandler(SimpleHTTPRequestHandler):
    """Serves a Kindle-friendly book listing page."""

    books_dir: Path = Path(".")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)

        if path == "/" or path == "":
            self._serve_listing()
        elif path.startswith("/download/"):
            filename = path[len("/download/"):]
            self._serve_file(filename)
        else:
            self.send_error(404, "Not found")

    def _serve_listing(self):
        books = []
        for f in sorted(self.books_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in EBOOK_EXTENSIONS:
                stat = f.stat()
                books.append({
                    "name": f.name,
                    "size": format_size(stat.st_size),
                    "modified": datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d"),
                    "url": f"/download/{urllib.parse.quote(f.name)}",
                })

        rows = ""
        if books:
            for b in books:
                name_escaped = html.escape(b["name"])
                rows += f"""
                <tr>
                    <td style="padding:8px;border-bottom:1px solid #ccc;">
                        <a href="{b['url']}" style="color:#000;font-size:18px;">{name_escaped}</a>
                    </td>
                    <td style="padding:8px;border-bottom:1px solid #ccc;text-align:right;font-size:14px;color:#666;">
                        {b['size']}
                    </td>
                    <td style="padding:8px;border-bottom:1px solid #ccc;text-align:right;font-size:14px;color:#666;">
                        {b['modified']}
                    </td>
                </tr>"""
        else:
            rows = """
                <tr>
                    <td colspan="3" style="padding:20px;text-align:center;color:#666;">
                        No ebook files found. Drop .mobi files into the books folder and refresh.
                    </td>
                </tr>"""

        page = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=600">
    <title>Kindle Library</title>
</head>
<body style="margin:10px;font-family:serif;background:#fff;color:#000;">
    <h2 style="margin:10px 0;">Books ({len(books)})</h2>
    <table style="width:100%;border-collapse:collapse;">
        <tr style="border-bottom:2px solid #000;">
            <th style="padding:8px;text-align:left;">Title</th>
            <th style="padding:8px;text-align:right;">Size</th>
            <th style="padding:8px;text-align:right;">Date</th>
        </tr>
        {rows}
    </table>
    <p style="margin-top:20px;font-size:12px;color:#999;text-align:center;">
        Tap a book title to download. Refresh to see new books.
    </p>
</body>
</html>"""

        data = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_file(self, filename: str):
        filepath = self.books_dir / filename

        if not filepath.exists() or not filepath.is_file():
            self.send_error(404, "File not found")
            return

        try:
            filepath = filepath.resolve()
            if not str(filepath).startswith(str(self.books_dir.resolve())):
                self.send_error(403, "Forbidden")
                return
        except Exception:
            self.send_error(400, "Bad request")
            return

        size = filepath.stat().st_size
        encoded_name = urllib.parse.quote(filepath.name)

        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", f'attachment; filename="{encoded_name}"')
        self.end_headers()

        with open(filepath, "rb") as f:
            while chunk := f.read(65536):
                self.wfile.write(chunk)

    def log_message(self, format, *args):
        print(f"  {self.address_string()} — {args[0]}")


def main():
    parser = argparse.ArgumentParser(description="Book server for Kindle.")
    parser.add_argument("books_dir", nargs="?", type=Path, default=Path("."),
                        help="Directory containing ebook files (default: current dir)")
    parser.add_argument("-p", "--port", type=int, default=8080, help="Port (default: 8080)")
    parser.add_argument("-t", "--tunnel", action="store_true",
                        help="Create a Cloudflare tunnel (random URL, changes each restart)")
    parser.add_argument("-n", "--name", type=str, default=None,
                        help="Stable subdomain via serveo.net (e.g. --name mykindle → https://mykindle.serveo.net)")
    parser.add_argument("--cf-setup", action="store_true",
                        help="One-time setup: deploy a Cloudflare Worker for a permanent Kindle bookmark URL")
    args = parser.parse_args()

    if args.cf_setup:
        cf_setup()
        return

    books_dir = args.books_dir.resolve()
    if not books_dir.is_dir():
        print(f"ERROR: {books_dir} is not a directory.")
        raise SystemExit(1)

    KindleHandler.books_dir = books_dir

    ebook_count = sum(1 for f in books_dir.iterdir()
                      if f.is_file() and f.suffix.lower() in EBOOK_EXTENSIONS)

    local_ip = get_local_ip()
    server = HTTPServer(("0.0.0.0", args.port), KindleHandler)

    tunnel_proc = None

    if args.name:
        print(f"\n  Starting stable tunnel (serveo.net)...")
        tunnel_proc, tunnel_url = start_serveo_tunnel(args.port, args.name)
        drain_stream = tunnel_proc.stdout
    elif args.tunnel:
        print(f"\n  Starting tunnel (cloudflare)...")
        tunnel_proc, tunnel_url = start_tunnel(args.port)
        drain_stream = tunnel_proc.stderr
    else:
        drain_stream = None

    if tunnel_proc:
        # Auto-update Cloudflare Worker KV redirect if configured
        config = _load_config()
        cf_account = config.get("cf_account_id")
        cf_token = config.get("cf_api_token")
        cf_ns = config.get("cf_kv_namespace_id")
        worker_url = None
        if cf_account and cf_token and cf_ns:
            try:
                update_worker_redirect(cf_account, cf_token, cf_ns, tunnel_url)
                worker_url = True  # URL is whatever user bookmarked — we just update KV
            except Exception as e:
                print(f"  Warning: could not update Worker redirect: {e}")

        print()
        print(f"  Kindle Book Server")
        print(f"  ──────────────────────────────────────")
        print(f"  Books  : {books_dir}  ({ebook_count} file(s))")
        print()
        if worker_url:
            print(f"  Kindle URL : your bookmarked workers.dev URL  ← already updated")
            print(f"  Tunnel URL : {tunnel_url}")
        else:
            print(f"  Kindle URL : {tunnel_url}")
            print(f"  Tip: run --cf-setup once for a permanent bookmark URL")
        print(f"  Local URL  : http://{local_ip}:{args.port}")
        print(f"  ──────────────────────────────────────")
        print(f"  Press Ctrl+C to stop.")
        print()

        # Drain tunnel output in background so the subprocess doesn't block
        def _drain():
            try:
                while drain_stream.readline():
                    pass
            except Exception:
                pass
        threading.Thread(target=_drain, daemon=True).start()

    else:
        print()
        print(f"  Kindle Book Server")
        print(f"  ──────────────────────────────────────")
        print(f"  Books folder : {books_dir}")
        print(f"  Local URL    : http://{local_ip}:{args.port}")
        print(f"  Found        : {ebook_count} ebook(s)")
        print(f"  ──────────────────────────────────────")
        print(f"  Tip: run --cf-setup once for a permanent Kindle bookmark URL")
        print(f"  Press Ctrl+C to stop.\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopping...")
        server.server_close()
        if tunnel_proc:
            tunnel_proc.terminate()
            tunnel_proc.wait(timeout=5)
        print("  Done.")


if __name__ == "__main__":
    main()
