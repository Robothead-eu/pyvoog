"""
experimental_cmd.py — Environment comparison and copy commands.

WARNING: env-copy overwrites files in the target directory without undo.
         Always review the diff before confirming a copy.

These commands compare two local site directories using their manifest.json
as the file list. Only manifest-tracked files (layouts, components, assets)
are considered — unrelated files (docs, test files, etc.) are ignored.
"""

import filecmp
import os
import shutil

from . import manifest as mf
from .config import ConfigError


def resolve_env_dir(env_arg, config, site_dir):
    """
    Return the local directory for a named environment.

    Matches env_arg against config.env_name (→ site_dir, the current directory)
    or config.env_peer_name (→ config.env_peer_path).

    Raises ConfigError with a helpful message if unresolvable.
    """
    if config.env_name and env_arg == config.env_name:
        return site_dir

    if config.env_peer_name and env_arg == config.env_peer_name:
        if config.env_peer_path:
            return os.path.abspath(config.env_peer_path)
        raise ConfigError(
            f"Environment '{env_arg}' is configured as the peer but env_peer_path is not set.\n"
            "Run  pyvoog experimental env-setup  to set the path."
        )

    configured = []
    if config.env_name:
        configured.append(f"env_name={config.env_name!r}")
    if config.env_peer_name:
        configured.append(f"env_peer_name={config.env_peer_name!r}")

    hint = (
        f"Configured: {', '.join(configured)}"
        if configured else
        "No environments configured yet."
    )
    raise ConfigError(
        f"Environment '{env_arg}' not found in .voog.\n"
        f"{hint}\n"
        "Run  pyvoog experimental env-setup  to configure environments."
    )


def _diff_envs(src_dir, dst_dir):
    """
    Compare manifest-tracked files between two local directories.

    Returns a dict:
        different — files present in both but with differing content
        only_src  — files in src manifest absent or missing locally in dst
        only_dst  — files in dst manifest not tracked in src
        same      — files with identical content
        errors    — list of (side, path, message) for unreadable files
    """
    src_manifest = mf.load(src_dir)
    dst_manifest = mf.load(dst_dir)

    src_files = mf.lookup_by_file(src_manifest) if src_manifest else {}
    dst_files = mf.lookup_by_file(dst_manifest) if dst_manifest else {}

    all_src = set(src_files)
    all_dst = set(dst_files)

    different = []
    only_src = []
    only_dst = sorted(all_dst - all_src)
    same = []
    errors = []

    for rel in sorted(all_src):
        src_abs = os.path.join(src_dir, rel)
        dst_abs = os.path.join(dst_dir, rel)

        if not os.path.isfile(src_abs):
            errors.append(("src", rel, "file missing locally"))
            continue

        if rel not in all_dst or not os.path.isfile(dst_abs):
            only_src.append(rel)
        else:
            try:
                if filecmp.cmp(src_abs, dst_abs, shallow=False):
                    same.append(rel)
                else:
                    different.append(rel)
            except OSError as exc:
                errors.append(("compare", rel, str(exc)))

    return {
        "different": different,
        "only_src": only_src,
        "only_dst": only_dst,
        "same": same,
        "errors": errors,
    }


def _display_diff(diff, src_name, dst_name, src_dir, dst_dir, out, verbose=False):
    """Print a formatted diff report."""
    out.info(f"\nSource : {src_name}  ({src_dir})")
    out.info(f"Target : {dst_name}  ({dst_dir})")
    out.info("")

    if diff["different"]:
        out.info(f"  Modified ({len(diff['different'])} file(s)):")
        for f in diff["different"]:
            out.info(f"    ~  {f}")

    if diff["only_src"]:
        out.info(f"\n  Only in source [{src_name}] ({len(diff['only_src'])} file(s)):")
        for f in diff["only_src"]:
            out.info(f"    +  {f}")

    if diff["only_dst"]:
        out.info(f"\n  Only in target [{dst_name}] ({len(diff['only_dst'])} file(s)):")
        for f in diff["only_dst"]:
            out.info(f"    -  {f}")

    total_same = len(diff["same"])
    if total_same:
        if verbose:
            out.info(f"\n  Identical ({total_same} file(s)):")
            for f in sorted(diff["same"]):
                out.info(f"    =  {f}")
        else:
            out.info(f"\n  Identical: {total_same} file(s)  (--verbose to list)")

    if not diff["different"] and not diff["only_src"] and not diff["only_dst"]:
        out.info("  Environments are in sync.")

    if diff["errors"]:
        out.info(f"\n  Warnings ({len(diff['errors'])}):")
        for side, path, msg in diff["errors"]:
            out.warn(f"    [{side}] {path}: {msg}")


