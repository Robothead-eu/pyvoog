"""
output.py — Printing helpers for voog-cli.
Provides an Output instance that respects --verbose and formats
progress lines consistently.
"""

import sys


class Output:
    def __init__(self, verbose=False):
        self.verbose = verbose

    # ------------------------------------------------------------------
    # Always-visible output
    # ------------------------------------------------------------------

    def info(self, msg=""):
        print(msg)

    def success(self, msg):
        print(f"  OK  {msg}")

    def fail(self, msg, detail=""):
        line = f"  FAIL {msg}"
        if detail:
            line += f"  ({detail})"
        print(line)

    def warn(self, msg):
        print(f"  WARN {msg}", file=sys.stderr)

    def attention(self, title, lines=None):
        """Print a prominent, multi-line warning block to stderr.

        Used for things the user must not miss — e.g. a push conflict where
        the server changed since the last pull. A single WARN line is too
        easy to scroll past.
        """
        bar = "!" * 64
        print(f"\n{bar}", file=sys.stderr)
        print(f"  {title}", file=sys.stderr)
        for ln in (lines or []):
            print(f"  {ln}", file=sys.stderr)
        print(f"{bar}\n", file=sys.stderr)

    def error(self, msg):
        print(f"ERROR: {msg}", file=sys.stderr)

    def section(self, msg):
        """Print a section header."""
        print(f"\n{msg}")
        print("-" * len(msg))

    def summary(self, succeeded, failed, dry_run=False):
        prefix = "[dry-run] " if dry_run else ""
        parts = [f"{prefix}{len(succeeded)} written"]
        if failed:
            parts.append(f"{len(failed)} failed")
        print(f"\nDone: {', '.join(parts)}.")

    # ------------------------------------------------------------------
    # Verbose-only output
    # ------------------------------------------------------------------

    def log(self, msg):
        if self.verbose:
            print(f"  {msg}")

    def step(self, n, total, label, filepath):
        if self.verbose:
            print(f"  [{n}/{total}] {label} {filepath}", end=" ... ", flush=True)

    def step_result(self, ok, detail=""):
        if self.verbose:
            if ok:
                print("ok")
            else:
                if detail:
                    print(f"FAIL ({detail})")
                else:
                    print("FAIL")

    # ------------------------------------------------------------------
    # Progress bar (always visible, overwrites current line)
    # ------------------------------------------------------------------

    def progress(self, current, total, label="", bar_width=32):
        """Print an overwriting progress bar on the current line."""
        if total == 0:
            return
        filled = int(bar_width * current / total)
        bar = "\u2588" * filled + "\u2591" * (bar_width - filled)
        # Truncate label so the whole line stays under ~80 chars
        max_label = 45
        if len(label) > max_label:
            label = "\u2026" + label[-(max_label - 1):]
        print(f"\r[{bar}] {current}/{total}  {label:<{max_label}}", end="", flush=True)

    def progress_done(self):
        """Move to a new line after the progress bar is complete."""
        print()
