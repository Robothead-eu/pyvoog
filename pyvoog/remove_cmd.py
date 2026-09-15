"""
remove_cmd.py — Delete layouts and assets locally and on the Voog server.

Equivalent of the Ruby kit's `kit remove`, which deletes the local file, the
manifest entry and the remote resource in one go. Unlike the Ruby kit this
asks before touching the server: a delete cannot be undone from here, and the
git history only covers the local copy.

Server IDs come from a fresh listing rather than from manifest.json, because
a stale manifest would otherwise point a delete at the wrong resource.
"""

import os

from .api import APIError
from .manifest import (
    layout_file_path, asset_file_path,
    load as load_manifest, save as save_manifest,
)


# The only directories a Voog site owns. Confining removes to these keeps
# `pyvoog remove` from touching .voog (which holds the API token),
# manifest.json, or any developer file that happens to share the directory.
SITE_DIRS = frozenset(
    ("layouts", "components", "stylesheets", "javascripts", "images", "assets")
)


def _resolve_target(site_dir, raw_path):
    """
    Turn a user-supplied path into a safe (rel_path, abs_path) pair.

    Returns (rel_path, abs_path, None) when the path is acceptable, or
    (rel_path, None, reason) when it must be refused. This command deletes
    files, so the path is checked rather than trusted: a stray `../` from a
    shell completion would otherwise remove something outside the site
    entirely.
    """
    rel = raw_path.replace("\\", "/").strip().strip("/")
    if not rel:
        return raw_path, None, "empty path"

    parts = rel.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return rel, None, "path must not contain '..' or '.' segments"

    if len(parts) != 2:
        return rel, None, (
            "expected <dir>/<file>, e.g. layouts/page.tpl "
            f"({'nested paths are not supported' if len(parts) > 2 else 'no directory given'})"
        )

    if parts[0] not in SITE_DIRS:
        return rel, None, (
            f"'{parts[0]}/' is not a Voog site directory "
            f"(expected one of: {', '.join(sorted(SITE_DIRS))})"
        )

    abs_path = os.path.abspath(os.path.join(site_dir, rel))
    # Belt and braces: a symlinked site directory, or a drive-absolute path on
    # Windows, could still land outside despite the checks above.
    root = os.path.realpath(site_dir)
    if os.path.commonpath([os.path.realpath(os.path.dirname(abs_path)), root]) != root:
        return rel, None, "path resolves outside the site directory"

    return rel, abs_path, None


def _server_maps(api, want_layouts, want_assets):
    """
    Return ({rel_path: (kind, id)}, {rel_path: [ids]}) for the live server.

    The second dict holds paths that more than one server resource maps to —
    two assets whose differing asset_type still folds into assets/, say. The
    whole point of resolving ids from a live listing is to delete the right
    thing, so an ambiguous path is refused rather than resolved arbitrarily.
    """
    found = {}
    collisions = {}
    def add(fp, kind, res_id):
        if fp in found:
            collisions.setdefault(fp, [found[fp][1]]).append(res_id)
        found[fp] = (kind, res_id)

    if want_layouts:
        for lay in api.get_layouts():
            add(layout_file_path(lay.get("layout_name", ""),
                                 lay.get("component", False)), "layout", lay["id"])
    if want_assets:
        for asset in api.get_layout_assets():
            add(asset_file_path(asset.get("filename", ""),
                                asset.get("asset_type", "")), "asset", asset["id"])
    return found, collisions


def _drop_from_manifest(manifest, rel_path):
    """Remove the entry for rel_path from the manifest. Returns True if found."""
    changed = False
    for key in ("layouts", "assets"):
        entries = manifest.get(key, [])
        kept = [e for e in entries if e.get("file") != rel_path]
        if len(kept) != len(entries):
            manifest[key] = kept
            changed = True
    return changed