def env_diff(src_dir, dst_dir, src_name, dst_name, out, verbose=False):
    """Run env-diff: compare two environments and print the result."""
    if not mf.load(src_dir):
        out.error(f"No manifest.json found in source directory: {src_dir}")
        out.info("Run  pyvoog pull  from the source environment to generate one.")
        return 1

    out.info("Comparing manifest-tracked files...")
    diff = _diff_envs(src_dir, dst_dir)
    _display_diff(diff, src_name, dst_name, src_dir, dst_dir, out, verbose=verbose)

    has_diff = bool(diff["different"] or diff["only_src"] or diff["only_dst"])
    return 1 if has_diff else 0


def _copy_files(files, src_dir, dst_dir, out):
    """Copy a list of relative paths from src_dir to dst_dir. Returns (succeeded, failed)."""
    succeeded = []
    failed = []
    for rel in files:
        src_abs = os.path.join(src_dir, rel)
        dst_abs = os.path.join(dst_dir, rel)
        try:
            os.makedirs(os.path.dirname(dst_abs), exist_ok=True)
            shutil.copy2(src_abs, dst_abs)
            out.success(rel)
            succeeded.append(rel)
        except OSError as exc:
            out.fail(rel, str(exc))
            failed.append(rel)
    return succeeded, failed


