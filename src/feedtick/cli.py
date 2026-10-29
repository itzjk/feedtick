import argparse
import math
import os
import sqlite3
import sys
from pathlib import Path

from . import __version__
from .core import ack, check, config
from .feeds import visible
from .output import render


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def default_state():
    root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return os.environ.get("FEEDTICK_STATE", str(root / "feedtick" / "state.sqlite3"))


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0 or number > 168:
        raise argparse.ArgumentTypeError("must be greater than 0 and at most 168")
    return number


def main(argv=None):
    parser = ArgumentParser(description="New RSS/Atom headlines since your last confirmed check.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    checking = sub.add_parser("check", help="fetch new headlines or replay the saved pending batch")
    checking.add_argument("config", type=Path)
    checking.add_argument("--state", default=default_state(), help="SQLite state file")
    formats = checking.add_mutually_exclusive_group()
    formats.add_argument("--json", action="store_true", help="structured output")
    formats.add_argument("--markdown", action="store_true", help="Markdown output")
    checking.add_argument("--timeout", type=positive, default=10, help="seconds per download attempt (default: 10)")
    checking.add_argument("--retries", type=int, choices=range(3), default=1, help="extra attempts, 0 to 2 (default: 1)")
    checking.add_argument("--recent-hours", type=positive, default=24, help="recent publication window (default: 24)")
    acknowledging = sub.add_parser("ack", help="confirm only the entries in a saved batch")
    acknowledging.add_argument("batch_id")
    acknowledging.add_argument("--state", default=default_state(), help="SQLite state file")
    demonstration = sub.add_parser("demo", help="run a synthetic local HTTP demonstration in temporary state")
    demonstration.add_argument("--pause", type=positive, default=0, help="seconds between demo stages")
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            from .demo import run
            run(args.pause)
            return 0
        if args.command == "ack":
            changed = ack(args.state, args.batch_id)
            print(f"{'Confirmed' if changed else 'Already confirmed'} batch {visible(args.batch_id)}.")
            return 0
        payload = check(args.state, config(args.config), timeout=args.timeout, retries=args.retries, recent_hours=args.recent_hours)
        print(render(payload, "json" if args.json else "markdown" if args.markdown else "text", state=args.state))
        return 2 if payload["coverage_status"] == "partial" else 0
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"feedtick: {visible(exc)}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("feedtick: interrupted; retry check to recover the saved batch", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