def remove(api, site_dir, files, local_only=False, remote_only=False,
           dry_run=False, assume_yes=False, out=None):
    """
    Remove files locally and/or on the server, and drop them from the manifest.

    files       — list of relative paths
    local_only  — delete the local file only, leave the server alone
    remote_only — delete on the server only, keep the local file
    dry_run     — print the plan, change nothing
    assume_yes  — skip the confirmation prompt

    Returns (succeeded, failed).
    """
    succeeded = []
    failed = []

    if not files:
        out and out.error("No files given. Usage: pyvoog remove <file> [...]")
        return succeeded, failed

    targets = []
    for raw in files:
        rel_path, abs_path, reason = _resolve_target(site_dir, raw)
        if reason:
            out and out.warn(f"{rel_path}: refused — {reason}.")
            failed.append((rel_path, reason))
            continue
        targets.append((rel_path, abs_path))

    if not targets:
        return succeeded, failed

    manifest = load_manifest(site_dir) or {"layouts": [], "assets": []}

    # -- Work out what exists where -----------------------------------

    server = {}
    collisions = {}
    if not local_only:
        want_layouts = any(r.startswith(("layouts/", "components/")) for r, _ in targets)
        want_assets  = any(not r.startswith(("layouts/", "components/")) for r, _ in targets)
        out and out.info("Checking server state…")
        try:
            server, collisions = _server_maps(api, want_layouts, want_assets)
        except APIError as exc:
            out and out.error(f"Could not fetch server state: {exc}")
            return succeeded, [(r, str(exc)) for r, _ in targets]

    plan = []
    for rel_path, abs_path in targets:
        has_local = os.path.isfile(abs_path)
        kind_id = server.get(rel_path)

        if rel_path in collisions:
            ids = ", ".join(str(i) for i in collisions[rel_path])
            out and out.warn(
                f"{rel_path}: refused — {len(collisions[rel_path])} server "
                f"resources map to this path (ids {ids}). Remove it in the "
                "Voog editor so the right one is deleted."
            )
            failed.append((rel_path, "ambiguous on server"))
            continue

        if not has_local and not kind_id:
            where = "locally" if local_only else "locally or on the server"
            out and out.warn(f"{rel_path}: not found {where} — skipping.")
            failed.append((rel_path, "not found"))
            continue

        if remote_only and not kind_id:
            out and out.warn(
                f"{rel_path}: not on the server — nothing to remove with "
                "--remote-only."
            )
            failed.append((rel_path, "not on server"))
            continue

        plan.append((rel_path, abs_path, has_local, kind_id))

    if not plan:
        return succeeded, failed

    # -- Show the plan -------------------------------------------------

    prefix = "[dry-run] " if dry_run else ""
    out and out.info(f"\n{prefix}{len(plan)} file(s) to remove:")
    deletes_remote = False
    deletes_local = False
    for rel_path, _abs, has_local, kind_id in plan:
        where = []
        if has_local and not remote_only:
            where.append("local")
            deletes_local = True
        if kind_id and not local_only:
            where.append(f"server ({kind_id[0]} id {kind_id[1]})")
            deletes_remote = True
        if not where:
            where.append("manifest only")
        out and out.info(f"  - {rel_path}   [{', '.join(where)}]")

    if dry_run:
        out and out.info("\n[dry-run] Nothing was removed.")
        # The plan is what was asked for; refusals above are already in
        # `failed` and still set a non-zero exit.
        return [p[0] for p in plan], failed

    # -- Confirm -------------------------------------------------------
    #
    # Deleting on the server is irreversible and git only ever held the local
    # copy, so this always asks unless the caller passed --yes.

    if not assume_yes:
        scopes = []
        if deletes_local:
            scopes.append("local files")
        if deletes_remote:
            scopes.append("the REMOTE site")
        scope = " and ".join(scopes) or "manifest.json only"
        try:
            answer = input(
                f"\nDelete from {scope}? This cannot be undone. [y/N] "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            out and out.info("\nAborted.")
            return succeeded, failed
        if answer not in ("y", "yes"):
            out and out.info("Aborted.")
            return succeeded, failed

    # -- Execute -------------------------------------------------------

    out and out.info("")
    manifest_dirty = False

    for rel_path, abs_path, has_local, kind_id in plan:
        # Anything unexpected is contained to this one file: aborting the loop
        # would skip the manifest save and the git commit below, leaving the
        # manifest disagreeing with both disk and server for files already
        # deleted. A read timeout raises TimeoutError, not APIError, so this
        # catches Exception rather than just APIError.
        try:
            # Server first: if it fails, the local copy is still there to retry.
            if kind_id and not local_only:
                kind, res_id = kind_id
                try:
                    if kind == "layout":
                        api.delete_layout(res_id)
                    else:
                        api.delete_layout_asset(res_id)
                    out and out.log(f"Deleted on server: {rel_path}")
                except APIError as exc:
                    hint = ""
                    if kind == "layout":
                        hint = (
                            " Voog refuses to delete a layout that is still "
                            "assigned to a page, or on a site without a custom "
                            "design."
                        )
                    out and out.warn(
                        f"Could not delete {rel_path} on server: {exc}.{hint}"
                    )
                    failed.append((rel_path, str(exc)))
                    continue

                # The server copy is gone, so the manifest entry is wrong as of
                # now. Drop it before attempting the local delete, which may
                # fail on its own.
                if _drop_from_manifest(manifest, rel_path):
                    manifest_dirty = True

            if has_local and not remote_only:
                try:
                    os.remove(abs_path)
                    out and out.log(f"Deleted local file: {rel_path}")
                except OSError as exc:
                    out and out.warn(f"Could not delete local {rel_path}: {exc}")
                    failed.append((rel_path, str(exc)))
                    continue

            if _drop_from_manifest(manifest, rel_path):
                manifest_dirty = True

            out and out.success(rel_path)
            succeeded.append(rel_path)

        except Exception as exc:                      # noqa: BLE001
            out and out.warn(f"Unexpected error removing {rel_path}: {exc}")
            failed.append((rel_path, str(exc)))

    # -- Save manifest + commit ---------------------------------------

    # Save whenever the manifest changed, not only on success: a server delete
    # can succeed and the local delete still fail, and the manifest must not go
    # on claiming a resource that is already gone.
    if manifest_dirty:
        try:
            save_manifest(manifest, site_dir)
            out and out.log("Updated manifest.json.")
        except OSError as exc:
            out and out.warn(f"Could not update manifest.json: {exc}")

    if succeeded or manifest_dirty:
        from . import git
        if git.git_available():
            try:
                git.ensure_repo(site_dir)
                # --remote-only keeps the local files, so staging them would
                # make a commit named "remove" that adds a file.
                paths = [] if remote_only else list(succeeded)
                if manifest_dirty:
                    paths.append("manifest.json")
                if paths and git.commit_files(
                    site_dir, paths, f"pyvoog remove: {len(succeeded)} file(s)"
                ):
                    out and out.info(
                        f"\nCommitted removal of {len(succeeded)} file(s) to git."
                    )
            except RuntimeError as exc:
                out and out.warn(f"Git commit after remove failed: {exc}")

    # Not out.summary() — that reports "N written", which reads wrong here.
    parts = [f"{len(succeeded)} removed"]
    if failed:
        parts.append(f"{len(failed)} failed")
    out and out.info(f"\nDone: {', '.join(parts)}.")
    return succeeded, failed
