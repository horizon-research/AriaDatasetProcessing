#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
download_vrs.py
---------------
Given a (possibly very large) nested JSON file describing sequences with file entries,
download ONLY entries whose "filename" ends with .vrs (case-insensitive).

Features
- Recursive traversal; no hardcoding of keys
- Regex-based filename filter for .vrs (case-insensitive)
- Streaming download with resume support (HTTP Range)
- Optional SHA1 and file-size verification when provided
- Sensible defaults and CLI flags

Usage
-----
1) Using default JSON file (AriaEverydayActivities_download_urls.json):
   python download_vrs.py --outdir ./aria_downloads

2) From a specific JSON file:
   python download_vrs.py input.json --outdir ./aria_downloads

3) Extra flags:
   --workers 4              # concurrent downloads (default: 2)
   --skip-existing          # skip files that are already fully downloaded & verified
   --no-verify              # do not verify sha1 or file size
   --timeout 30             # per-request timeout seconds (default: 30)
   --max-files 10           # download first N files (-1 for all, default: -1)
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys
import threading
import time
from queue import Queue
from urllib.parse import urlparse
from urllib.request import Request, urlopen

VRS_REGEX = re.compile(r"\.vrs$", re.IGNORECASE)
CHUNK_SIZE = 1024 * 1024  # 1 MiB

def sha1_of_file(path):
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def human_bytes(n):
    units = ['B','KB','MB','GB','TB']
    i = 0
    f = float(n)
    while f >= 1024 and i < len(units) - 1:
        f /= 1024.0
        i += 1
    return f"{f:.2f} {units[i]}"

def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', '_', name)

def recursive_find_entries(obj):
    """Yield dicts that look like file entries with at least a filename and download_url."""
    if isinstance(obj, dict):
        # A 'file entry' pattern
        if "filename" in obj and "download_url" in obj:
            yield obj
        for v in obj.values():
            yield from recursive_find_entries(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from recursive_find_entries(item)

def head_request_length(url, timeout=30):
    """Try to fetch content length via HEAD (best-effort)."""
    try:
        req = Request(url, method='HEAD')
        with urlopen(req, timeout=timeout) as resp:
            length = resp.headers.get('Content-Length')
            if length is not None and length.isdigit():
                return int(length)
    except Exception:
        pass
    return None

def ranged_download(url, dest_path, expected_size=None, timeout=30):
    """Download with resume support using HTTP Range. Returns True on success."""
    tmp_path = dest_path + ".part"
    existing = 0
    if os.path.exists(tmp_path):
        existing = os.path.getsize(tmp_path)

    headers = {}
    if existing > 0:
        headers['Range'] = f'bytes={existing}-'

    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=timeout) as resp:
            # If server ignored Range, and we had partial data, start over
            if existing > 0 and resp.getcode() not in (206, 200):
                existing = 0  # restart
            mode = 'ab' if existing > 0 else 'wb'
            total_downloaded = existing
            # Try to detect total size if not given
            if expected_size is None:
                # Try to infer from response
                cl = resp.headers.get('Content-Length')
                if cl and cl.isdigit():
                    # If 206, Content-Length is the remaining bytes, so total = existing + remaining
                    expected_size = existing + int(cl)

            start = time.time()
            with open(tmp_path, mode) as f:
                while True:
                    chunk = resp.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    f.write(chunk)
                    total_downloaded += len(chunk)
                    # simple single-line progress
                    if expected_size:
                        pct = (total_downloaded / expected_size) * 100
                        sys.stdout.write(f"\r  → {os.path.basename(dest_path)}  {human_bytes(total_downloaded)}/{human_bytes(expected_size)} ({pct:.1f}%)")
                    else:
                        sys.stdout.write(f"\r  → {os.path.basename(dest_path)}  {human_bytes(total_downloaded)}")
                    sys.stdout.flush()
            sys.stdout.write("\n")
            sys.stdout.flush()

        # Move to final
        os.replace(tmp_path, dest_path)
        return True
    except Exception as e:
        sys.stderr.write(f"[ERROR] Download failed for {url}: {e}\n")
        return False

