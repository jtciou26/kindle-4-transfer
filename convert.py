#!/usr/bin/env python3
"""Convert EPUB files to MOBI using Calibre's ebook-convert."""

import argparse
import glob as _glob
import subprocess
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed


def find_ebook_convert():
    """Locate the ebook-convert binary."""
    common_paths = [
        "ebook-convert",  # system PATH
        "/usr/bin/ebook-convert",
        "/usr/local/bin/ebook-convert",
        "/opt/calibre/ebook-convert",
        # macOS Calibre.app
        "/Applications/calibre.app/Contents/MacOS/ebook-convert",
    ]
    for path in common_paths:
        try:
            subprocess.run(
                [path, "--version"],
                capture_output=True,
                timeout=10,
            )
            return path
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


def convert_one(epub_path: Path, output_dir: Path | None, ebook_convert: str) -> tuple[Path, bool, str]:
    """Convert a single EPUB to MOBI. Returns (output_path, success, message)."""
    if not epub_path.exists():
        return epub_path, False, f"File not found: {epub_path}"

    if epub_path.suffix.lower() != ".epub":
        return epub_path, False, f"Not an EPUB file: {epub_path}"

    if output_dir:
        mobi_path = output_dir / epub_path.with_suffix(".mobi").name
    else:
        mobi_path = epub_path.with_suffix(".mobi")

    try:
        result = subprocess.run(
            [ebook_convert, str(epub_path), str(mobi_path)],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=300,
        )
        if result.returncode == 0:
            return mobi_path, True, f"OK → {mobi_path}"
        else:
            error = result.stderr.strip().split("\n")[-1] if result.stderr else "Unknown error"
            return mobi_path, False, f"Failed: {error}"
    except subprocess.TimeoutExpired:
        return mobi_path, False, "Timed out (>5 min)"
    except Exception as e:
        return mobi_path, False, f"Error: {e}"


def main():
    parser = argparse.ArgumentParser(description="Convert EPUB files to MOBI.")
    parser.add_argument("files", nargs="*", type=Path, help="EPUB files or directories to convert")
    parser.add_argument("-o", "--output-dir", type=Path, default=None, help="Output directory (default: same as input)")
    parser.add_argument("-j", "--jobs", type=int, default=4, help="Parallel conversions (default: 4)")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    # Find ebook-convert
    ebook_convert = find_ebook_convert()
    if not ebook_convert:
        print("ERROR: Calibre's ebook-convert not found.")
        print("Install Calibre: https://calibre-ebook.com/download")
        sys.exit(1)

    if not args.files:
        print("Usage: convert.py <file.epub|directory> ... [-o OUTPUT_DIR]")
        print("Example: convert.py books -o books")
        sys.exit(1)

    # Expand directories, glob patterns (Windows doesn't expand in shell), and plain files
    files = []
    for p in args.files:
        s = str(p)
        if any(c in s for c in ('*', '?', '[')):
            expanded = sorted(Path(x) for x in _glob.glob(s))
            if not expanded:
                print(f"No files matched: {s}")
                sys.exit(1)
            files.extend(expanded)
        elif p.is_dir():
            found = sorted(p.glob("*.epub"))
            if not found:
                print(f"No EPUB files found in {p}")
                sys.exit(1)
            files.extend(found)
        else:
            files.append(p)

    # Prepare output dir
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
    total = len(files)
    success_count = 0

    print(f"Converting {total} file{'s' if total != 1 else ''}...\n")

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {
            pool.submit(convert_one, f, args.output_dir, ebook_convert): f
            for f in files
        }
        for i, future in enumerate(as_completed(futures), 1):
            mobi_path, success, message = future.result()
            status = "✓" if success else "✗"
            print(f"  [{i}/{total}] {status} {futures[future].name}: {message}")
            if success:
                success_count += 1

    print(f"\nDone: {success_count}/{total} converted.")

    if success_count < total:
        sys.exit(1)


if __name__ == "__main__":
    main()
