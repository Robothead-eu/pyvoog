#!/usr/bin/env python3
"""
pyvoog.py — Voog CMS command-line tool.

A reliable Python replacement for the Ruby voog-kit.
Pulls site templates and assets directly via the Voog REST API.

Usage:
    python pyvoog.py <command> [options]

Run  python pyvoog.py help  for the full command reference.
"""

import argparse
import os
import sys

from pyvoog import __version__
from pyvoog.config import load_config, ConfigError
from pyvoog.api import VoogAPI, APIError
from pyvoog.output import Output


# ------------------------------------------------------------------
# Help text
# ------------------------------------------------------------------

HELP_TEXT = """\
pyvoog {version} — Voog CMS command-line tool

USAGE
    python pyvoog.py <command> [options]

    Set up a shell alias to call it as just  pyvoog  from any site directory:
    (see README.md for setup instructions)

COMMANDS

  init [DIR] --host HOST --token TOKEN
      Initialise a site directory with a .voog config, .gitignore and
      git repository. DIR defaults to the current directory.

      Examples:
          python pyvoog.py init --host mysite.voog.com --token abc123
          python pyvoog.py init ./my-site --host mysite.voog.com --token abc123

  pull [--dry-run] [--reset]
      Pull all layout, component .tpl files and design assets from the server.
      Server is always the source of truth; local files are overwritten.
      A git commit is made automatically after each successful pull.
      Only manifest-tracked files are staged in git — developer files are
      left untouched.

      Examples:
          python pyvoog.py pull
          python pyvoog.py pull --dry-run
          python pyvoog.py pull --reset

  check
      Compare local files against the server without writing anything.
      Shows missing, modified, and extra files.

      Example:
          python pyvoog.py check

  manifest [--save]
      Fetch and display the remote manifest (file list).
      Add --save to write manifest.json to the site directory.

      Examples:
          python pyvoog.py manifest
          python pyvoog.py manifest --save

  status
      Show site info: host, manifest summary, and last git commit.

      Example:
          python pyvoog.py status

  push [FILE ...] [--dry-run] [--force] [--create]
      Push locally modified layouts and text assets (CSS/JS) to the server.
      Only files tracked in manifest.json are eligible — developer files are
      ignored automatically. Detects server-side conflicts before uploading.

      Use --force to skip the conflict check and push even when the server
      has a newer timestamp (your local content will overwrite the server).

      Use --create to upload files that don't yet exist on the server
      (and are therefore not in manifest.json). The file is created on the
      server and the manifest is updated automatically.

      Examples:
          python pyvoog.py push
          python pyvoog.py push layouts/page.tpl stylesheets/main.css
          python pyvoog.py push --dry-run
          python pyvoog.py push --force
          python pyvoog.py push stylesheets/newfile.css --create
          python pyvoog.py push layouts/blog.tpl --create

  new FILE [--type TYPE] [--dry-run]
      Create a new layout or asset on the Voog server from a local file.
      The file must exist locally. Type is inferred from the directory:
        layouts/    → layout (content_type=page)
        components/ → component
        stylesheets/, javascripts/, images/, assets/ → asset

      Examples:
          python pyvoog.py new layouts/blog.tpl
          python pyvoog.py new components/sidebar.tpl
          python pyvoog.py new stylesheets/custom.css
          python pyvoog.py new layouts/blog.tpl --type blog

  new --all [--dry-run]
      Find all local files not on the server and create them.
      Shows the list and asks for confirmation before proceeding.

      Examples:
          python pyvoog.py new --all
          python pyvoog.py new --all --dry-run

  new --list
      List local files that are not yet on the server.

      Example:
          python pyvoog.py new --list

  watch (not yet implemented)
      Watch local files for changes and push automatically.

GLOBAL OPTIONS
    --verbose, -v   Show detailed output (API calls, file writes, git ops)
    --site NAME     Select a named section from .voog (for multi-site configs)
    --version       Print version and exit

WHERE TO RUN
    Run pyvoog from inside your site directory (where .voog lives),
    or from any subdirectory — pyvoog walks up to find .voog.

    The tool lives in its own directory and operates on the current
    working directory. Example workflow:

        cd ~/sites/mysite
        python ~/tools/pyvoog/pyvoog.py pull

FILES
    .voog        — Site config (host, api_token). Never commit this file.
    .gitignore   — Created by  pyvoog init  to exclude .voog from git.
    manifest.json — Updated automatically on every pull.
""".format(version=__version__)