def download_one(entry, outdir, verify=True, timeout=30):
    filename = sanitize_filename(entry.get("filename", "unknown.vrs"))
    url = entry.get("download_url")
    if not url:
        print(f"[SKIP] No download_url for {filename}")
        return

    # Determine expected size if available
    expected_size = entry.get("file_size_bytes")
    if not isinstance(expected_size, int):
        expected_size = None
    if expected_size is None:
        # Try HEAD
        expected_size = head_request_length(url, timeout=timeout)

    dest_path = os.path.join(outdir, filename)
    os.makedirs(outdir, exist_ok=True)

    if os.path.exists(dest_path) and verify:
        # If file exists, verify and skip if matches
        size_ok = True
        if expected_size is not None:
            size_ok = os.path.getsize(dest_path) == expected_size
        sha_ok = True
        if "sha1sum" in entry and entry["sha1sum"]:
            try:
                sha_ok = sha1_of_file(dest_path).lower() == entry["sha1sum"].lower()
            except Exception:
                sha_ok = False
        if size_ok and sha_ok:
            print(f"[OK] {filename} already exists and verified.")
            return

    print(f"[INFO] Downloading {filename} → {outdir}")
    ok = ranged_download(url, dest_path, expected_size=expected_size, timeout=timeout)
    if not ok:
        print(f"[FAIL] {filename}")
        return

    if verify:
        # Verify size
        if expected_size is not None and os.path.getsize(dest_path) != expected_size:
            print(f"[WARN] Size mismatch for {filename}: got {os.path.getsize(dest_path)}, expect {expected_size}")
        # Verify sha1
        sha = entry.get("sha1sum")
        if sha:
            got = sha1_of_file(dest_path)
            if got.lower() != sha.lower():
                print(f"[WARN] SHA1 mismatch for {filename}: {got} != {sha}")
            else:
                print(f"[OK] SHA1 verified for {filename}")

def worker(q, outdir, verify, timeout):
    while True:
        item = q.get()
        if item is None:
            break
        try:
            download_one(item, outdir, verify=verify, timeout=timeout)
        finally:
            q.task_done()

def main():
    ap = argparse.ArgumentParser(description="Download only .vrs files from nested JSON descriptors.")
    ap.add_argument("--json_path", default="./AriaEverydayActivities_download_urls.json", help="Path to JSON file. If omitted, uses default AriaEverydayActivities_download_urls.json.")
    ap.add_argument("--outdir", default="./downloads", help="Output directory for .vrs files.")
    ap.add_argument("--workers", type=int, default=2, help="Number of concurrent downloads.")
    ap.add_argument("--skip-existing", action="store_true", help="Skip existing files without verifying.")
    ap.add_argument("--no-verify", action="store_true", help="Disable SHA1/size verification.")
    ap.add_argument("--timeout", type=int, default=30, help="Per-request timeout in seconds.")
    ap.add_argument("--max-files", type=int, default=-1, help="Download first N files (-1 for all).")
    args = ap.parse_args()

    # Load JSON
    if args.json_path:
        with open(args.json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        raise ValueError("JSON path is required.")

    # Find candidate entries
    entries = []
    for entry in recursive_find_entries(data):
        fname = entry.get("filename", "")
        if VRS_REGEX.search(fname):
            entries.append(entry)

    if not entries:
        print("[INFO] No .vrs files found in the provided JSON.")
        return

    # Limit number of files if specified
    if args.max_files > 0:
        total_found = len(entries)
        entries = entries[:args.max_files]
        print(f"[INFO] Found {total_found} .vrs files, downloading first {len(entries)} files.")
    else:
        print(f"[INFO] Found {len(entries)} .vrs files, downloading all.")

    verify = not args.no_verify and (not args.skip_existing)

    # Queue + threads
    q = Queue()
    threads = []
    for _ in range(max(1, args.workers)):
        t = threading.Thread(target=worker, args=(q, args.outdir, verify, args.timeout), daemon=True)
        t.start()
        threads.append(t)

    for e in entries:
        q.put(e)

    q.join()
    for _ in threads:
        q.put(None)
    for t in threads:
        t.join()

    print("[DONE] All applicable .vrs files processed.")

if __name__ == "__main__":
    main()
