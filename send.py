#!/usr/bin/env python3
"""Send files to Kindle via email (Gmail SMTP)."""

import argparse
import glob as _glob
import json
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.json"
KINDLE_MAX_ATTACHMENT_MB = 25


def load_config() -> dict:
    """Load config from config.json."""
    if not CONFIG_PATH.exists():
        print("No config found. Run: python send.py --setup")
        sys.exit(1)

    with open(CONFIG_PATH) as f:
        config = json.load(f)

    required = ["gmail_address", "gmail_app_password", "kindle_email"]
    missing = [k for k in required if not config.get(k)]
    if missing:
        print(f"Config missing fields: {', '.join(missing)}")
        print("Run: python send.py --setup")
        sys.exit(1)

    return config


def run_setup():
    """Interactive first-time setup."""
    print("=== Kindle Send Setup ===\n")

    # Merge with existing config so we don't wipe Cloudflare or other keys
    existing = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            existing = json.load(f)

    def prompt(label, key, secret=False):
        default = existing.get(key, "")
        hint = f" [{default}]" if default and not secret else ""
        hint = " [****]" if default and secret else hint
        value = input(f"  {label}{hint}: ").strip()
        return value if value else default

    existing["gmail_address"] = prompt("Gmail address", "gmail_address")
    existing["gmail_app_password"] = prompt("Gmail app password", "gmail_app_password", secret=True)
    existing["kindle_email"] = prompt("Kindle email (xxx@kindle.com)", "kindle_email")

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(existing, f, indent=2)

    # Restrict permissions (owner read/write only)
    try:
        CONFIG_PATH.chmod(0o600)
    except Exception:
        pass

    print(f"\nConfig saved to {CONFIG_PATH}")
    print("\nGmail app password setup:")
    print("  1. Go to https://myaccount.google.com/apppasswords")
    print("  2. Create an app password for 'Mail'")
    print("  3. Use the 16-character password (spaces ok)\n")
    print("Kindle email setup:")
    print("  1. Go to https://www.amazon.com/myk → Preferences → Personal Document Settings")
    print("  2. Find your Send-to-Kindle email address")
    print("  3. Add your Gmail to the 'Approved Personal Document E-mail List'\n")


def get_file_size_mb(path: Path) -> float:
    return path.stat().st_size / (1024 * 1024)


def send_batch(files: list[Path], config: dict, dry_run: bool = False) -> tuple[int, int]:
    """Send files in batches respecting Kindle's size limit. Returns (sent, failed)."""

    # Group files into batches under 25MB
    batches: list[list[Path]] = []
    current_batch: list[Path] = []
    current_size = 0.0

    for f in files:
        size = get_file_size_mb(f)
        if size > KINDLE_MAX_ATTACHMENT_MB:
            print(f"  ✗ {f.name}: too large ({size:.1f}MB > {KINDLE_MAX_ATTACHMENT_MB}MB limit)")
            continue

        if current_size + size > KINDLE_MAX_ATTACHMENT_MB and current_batch:
            batches.append(current_batch)
            current_batch = []
            current_size = 0.0

        current_batch.append(f)
        current_size += size

    if current_batch:
        batches.append(current_batch)

    if not batches:
        print("No files to send.")
        return 0, len(files)

    total_files = sum(len(b) for b in batches)
    print(f"Sending {total_files} file{'s' if total_files != 1 else ''} in {len(batches)} email{'s' if len(batches) != 1 else ''}...\n")

    if dry_run:
        for i, batch in enumerate(batches, 1):
            names = ", ".join(f.name for f in batch)
            size = sum(get_file_size_mb(f) for f in batch)
            print(f"  [Email {i}] {names} ({size:.1f}MB) → {config['kindle_email']}")
        print("\n(Dry run — no emails sent)")
        return total_files, 0

    sent = 0
    failed = 0

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(config["gmail_address"], config["gmail_app_password"])

            for i, batch in enumerate(batches, 1):
                try:
                    msg = EmailMessage()
                    msg["From"] = config["gmail_address"]
                    msg["To"] = config["kindle_email"]
                    msg["Subject"] = "convert" if any(f.suffix.lower() != ".mobi" for f in batch) else ""

                    for f in batch:
                        data = f.read_bytes()
                        msg.add_attachment(
                            data,
                            maintype="application",
                            subtype="octet-stream",
                            filename=f.name,
                        )

                    server.send_message(msg)
                    names = ", ".join(f.name for f in batch)
                    size = sum(get_file_size_mb(f) for f in batch)
                    print(f"  ✓ [Email {i}/{len(batches)}] Sent: {names} ({size:.1f}MB)")
                    sent += len(batch)

                except Exception as e:
                    names = ", ".join(f.name for f in batch)
                    print(f"  ✗ [Email {i}/{len(batches)}] Failed: {names} — {e}")
                    failed += len(batch)

    except smtplib.SMTPAuthenticationError:
        print("ERROR: Gmail authentication failed.")
        print("Check your app password. Run: python send.py --setup")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Could not connect to Gmail: {e}")
        sys.exit(1)

    return sent, failed


def main():
    parser = argparse.ArgumentParser(description="Send files to Kindle via email.")
    parser.add_argument("files", nargs="*", type=Path, help="Files to send")
    parser.add_argument("--setup", action="store_true", help="Configure Gmail and Kindle email")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be sent without sending")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    if args.setup:
        run_setup()
        return

    if not args.files:
        parser.print_help()
        sys.exit(1)

    # Expand glob patterns (PowerShell doesn't expand them) and validate
    valid_files = []
    for p in args.files:
        s = str(p)
        if any(c in s for c in ('*', '?', '[')):
            matches = sorted(Path(x) for x in _glob.glob(s))
            if not matches:
                print(f"  ✗ No files matched: {s}")
            else:
                valid_files.extend(matches)
        elif not p.exists():
            print(f"  ✗ File not found: {p}")
        elif not p.is_file():
            print(f"  ✗ Not a file: {p}")
        else:
            valid_files.append(p)

    if not valid_files:
        print("No valid files to send.")
        sys.exit(1)

    config = load_config()
    sent, failed = send_batch(valid_files, config, dry_run=args.dry_run)

    print(f"\nDone: {sent} sent, {failed} failed.")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