COMMAND_HELP = {
    "init": """\
pyvoog init [DIR] --host HOST --token TOKEN

Initialise a site directory.

Arguments:
    DIR       Directory to create or use (default: current directory)
    --host    Site hostname, e.g. mysite.voog.com
    --token   Voog API token (find it in the Voog admin panel)
    --protocol  http or https (default: https)

Examples:
    python pyvoog.py init --host mysite.voog.com --token abc123
    python pyvoog.py init ./new-site --host mysite.voog.com --token abc123
""",
    "pull": """\
pyvoog pull [--dry-run] [--reset]

Pull all layouts, components, and design assets from the Voog server.
Server content always overwrites local files.
A git commit is made automatically after a successful pull.
Only manifest-tracked files are staged in git.

Arguments:
    --dry-run Show what would be written without writing anything
    --reset   Also remove local .tpl files not present on the server

Examples:
    python pyvoog.py pull
    python pyvoog.py pull --dry-run
    python pyvoog.py pull --reset
""",
    "check": """\
pyvoog check

Compare local files against the server.
Reports missing, modified, and extra files without writing anything.

Example:
    python pyvoog.py check
    python pyvoog.py check --verbose
""",
    "manifest": """\
pyvoog manifest [--save]

Fetch the remote manifest and display a summary.
Use --save to write manifest.json to the site directory.

Examples:
    python pyvoog.py manifest
    python pyvoog.py manifest --save --verbose
""",
    "status": """\
pyvoog status

Show site info: host, manifest summary, and last git commit.

Example:
    python pyvoog.py status
""",
    "new": """\
pyvoog new FILE [--type TYPE] [--dry-run]
pyvoog new --all [--dry-run]

Create new layouts or assets on the Voog server from local files.

Single file:
    python pyvoog.py new layouts/blog.tpl
    python pyvoog.py new components/sidebar.tpl
    python pyvoog.py new stylesheets/custom.css
    python pyvoog.py new layouts/blog.tpl --type blog

All new files:
    python pyvoog.py new --all
    python pyvoog.py new --all --dry-run

List new files (no action):
    python pyvoog.py new --list

Type is inferred from the directory (layouts → page, components → component).
Use --type to override for special layouts (blog, blog_article, etc.).
""",
}


# ------------------------------------------------------------------
# Command implementations
# ------------------------------------------------------------------

def cmd_init(args, out):
    from pyvoog.init_cmd import init
    target = args.dir or os.getcwd()
    ok = init(target, args.host, args.token, protocol=args.protocol, out=out)
    return 0 if ok else 1


def cmd_pull(args, out, config, site_dir):
    from pyvoog.pull import pull
    from pyvoog import git

    api = VoogAPI(config, output=out)

    subset = args.subset  # None, 'layouts', or 'assets'
    dry_run = args.dry_run
    reset = args.reset

    if dry_run:
        out.info("(dry-run mode — no files will be written)\n")

    succeeded, failed = pull(
        api=api,
        site_dir=site_dir,
        subset=subset,
        dry_run=dry_run,
        reset=reset,
        out=out,
    )

    out.summary(succeeded, failed, dry_run=dry_run)

    # Auto-commit after a real pull — stage only the pulled files + manifest.
    # Using commit_files() instead of commit_all() so developer files in the
    # same directories are never accidentally staged.
    if not dry_run and succeeded:
        if not git.git_available():
            out.warn("git not found — skipping auto-commit.")
        else:
            try:
                git.ensure_repo(site_dir)
                subset_label = f" ({subset})" if subset else ""
                message = (
                    f"pyvoog pull{subset_label}: "
                    f"{len(succeeded)} files"
                )
                committed = git.commit_files(
                    site_dir,
                    succeeded + ["manifest.json"],
                    message,
                )
                if committed:
                    out.info(f"\nCommitted: \"{message}\"")
                else:
                    out.log("Nothing to commit (all files unchanged).")
            except RuntimeError as exc:
                out.warn(f"Git error: {exc}")

    return 1 if failed else 0


def cmd_check(args, out, config, site_dir):
    from pyvoog.check import check, display_check_result

    api = VoogAPI(config, output=out)
    result = check(api, site_dir, out=out)
    display_check_result(result, out)

    issues = (
        len(result["layouts"]["missing"])
        + len(result["layouts"]["modified"])
        + len(result["assets"]["missing"])
    )
    return 1 if (result.get("error") or issues) else 0


def cmd_manifest(args, out, config, site_dir):
    from pyvoog import manifest as mf

    api = VoogAPI(config, output=out)

    out.info("Fetching layouts from server…")
    try:
        layouts = api.get_layouts()
    except APIError as exc:
        out.error(str(exc))
        return 1

    out.info("Fetching layout assets from server…")
    try:
        assets = api.get_layout_assets()
    except APIError as exc:
        out.error(str(exc))
        return 1

    remote_manifest = mf.build_from_api(layouts, assets)

    out.info("\nRemote manifest:")
    mf.display(remote_manifest, out, verbose=args.verbose)

    if args.save:
        mf.save(remote_manifest, site_dir)
        out.info("\nSaved manifest.json")

    return 0


