"""Show site info, manifest and content counts, and git state."""

import os

from . import git
from .content import read_manifest as load_content_manifest
from .manifest import load as load_manifest


def status(site_dir, config, out):
    """Print site status to out."""
    out.info(f"Site:      {config.host}")
    out.info(f"Protocol:  {config.protocol}")
    out.info(f"Directory: {site_dir}")

    # -- Manifest info -------------------------------------------------

    manifest = load_manifest(site_dir)
    if manifest:
        layouts = manifest.get("layouts", [])
        assets = manifest.get("assets", [])
        components = [l for l in layouts if l.get("component")]
        page_layouts = [l for l in layouts if not l.get("component")]
        out.info(f"\nManifest (manifest.json):")
        out.info(f"  Layouts:    {len(page_layouts)}")
        out.info(f"  Components: {len(components)}")
        out.info(f"  Assets:     {len(assets)}")
    else:
        out.info("\nManifest: not found (run  voog pull  to create it)")

    # -- Content copy (pull content) ------------------------------------

    content = load_content_manifest(site_dir)
    if isinstance(content, dict):
        counts = ", ".join(f"{n} {k}" for k, n in content.get("counts", {}).items())
        scope = " (published only)" if content.get("options", {}).get("published_only") else ""
        out.info(f"\nContent (.voog-content/): pulled {content.get('pulled_at', '?')}{scope}")
        if counts:
            out.info(f"  {counts}")
    elif config.content_pull is not True:
        out.info("\nContent: disabled (set  content_pull=true  in .voog to enable  pull content)")
    else:
        out.info("\nContent: not pulled (run  pyvoog pull content  for voog-server)")

    # -- Git info ------------------------------------------------------

    git_dir = os.path.join(site_dir, ".git")
    if not os.path.isdir(git_dir):
        out.info("\nGit: not initialised (will be set up on first pull)")
    elif not git.git_available():
        out.info("\nGit: git binary not found on PATH")
    else:
        last = git.last_commit_info(site_dir)
        if last:
            out.info(f"\nGit: {last['hash']}  {last['date'][:19]}")
            out.info(f"     {last['message']}")
        else:
            out.info("\nGit: repo exists but no commits yet")

        if git.has_changes(site_dir):
            out.info("     (uncommitted local changes — run  git diff  to review)")