def _select_files(to_copy, out):
    """
    Prompt the user to accept or skip each file individually.

    Returns the list of files the user chose to copy, or None on abort (q).
    """
    chosen = []
    out.info("")
    for rel in to_copy:
        try:
            answer = input(f"  {rel:<55} [y/n/q]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            out.info("\nAborted.")
            return None
        if answer == "q":
            out.info("  (stopped)")
            break
        if answer in ("y", "yes"):
            chosen.append(rel)
    return chosen


def env_copy(src_dir, dst_dir, src_name, dst_name, dry_run, out):
    """
    Copy changed files from src to dst.

    Shows the diff, then asks:
      y        — copy all differing files
      n/Enter  — abort
      s        — select file by file (y/n/q per file)

    Does not commit or push anything.
    """
    if not mf.load(src_dir):
        out.error(f"No manifest.json found in source directory: {src_dir}")
        out.info("Run  pyvoog pull  from the source environment to generate one.")
        return 1

    out.info("Comparing manifest-tracked files...")
    diff = _diff_envs(src_dir, dst_dir)
    _display_diff(diff, src_name, dst_name, src_dir, dst_dir, out, verbose=False)

    to_copy = diff["different"] + diff["only_src"]

    if not to_copy:
        out.info("\nNothing to copy — environments are in sync.")
        return 0

    label = "[dry-run] " if dry_run else ""
    out.info(f"\n{label}{len(to_copy)} file(s) can be copied: {src_name} → {dst_name}")

    if dry_run:
        return 0

    try:
        answer = input(
            f"\nCopy from {src_name} → {dst_name}?"
            " [y(es all)/N(o)/s(elect)]: "
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        out.info("\nAborted.")
        return 130

    if answer in ("s", "select"):
        chosen = _select_files(to_copy, out)
        if chosen is None:
            return 130
        if not chosen:
            out.info("\nNo files selected — nothing copied.")
            return 0
        to_copy = chosen
    elif answer in ("y", "yes"):
        pass  # copy all
    else:
        out.info("Aborted.")
        return 0

    out.info(f"\nCopying {len(to_copy)} file(s)...")
    succeeded, failed = _copy_files(to_copy, src_dir, dst_dir, out)

    parts = [f"{len(succeeded)} copied"]
    if failed:
        parts.append(f"{len(failed)} failed")
    out.info(f"\nDone: {', '.join(parts)}.")
    return 1 if failed else 0


def _check_peer_dir(peer_path, peer_name, out):
    """
    Validate that peer_path looks like a working Voog site directory.
    Checks: directory exists, .voog present, manifest.json present.
    Prints a result line for each check. Returns True if all pass.
    """
    out.info(f"\nChecking peer environment [{peer_name}] at: {peer_path}")

    checks = [
        ("Directory exists",  os.path.isdir(peer_path)),
        (".voog file present", os.path.isfile(os.path.join(peer_path, ".voog"))),
        ("manifest.json present", os.path.isfile(os.path.join(peer_path, "manifest.json"))),
    ]

    all_ok = True
    for label, ok in checks:
        if ok:
            out.success(label)
        else:
            out.fail(label)
            all_ok = False

    if not all_ok:
        out.info("")
        if not os.path.isdir(peer_path):
            out.warn("The path does not exist. Double-check the directory location.")
        elif not os.path.isfile(os.path.join(peer_path, ".voog")):
            out.warn("No .voog found — run  pyvoog init  in that directory first.")
        elif not os.path.isfile(os.path.join(peer_path, "manifest.json")):
            out.warn("No manifest.json — run  pyvoog pull  in that directory to generate one.")

    return all_ok


def env_setup(site_dir, voog_file, config, out):
    """
    Interactive wizard to configure env_name, env_peer_name, env_peer_path
    in the .voog file. Validates the peer directory after saving.
    """
    from .config import update_env_config

    out.info("pyvoog experimental env-setup")
    out.info("=" * 40)
    out.info(f"Config : {voog_file}")
    out.info(f"Section: [{config.section}]\n")
    out.info(
        "Set a name for this environment and for its peer (e.g. staging / production).\n"
        "Leave env_peer_path empty for the environment you run pyvoog from.\n"
    )

    def _prompt(label, current):
        display = f" [{current}]" if current else ""
        try:
            return input(f"  {label}{display}: ").strip()
        except (EOFError, KeyboardInterrupt):
            raise

    try:
        env_name      = _prompt("env_name      (this environment)", config.env_name or "") or config.env_name or ""
        env_peer_name = _prompt("env_peer_name (peer environment)", config.env_peer_name or "") or config.env_peer_name or ""
        env_peer_path = _prompt("env_peer_path (path to peer)    ", config.env_peer_path or "") or config.env_peer_path or ""
    except (EOFError, KeyboardInterrupt):
        out.info("\nAborted.")
        return 130

    unchanged = (
        env_name      == (config.env_name or "") and
        env_peer_name == (config.env_peer_name or "") and
        env_peer_path == (config.env_peer_path or "")
    )

    if unchanged:
        out.info("\nNo changes made.")
    else:
        update_env_config(voog_file, config.section, env_name, env_peer_name, env_peer_path)
        out.info(f"\nSaved to {voog_file}:")
        out.info(f"  env_name      = {env_name or '(empty)'}")
        out.info(f"  env_peer_name = {env_peer_name or '(empty)'}")
        out.info(f"  env_peer_path = {env_peer_path or '(empty)'}")

    if env_peer_path:
        peer_ok = _check_peer_dir(env_peer_path, env_peer_name or "peer", out)
        if not peer_ok:
            out.info("\nSetup saved but peer directory has issues — fix them before using env-diff / env-copy.")
            return 1
        out.info("\nPeer environment looks good. Ready to use env-diff and env-copy.")
    else:
        out.info("\nNo env_peer_path set — configure it to use env-diff and env-copy.")

    return 0