def cmd_status(args, out, config, site_dir):
    from pyvoog.status import status
    status(site_dir, config, out)
    return 0


def cmd_push(args, out, config, site_dir):
    from pyvoog.push import push

    api = VoogAPI(config, output=out)

    files   = args.files or None   # [] from argparse → treat as None (auto-detect)
    dry_run = args.dry_run
    force   = args.force
    create  = args.create

    if dry_run:
        out.info("(dry-run mode — nothing will be uploaded)\n")
    if force:
        out.info("(--force: conflict check skipped)\n")
    if create:
        out.info("(--create: new files will be created on server)\n")

    succeeded, failed = push(
        api=api,
        site_dir=site_dir,
        files=files if files else None,
        dry_run=dry_run,
        force=force,
        create=create,
        out=out,
    )

    return 1 if failed else 0


def cmd_new(args, out, config, site_dir):
    from pyvoog.new_cmd import new_single, new_all, list_new
    from pyvoog import git

    api = VoogAPI(config, output=out)
    dry_run = args.dry_run

    if args.list:
        list_new(api, site_dir, out=out)
        return 0

    if dry_run:
        out.info("(dry-run mode — nothing will be created)\n")

    if args.all:
        succeeded, failed = new_all(api, site_dir, dry_run=dry_run, out=out)
        if not dry_run and succeeded:
            out.info(f"\nCreated {len(succeeded)} file(s) on server.")
            if git.git_available():
                try:
                    git.ensure_repo(site_dir)
                    commit_paths = list(succeeded) + ["manifest.json"]
                    committed = git.commit_files(
                        site_dir,
                        commit_paths,
                        f"pyvoog new --all: {len(succeeded)} file(s) created",
                    )
                    if committed:
                        out.info("Committed to git.")
                except RuntimeError as exc:
                    out.warn(f"Git error: {exc}")
        if failed:
            out.info(f"{len(failed)} file(s) failed.")
        return 1 if failed else 0
    else:
        if not args.file:
            out.error("Specify a file path or use --all.\nExample: pyvoog new layouts/blog.tpl")
            return 1
        ok = new_single(
            api, site_dir, args.file,
            content_type_override=args.type,
            dry_run=dry_run, out=out,
        )
        if ok and not dry_run and git.git_available():
            try:
                git.ensure_repo(site_dir)
                committed = git.commit_files(
                    site_dir,
                    [args.file, "manifest.json"],
                    f"pyvoog new: {args.file}",
                )
                if committed:
                    out.info("Committed to git.")
            except RuntimeError as exc:
                out.warn(f"Git error: {exc}")
        return 0 if ok else 1


def cmd_watch(args, out, config, site_dir):
    out.info("watch: not yet implemented.")
    return 1


def cmd_help(args, out):
    topic = getattr(args, "topic", None)
    if topic and topic in COMMAND_HELP:
        out.info(COMMAND_HELP[topic])
    else:
        out.info(HELP_TEXT)
    return 0


