"""Sync local agentplay sessions to a Hackathon Control hub.

Same job as the /join.ps1 and /join.sh one-liners (it runs the very same sync loop), for
people who prefer Python. Only sessions run in --folder (default: the current folder) and
active in the last hour are uploaded; nothing else on the laptop is sent.
Get your upload token from the hub's /join page (it identifies you; name and team
come from your registration).

Examples:
    python sync_to_hub.py --hub https://hub.example.com --token <TOKEN> --folder ~/my-project
    HUB_TOKEN=<TOKEN> python sync_to_hub.py --hub https://hub.example.com
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hub.join_scripts import SYNC_INTERVAL_S, SYNC_PY  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Upload this folder's recent agent sessions to Hackathon Control.")
    ap.add_argument("--hub", default="http://127.0.0.1:7400", help="Hackathon Control URL")
    ap.add_argument("--token", default=os.environ.get("HUB_TOKEN", ""), help="Upload token from /join (or HUB_TOKEN)")
    ap.add_argument("--folder", default=os.getcwd(), help="Project folder whose sessions count (default: current folder)")
    ap.add_argument("--since", default="", help="Event start (ISO time); sessions started before it don't count")
    ap.add_argument("--interval", type=int, default=SYNC_INTERVAL_S, help="Seconds between syncs")
    args = ap.parse_args()
    if not args.token:
        ap.error("--token is required (get it from the hub's /join page)")

    os.environ.update({
        "HC_HUB": args.hub, "HC_TOKEN": args.token, "HC_FOLDER": args.folder,
        "HC_SINCE": args.since, "HC_INTERVAL": str(args.interval),
    })
    exec(compile(SYNC_PY, "hackathon-control-sync", "exec"), {"__name__": "__main__"})


if __name__ == "__main__":
    main()
