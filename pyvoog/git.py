"""Wrapper around the system git binary: init and commit snapshots. Git is optional."""

import os
import shutil
import subprocess


def git_available():
    """Return True if the `git` binary is on PATH."""
    return shutil.which("git") is not None


def _decode(raw):
    """Decode git output without raising.

    Filenames need not be valid UTF-8; surrogateescape keeps them usable with open().
    """
    return raw.decode("utf-8", errors="surrogateescape")


def _git(*args, cwd=None):
    """Run git in cwd; return (returncode, stdout, stderr), stripped. Never raises.

    Use _git_z() for -z output: stripping would eat leading-whitespace filenames.
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
    """Run a -z git command and return the paths as a list ([] on failure).

    Reads bytes, not text: text mode would translate a CR in a filename to LF.
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
    """git init path unless .git exists. Returns True if a repo was created.

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
    """git add -A and commit. Returns False if nothing to commit.

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
    """Return {hash, message, date} for HEAD, or None if there are no commits."""
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
    """Files changed in the working tree vs HEAD ([] if no repo or commits).

    -z avoids core.quotepath escaping of non-ASCII names, which would never
    match manifest entries and be skipped by push.
    """
    # --relative: paths match manifest.json when the site is a subdirectory
    # of a larger repo, as untracked_files() output already does.
    return _git_z("diff", "HEAD", "--name-only", "-z", "--relative", cwd=path)


def untracked_files(path):
    """Untracked, non-ignored files, which push needs to create new files ([] if no repo)."""
    return _git_z("ls-files", "--others", "--exclude-standard", "-z", cwd=path)


def commit_files(path, files, message):
    """Stage only the given files and commit, leaving other files untracked.

    Returns False if nothing was staged; raises RuntimeError on commit failure.
    """
    for f in files:
        _git("add", f, cwd=path)

    code, _out, _err = _git("diff", "--cached", "--quiet", cwd=path)
    if code == 0:
        return False

    code, _out, err = _git("commit", "-m", message, cwd=path)
    if code != 0:
        raise RuntimeError(f"git commit failed: {err}")
    return True