# ------------------------------------------------------------------
# Argument parser
# ------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="pyvoog",
        description="Voog CMS command-line tool",
        add_help=True,
    )
    parser.add_argument(
        "--version", action="version", version=f"pyvoog {__version__}"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Show detailed output (API calls, file writes, git ops)",
    )
    parser.add_argument(
        "--site",
        metavar="NAME",
        help="Select a named site section from .voog (multi-site configs)",
    )

    sub = parser.add_subparsers(dest="command", metavar="command")

    # help
    p_help = sub.add_parser("help", help="Show help")
    p_help.add_argument("topic", nargs="?", choices=list(COMMAND_HELP), metavar="command")

    # init
    p_init = sub.add_parser("init", help="Initialise a site directory")
    p_init.add_argument("dir", nargs="?", default=None, metavar="DIR",
                        help="Target directory (default: current directory)")
    p_init.add_argument("--host", required=True, metavar="HOST",
                        help="Site hostname, e.g. mysite.voog.com")
    p_init.add_argument("--token", required=True, metavar="TOKEN",
                        help="Voog API token")
    p_init.add_argument("--protocol", default="https", choices=["https", "http"],
                        help="Protocol (default: https)")

    # pull
    p_pull = sub.add_parser("pull", help="Pull files from the server")
    p_pull.add_argument("subset", nargs="?", choices=["layouts", "assets"],
                        default=None, metavar="[layouts|assets]",
                        help="Pull only layouts or only assets (default: all)")
    p_pull.add_argument("--dry-run", action="store_true",
                        help="Show what would be written without writing")
    p_pull.add_argument("--reset", action="store_true",
                        help="Also remove local files not on the server")

    # check
    sub.add_parser("check", help="Compare local files against the server")

    # manifest
    p_manifest = sub.add_parser("manifest", help="Fetch and display the remote manifest")
    p_manifest.add_argument("--save", action="store_true",
                             help="Write manifest.json to the site directory")

    # status
    sub.add_parser("status", help="Show site info and git state")

    # push
    p_push = sub.add_parser("push", help="Push local changes to the server")
    p_push.add_argument(
        "files", nargs="*", metavar="FILE",
        help="Specific file(s) to push (default: all changed manifest-tracked files)",
    )
    p_push.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be pushed without uploading",
    )
    p_push.add_argument(
        "--force", action="store_true",
        help="Skip conflict check and overwrite server even when its timestamp is newer",
    )
    p_push.add_argument(
        "--create", action="store_true",
        help="Create files that don't exist on the server (not yet in manifest)",
    )

    # new
    p_new = sub.add_parser("new", help="Create new layouts/assets on the server from local files")
    p_new.add_argument(
        "file", nargs="?", default=None, metavar="FILE",
        help="Local file path to create on server (e.g. layouts/blog.tpl)",
    )
    p_new.add_argument(
        "--all", action="store_true",
        help="Find and create all local files not on the server",
    )
    p_new.add_argument(
        "--list", action="store_true",
        help="List local files not yet on the server",
    )
    p_new.add_argument(
        "--type", metavar="TYPE",
        choices=["page", "blog", "blog_article", "elements", "element",
                 "product", "error_401", "error_404"],
        help="Override content_type for layouts (default: page)",
    )
    p_new.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be created without creating",
    )

    # watch (stub)
    sub.add_parser("watch", help="Watch for changes and push automatically (not yet implemented)")

    return parser


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def _resolve_site_dir(args):
    """
    Determine the site directory from context.
    For 'init', it's args.dir (or cwd if not given — handled in cmd_init).
    For all other commands, walk up from cwd to find .voog.
    Returns (site_dir, config) or raises ConfigError.
    """
    from pyvoog.config import find_voog_file
    voog_file = find_voog_file()
    if voog_file:
        return os.path.dirname(os.path.abspath(voog_file))
    return os.getcwd()


def _pre_extract_globals(argv):
    """
    Extract --verbose/-v and --site anywhere in the arg list before argparse,
    so users can write `voog pull --verbose` or `voog --verbose pull`.
    Returns (verbose, site, cleaned_argv).
    """
    verbose = False
    site = None
    cleaned = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--verbose", "-v"):
            verbose = True
        elif arg == "--site" and i + 1 < len(argv):
            site = argv[i + 1]
            i += 1
        elif arg.startswith("--site="):
            site = arg[len("--site="):]
        else:
            cleaned.append(arg)
        i += 1
    return verbose, site, cleaned


def main():
    verbose, site_pre, cleaned_argv = _pre_extract_globals(sys.argv[1:])

    parser = build_parser()
    args = parser.parse_args(cleaned_argv)

    # Merge pre-extracted globals onto the namespace
    args.verbose = verbose
    if not getattr(args, "site", None):
        args.site = site_pre

    out = Output(verbose=args.verbose)

    # No command → print help
    if not args.command:
        out.info(HELP_TEXT)
        sys.exit(0)

    if args.command == "help":
        sys.exit(cmd_help(args, out))

    if args.command == "init":
        sys.exit(cmd_init(args, out))

    # All other commands need a site config
    site_dir = _resolve_site_dir(args)
    try:
        config = load_config(site_dir=site_dir, site_name=args.site)
    except ConfigError as exc:
        out.error(str(exc))
        sys.exit(1)

    if args.verbose:
        out.info(f"Site: {config.host}  ({site_dir})\n")

    dispatch = {
        "pull":     lambda: cmd_pull(args, out, config, site_dir),
        "check":    lambda: cmd_check(args, out, config, site_dir),
        "manifest": lambda: cmd_manifest(args, out, config, site_dir),
        "status":   lambda: cmd_status(args, out, config, site_dir),
        "push":     lambda: cmd_push(args, out, config, site_dir),
        "new":      lambda: cmd_new(args, out, config, site_dir),
        "watch":    lambda: cmd_watch(args, out, config, site_dir),
    }

    handler = dispatch.get(args.command)
    if not handler:
        out.error(f"Unknown command: {args.command}")
        out.info("Run  pyvoog help  for the full command reference.")
        sys.exit(1)

    try:
        exit_code = handler()
    except KeyboardInterrupt:
        out.info("\nInterrupted.")
        sys.exit(130)
    except Exception as exc:
        out.error(f"Unexpected error: {exc}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)

    sys.exit(exit_code or 0)


if __name__ == "__main__":
    main()
