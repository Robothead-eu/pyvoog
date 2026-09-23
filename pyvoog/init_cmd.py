"""Initialise a site directory: .voog, a .gitignore that excludes it, and git init.

Works on existing directories too.
"""

import os

from .config import write_voog_file, load_config, ConfigError
from . import git


SITE_GITIGNORE = """\
# pyvoog — auto-generated .gitignore

# API token — never commit this
.voog

# Local content copy (pyvoog pull content) — never commit this
.voog-content/
.voog-content.*/

# Node / build tools
node_modules/
dist/
build/
.cache/

# Python cache
__pycache__/
*.pyc

# OS
.DS_Store
Thumbs.db
"""

# Appended to a .gitignore that already exists but doesn't exclude .voog.
VOOG_IGNORE_RULE = """
# pyvoog — API token, never commit this
.voog
"""


def _ignores_voog(gitignore_text):
    """Return True if the .gitignore text has a rule ignoring the .voog file.

    Only '.voog' and '/.voog' count: '.voog/' matches directories only.
    """
    for line in gitignore_text.splitlines():
        rule = line.strip()
        if not rule or rule.startswith("#") or rule.startswith("!"):
            continue
        if rule.endswith("/"):
            continue
        if rule.lstrip("/") == ".voog":
            return True
    return False



def init(target_dir, host, api_token, protocol="https", out=None):
    """Initialise target_dir (created if missing). Returns True on success."""
    abs_dir = os.path.abspath(target_dir)

    # -- Create directory if needed ------------------------------------

    if not os.path.exists(abs_dir):
        os.makedirs(abs_dir)
        out and out.info(f"Created directory: {abs_dir}")
    else:
        out and out.info(f"Using directory:   {abs_dir}")

    # -- Write .voog ---------------------------------------------------

    voog_path = os.path.join(abs_dir, ".voog")
    if os.path.isfile(voog_path):
        out and out.warn(f".voog already exists, not overwriting: {voog_path}")
    else:
        write_voog_file(voog_path, host, api_token, protocol=protocol)
        # Owner-only: the file holds a live API token. No-op on Windows.
        try:
            os.chmod(voog_path, 0o600)
        except OSError:
            pass
        out and out.info(f"Created .voog (keep this file private — it contains your API token)")

    # -- Write .gitignore ----------------------------------------------
    #
    # An existing .gitignore is kept, but must still ignore .voog so the token
    # can't be committed.

    gitignore_path = os.path.join(abs_dir, ".gitignore")
    if os.path.isfile(gitignore_path):
        try:
            # errors="replace": a latin-1 .gitignore must not abort init.
            # We only append, so a lossy read is safe.
            with open(gitignore_path, encoding="utf-8", errors="replace") as f:
                existing = f.read()
        except OSError as exc:
            existing = ""
            out and out.warn(f"Could not read .gitignore: {exc}")

        if _ignores_voog(existing):
            out and out.log(".gitignore already excludes .voog.")
        else:
            try:
                with open(gitignore_path, "a", encoding="utf-8") as f:
                    if existing and not existing.endswith("\n"):
                        f.write("\n")
                    f.write(VOOG_IGNORE_RULE)
                out and out.info(
                    "Added .voog to the existing .gitignore "
                    "(it holds your API token)."
                )
            except OSError as exc:
                out and out.warn(
                    f"Could not update .gitignore: {exc}. "
                    "Add a '.voog' line yourself — it contains your API token."
                )
    else:
        with open(gitignore_path, "w", encoding="utf-8") as f:
            f.write(SITE_GITIGNORE)
        out and out.info("Created .gitignore")

    # -- Git init ------------------------------------------------------

    if not git.git_available():
        out and out.warn("git not found on PATH — skipping git init. Install git for undo support.")
    else:
        try:
            initialised = git.ensure_repo(abs_dir)
            if initialised:
                out and out.info("Initialised git repository.")
            else:
                out and out.log("Git repository already exists.")
        except RuntimeError as exc:
            out and out.warn(f"git init failed: {exc}")

    # -- Verify config is readable -------------------------------------

    try:
        cfg = load_config(site_dir=abs_dir)
        out and out.info(f"\nReady. Site: {cfg.host}")
        out and out.info("Run  pyvoog pull  to download all templates and assets.")
    except ConfigError as exc:
        out and out.error(f"Config verification failed: {exc}")
        return False

    return True
