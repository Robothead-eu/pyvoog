"""
git.py — Lightweight git integration for voog-cli.

Provides auto-init and auto-commit so every pull is a reversible snapshot.
All operations call the system `git` binary via subprocess.
Git is optional — the tool works without it, but warns.
"""

import os
import shutil
import subprocess


def git_available():
    """Return True if the `git` binary is on PATH."""
    return shutil.which("git") is not None


def _decode(raw):
    """
    Decode git output without ever raising.

    Filenames on Linux are bytes, not text, and need not be valid UTF-8 — a
    file named café.jpg saved by a latin-1 tool is legal. surrogateescape
    keeps those bytes recoverable, and the resulting str can still be passed
    straight back to open() and os.path. Decoding strictly here would turn
    one odd filename into a crash that aborts the whole command.
    """
    return raw.decode("utf-8", errors="surrogateescape")


def _git(*args, cwd=None):
    """
    Run git with the given args in cwd.
    Returns (returncode, stdout, stderr), both streams stripped.
    Never raises — callers check returncode.

    Not for NUL-separated output: use _git_z() for that, since stripping
    would eat a leading-whitespace filename.
    """
    result = subprocess.run(
        ["git"] + list(args),
        capture_output=True,
        cwd=cwd,
    )
    return (
        result.returncode,
        _decode(result.stdout).strip(),
        _decode(result.stderr).strip(),
    )


def _git_z(*args, cwd=None):
    """
    Run a git command whose output is NUL-separated (-z) and return the
    paths as a list.

    Reading bytes directly rather than in text mode matters twice over: it
    avoids a decode error on non-UTF-8 filenames, and it avoids universal
    newline translation, which would rewrite a CR inside a filename into LF
    and yield a path that does not exist.
    """
    result = subprocess.run(
        ["git"] + list(args),
        capture_output=True,
        cwd=cwd,
    )
    if result.returncode != 0:
        return []
    return [_decode(p) for p in result.stdout.split(b"\0") if p]


def ensure_repo(path):
    """
    Ensure path is a git repository.
    If .git doesn't exist, runs `git init`.
    Returns True if a new repo was initialised, False if it already existed.
    Raises RuntimeError on failure.
    """
    if os.path.isdir(os.path.join(path, ".git")):
        return False

    code, _, err = _git("init", cwd=path)
    if code != 0:
        raise RuntimeError(f"git init failed: {err}")
    return True


def has_changes(path):
    """Return True if there are staged or unstaged changes in the repo."""
    _code, out, _err = _git("status", "--porcelain", cwd=path)
    return bool(out.strip())


def commit_all(path, message):
    """
    Stage all changes (git add -A) and commit with message.
    Returns True if a commit was made, False if there was nothing to commit.
    Raises RuntimeError on commit failure.
    """
    if not has_changes(path):
        return False

    _git("add", "-A", cwd=path)
    code, _out, err = _git("commit", "-m", message, cwd=path)
    if code != 0:
        raise RuntimeError(f"git commit failed: {err}")
    return True


def last_commit_info(path):
    """
    Return a dict with last commit info, or None if no commits yet.
    Keys: hash (short), message, date.
    """
    code, out, _err = _git(
        "log", "-1", "--pretty=format:%h|%s|%ci", cwd=path
    )
    if code != 0 or not out:
        return None
    parts = out.split("|", 2)
    if len(parts) < 3:
        return None
    return {"hash": parts[0], "message": parts[1], "date": parts[2]}



def changed_files(path):
    """
    Return all files changed since the last commit (working tree vs HEAD),
    regardless of extension or directory.
    Returns an empty list if there is no git repo or no commits yet.

    Uses -z (NUL-separated) rather than plain --name-only: with the default
    core.quotepath=true git C-escapes any non-ASCII byte and wraps the path
    in double quotes, e.g. "images/p\\303\\244rnu.jpg". Those mangled paths
    never match a manifest entry, so the file would be silently treated as
    an untracked developer file and skipped by push. -z emits raw paths.
    """
    # --relative makes the paths relative to `path` rather than to the repo
    # root. That is a no-op for the usual case where the site directory is
    # the repo root, and it is what manifest.json entries look like when the
    # site lives in a subdirectory of a larger repo. It also keeps this
    # consistent with untracked_files(), whose output is always cwd-relative.
    return _git_z("diff", "HEAD", "--name-only", "-z", "--relative", cwd=path)


def untracked_files(path):
    """
    Return files that exist locally but are not tracked by git and are not
    ignored by .gitignore. These never show up in `git diff HEAD`, so push
    needs them separately to be able to create brand-new files.
    Returns an empty list if there is no git repo.
    """
    return _git_z("ls-files", "--others", "--exclude-standard", "-z", cwd=path)


def commit_files(path, files, message):
    """
    Stage only the specified files and commit.

    files   — iterable of relative paths within the repo
    message — commit message

    Unlike commit_all(), this never stages files outside the given list,
    so developer files in the same directories are left untracked.

    Returns True if a commit was made, False if nothing to commit.
    Raises RuntimeError on failure.
    """
    for f in files:
        _git("add", f, cwd=path)

    # Check if anything is actually staged
    code, _out, _err = _git("diff", "--cached", "--quiet", cwd=path)
    if code == 0:
        return False  # nothing staged

    code, _out, err = _git("commit", "-m", message, cwd=path)
    if code != 0:
        raise RuntimeError(f"git commit failed: {err}")
    return True

