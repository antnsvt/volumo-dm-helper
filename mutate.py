#!/usr/bin/env python3
"""Tiny CLI used by .github/workflows/mutate.yml to apply a single CSV edit
from a repository_dispatch event.

Reads:
  --event {save-handle|mark-sent}
  --payload  JSON string with the event's client_payload
  $VOLUMO_DATA_DIR  path to the data/ folder on the state branch checkout

Calls into _volumo_core so the schema stays in one place.
"""
import argparse
import json
import os
import sys
from pathlib import Path

from _volumo_core import ensure_files, mark_sent, save_handle


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", required=True, choices=["save-handle", "mark-sent"])
    ap.add_argument("--payload", required=True, help="JSON client_payload")
    args = ap.parse_args()

    data_dir = Path(os.environ.get("VOLUMO_DATA_DIR") or "data").resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    ensure_files(data_dir)

    try:
        payload = json.loads(args.payload or "{}")
    except json.JSONDecodeError as e:
        print(f"bad payload JSON: {e}", file=sys.stderr)
        sys.exit(2)

    if args.event == "save-handle":
        name = (payload.get("name") or "").strip()
        handle = (payload.get("handle") or "").strip().lstrip("@")
        if not name or not handle:
            print("save-handle: name and handle required", file=sys.stderr)
            sys.exit(2)
        # Manual saves from the UI are user assertions; always 'verified'.
        save_handle(data_dir, name, handle, "verified")
        print(f"save-handle: {name} -> @{handle}")

    elif args.event == "mark-sent":
        name = (payload.get("name") or "").strip()
        sent = bool(payload.get("sent", True))
        if not name:
            print("mark-sent: name required", file=sys.stderr)
            sys.exit(2)
        mark_sent(data_dir, name, sent)
        print(f"mark-sent: {name} sent={sent}")


if __name__ == "__main__":
    main()
