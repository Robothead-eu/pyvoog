"""
push.py — Push locally modified layouts and text assets to the Voog server.

Change detection:  git diff HEAD filtered against manifest.json entries.
Conflict detection: server updated_at vs manifest updated_at — if the server
                    was modified after our last pull, we skip and warn.

Safety rules:
  - Only files present in manifest.json are eligible for push.
    Developer files in the same directories are silently ignored.
  - Binary assets (images/fonts) cannot be updated via the API; skipped.
  - Creating new remote files is not yet supported; files absent from the
    server get a clear error with a suggested remedy.
"""

import os

from .api import APIError
from .manifest import load, lookup_by_file, layout_file_path, asset_file_path
from . import git


TEXT_ASSET_TYPES = frozenset(("stylesheet", "javascript"))


def _create_from_entry(api, site_dir, rel_path, entry, dry_run, out):
    """
    Re-create a server resource using its manifest entry + local file content.
    Used when a file is tracked in manifest.json but no longer exists on the server.
    Returns the server response dict on success, or None on failure.
    """
    is_layout = rel_path.startswith(("layouts/", "components/"))
    abs_path = os.path.join(site_dir, rel_path)

    if dry_run:
        out and out.info(f"  [dry-run] Would create {rel_path} on server.")
        return {"dry_run": True}

    try:
        if is_layout:
            with open(abs_path, encoding="utf-8") as f:
                body = f.read()
            return api.create_layout(
                title=entry.get("title", ""),
                content_type=entry.get("content_type", "page"),
                body=body,
                component=entry.get("component", False),
                layout_name=entry.get("layout_name", ""),
            )
        else:
            asset_type = entry.get("asset_type", "")
            filename = entry.get("filename", "")
            content_type = entry.get("content_type", "application/octet-stream")
            if asset_type in TEXT_ASSET_TYPES:
                with open(abs_path, encoding="utf-8") as f:
                    data = f.read()
                return api.create_layout_asset(
                    filename=filename, data=data, content_type=content_type,
                )
            else:
                with open(abs_path, "rb") as f:
                    file_bytes = f.read()
                return api.create_layout_asset(
                    filename=filename, file_bytes=file_bytes, content_type=content_type,
                )
    except OSError as exc:
        out and out.warn(f"Could not read {rel_path}: {exc}")
        return None
    except APIError as exc:
        out and out.warn(f"Failed to create {rel_path} on server: {exc}")
        return None


def push(api, site_dir, files=None, dry_run=False, force=False, create=False, out=None):
    """
    Push locally modified files to the Voog server.

    api      — VoogAPI instance
    site_dir — absolute path to the site directory
    files    — optional list of specific relative paths to push;
               if None, candidates are determined by git diff HEAD
    dry_run  — show what would be pushed but don't upload
    force    — skip conflict check; overwrite server even when its
               updated_at is newer than the manifest
    create   — when True, files not in manifest.json are created on the
               server and added to the manifest; files in manifest but
               absent from the server are also re-created

    Only files that appear in manifest.json are pushed (unless create=True).
    Returns (succeeded, failed) lists of relative paths.
    """
    succeeded = []
    failed = []

    # -- Load manifest ------------------------------------------------

    manifest = load(site_dir)
    if not manifest:
        out and out.error(
            "No manifest.json found. Run 'voog pull' first to sync the site."
        )
        return succeeded, failed

    by_file = lookup_by_file(manifest)  # {rel_path: entry}

    if not by_file:
        out and out.info("Manifest is empty — nothing to push.")
        return succeeded, failed

    # -- Determine candidates -----------------------------------------

    to_create = []  # files not in manifest that will be created (--create)

    if files:
        # Explicit file list from the command line
        candidates = []
        for f in files:
            f = f.replace("\\", "/")  # normalise Windows paths
            if f in by_file:
                candidates.append(f)
            elif create:
                to_create.append(f)
            else:
                out and out.warn(
                    f"{f}: not in manifest — skipping. "
                    "The file may not exist on the server, or run 'voog pull' first. "
                    "Use --create to create it on the server."
                )
    else:
        # git diff HEAD ∩ manifest
        if not git.git_available():
            out and out.error(
                "git is not available — cannot detect changed files. "
                "Specify files explicitly: voog push layouts/page.tpl"
            )
            return succeeded, failed

        changed = git.changed_files(site_dir)
        candidates = [f for f in changed if f in by_file]

        # Log skipped developer files (verbose only)
        skipped_dev = [
            f for f in changed
            if f not in by_file and f != "manifest.json"
        ]
        if skipped_dev:
            out and out.log(
                f"  ({len(skipped_dev)} non-manifest file(s) skipped: "
                + ", ".join(skipped_dev[:3])
                + ("…" if len(skipped_dev) > 3 else "") + ")"
            )

    if not candidates and not to_create:
        out and out.info(
            "Nothing to push — no local changes to manifest-tracked files."
        )
        return succeeded, failed

    prefix = "[dry-run] " if dry_run else ""
    if candidates:
        out and out.info(f"{prefix}{len(candidates)} file(s) to push:")
        for f in candidates:
            out and out.info(f"  ~ {f}")
    if to_create:
        out and out.info(f"{prefix}{len(to_create)} new file(s) to create on server:")
        for f in to_create:
            out and out.info(f"  + {f}")
    out and out.info("")

    # -- Fetch server state for conflict detection --------------------
    #
    # We fetch the full layout/asset lists (lightweight — no bodies).
    # This gives us the current server updated_at and the server IDs.
    # We compare server updated_at vs the updated_at stored in our manifest
    # (recorded at last pull) to detect if someone edited on the server.

    out and out.info("Checking server state…")

    has_layouts = any(f.startswith(("layouts/", "components/")) for f in candidates)
    has_assets  = any(not f.startswith(("layouts/", "components/")) for f in candidates)

    server_by_file = {}  # {rel_path: {"id": int, "updated_at": str}}

    if has_layouts:
        try:
            for lay in api.get_layouts():
                name      = lay.get("layout_name", "")
                component = lay.get("component", False)
                fp = layout_file_path(name, component)
                server_by_file[fp] = {
                    "id":         lay["id"],
                    "updated_at": lay.get("updated_at", ""),
                }
        except APIError as exc:
            out and out.error(f"Could not fetch layouts from server: {exc}")
            return succeeded, failed

    if has_assets:
        try:
            for asset in api.get_layout_assets():
                fp = asset_file_path(
                    asset.get("filename", ""), asset.get("asset_type", "")
                )
                server_by_file[fp] = {
                    "id":         asset["id"],
                    "updated_at": asset.get("updated_at", ""),
                }
        except APIError as exc:
            out and out.error(f"Could not fetch assets from server: {exc}")
            return succeeded, failed

    # -- Push each file -----------------------------------------------

    total        = len(candidates)
    conflicts    = []
    manifest_dirty = False

    for i, rel_path in enumerate(candidates, 1):
        entry       = by_file[rel_path]
        server_info = server_by_file.get(rel_path)

        out and out.progress(i, total, rel_path)

        # File in manifest but absent on server
        if server_info is None:
            out and out.progress_done()
            if create:
                out and out.info(f"  {rel_path}: not on server — creating…")
                result = _create_from_entry(api, site_dir, rel_path, entry, dry_run, out)
                if result:
                    if not dry_run:
                        entry["id"] = result.get("id", entry.get("id"))
                        new_ts = result.get("updated_at", "")
                        if new_ts:
                            entry["updated_at"] = new_ts
                        manifest_dirty = True
                    succeeded.append(rel_path)
                else:
                    failed.append((rel_path, "create failed"))
            else:
                out and out.warn(
                    f"{rel_path}: not found on server. "
                    "Creating new files is not yet supported — "
                    "create it via the Voog editor first, then run 'voog pull'. "
                    "Or use --create to create it automatically."
                )
                failed.append((rel_path, "not on server"))
            continue

        # Conflict check: has the server been edited since our last pull?
        manifest_ts = entry.get("updated_at", "")
        server_ts   = server_info.get("updated_at", "")
        if not force and manifest_ts and server_ts and manifest_ts != server_ts:
            out and out.progress_done()
            out and out.warn(
                f"{rel_path}: CONFLICT — server was modified after last pull "
                f"(pulled: {manifest_ts[:10]}, server now: {server_ts[:10]}). "
                "Skipping. Run 'voog pull' to sync, or use --force to overwrite."
            )
            conflicts.append(rel_path)
            failed.append((rel_path, "conflict"))
            continue

        if dry_run:
            succeeded.append(rel_path)
            continue

        # Read local content
        abs_path = os.path.join(site_dir, rel_path)
        try:
            with open(abs_path, encoding="utf-8") as fh:
                content = fh.read()
        except OSError as exc:
            out and out.progress_done()
            out and out.warn(f"Could not read {rel_path}: {exc}")
            failed.append((rel_path, str(exc)))
            continue

        # Upload
        is_layout  = rel_path.startswith(("layouts/", "components/"))
        asset_type = entry.get("asset_type", "")
        try:
            if is_layout:
                resp = api.update_layout(server_info["id"], content)
            elif asset_type in TEXT_ASSET_TYPES:
                resp = api.update_layout_asset(server_info["id"], content)
            else:
                # Binary assets (image, font, svg…) cannot be updated in-place
                out and out.progress_done()
                out and out.warn(
                    f"{rel_path}: binary assets cannot be pushed "
                    "(images/fonts must be updated via the Voog editor)."
                )
                failed.append((rel_path, "binary asset"))
                continue

            # Capture the new server timestamp so next push doesn't conflict
            new_ts = (resp or {}).get("updated_at", "")
            if new_ts:
                entry["updated_at"] = new_ts
                manifest_dirty = True

            succeeded.append(rel_path)

        except APIError as exc:
            out and out.progress_done()
            out and out.warn(f"Failed to push {rel_path}: {exc}")
            failed.append((rel_path, str(exc)))

    out and out.progress_done()

    # -- Create new files (--create, not in manifest) -----------------

    if to_create:
        from .new_cmd import _classify_file, _create_layout, _create_asset

        if not dry_run:
            out and out.info("Creating new file(s) on server…")

        for rel_path in to_create:
            abs_path = os.path.join(site_dir, rel_path)
            if not os.path.isfile(abs_path):
                out and out.warn(f"{rel_path}: file not found locally — skipping.")
                failed.append((rel_path, "file not found"))
                continue

            info = _classify_file(rel_path)
            if not info:
                out and out.warn(
                    f"{rel_path}: cannot infer type from path — skipping. "
                    "Expected: layouts/, components/, stylesheets/, javascripts/, images/, assets/"
                )
                failed.append((rel_path, "unknown type"))
                continue

            kind = info["kind"]
            if kind in ("layout", "component"):
                result = _create_layout(api, site_dir, info, dry_run=dry_run, out=out)
            else:
                result = _create_asset(api, site_dir, info, dry_run=dry_run, out=out)

            if result is None:
                failed.append((rel_path, "create failed"))
                continue

            if not dry_run:
                # Add the new resource to the in-memory manifest
                if kind in ("layout", "component"):
                    name = os.path.splitext(info["filename"])[0]
                    new_entry = {
                        "id": result.get("id"),
                        "title": result.get("title", name),
                        "layout_name": result.get("layout_name", name),
                        "content_type": result.get("content_type", "page"),
                        "component": kind == "component",
                        "file": rel_path,
                    }
                    if result.get("updated_at"):
                        new_entry["updated_at"] = result["updated_at"]
                    manifest.setdefault("layouts", []).append(new_entry)
                else:
                    new_entry = {
                        "id": result.get("id"),
                        "asset_type": result.get("asset_type", "unknown"),
                        "filename": info["filename"],
                        "file": rel_path,
                        "content_type": result.get("content_type", ""),
                    }
                    if result.get("updated_at"):
                        new_entry["updated_at"] = result["updated_at"]
                    manifest.setdefault("assets", []).append(new_entry)

                manifest_dirty = True

            succeeded.append(rel_path)

    # -- Refresh manifest timestamps after push -----------------------
    #
    # The PUT response may not include `updated_at`, or its format may
    # differ from what GET /admin/api/layouts returns.  Either way the
    # manifest would keep the pre-push timestamp, causing a false
    # "conflict" on every subsequent push.  Re-fetching the list gives
    # us the authoritative post-push timestamps in one round-trip.

    if not dry_run and succeeded:
        pushed_layout_files = {f for f in succeeded
                               if f.startswith(("layouts/", "components/"))}
        pushed_asset_files  = {f for f in succeeded
                               if not f.startswith(("layouts/", "components/"))}

        if pushed_layout_files:
            try:
                out and out.log("Refreshing layout timestamps after push…")
                for lay in api.get_layouts():
                    fp = layout_file_path(
                        lay.get("layout_name", ""), lay.get("component", False)
                    )
                    if fp in pushed_layout_files and fp in by_file:
                        new_ts = lay.get("updated_at", "")
                        if new_ts:
                            by_file[fp]["updated_at"] = new_ts
                            manifest_dirty = True
            except APIError as exc:
                out and out.warn(
                    f"Could not refresh layout timestamps after push: {exc}. "
                    "Run 'voog pull' before pushing again to avoid false conflicts."
                )

        if pushed_asset_files:
            try:
                out and out.log("Refreshing asset timestamps after push…")
                for asset in api.get_layout_assets():
                    fp = asset_file_path(
                        asset.get("filename", ""), asset.get("asset_type", "")
                    )
                    if fp in pushed_asset_files and fp in by_file:
                        new_ts = asset.get("updated_at", "")
                        if new_ts:
                            by_file[fp]["updated_at"] = new_ts
                            manifest_dirty = True
            except APIError as exc:
                out and out.warn(
                    f"Could not refresh asset timestamps after push: {exc}. "
                    "Run 'voog pull' before pushing again to avoid false conflicts."
                )

    # -- Save manifest + auto-commit pushed files ---------------------

    if not dry_run and succeeded:
        # Write back any updated_at timestamps (refreshed above or from PUT response)
        if manifest_dirty:
            from .manifest import save as save_manifest
            try:
                save_manifest(manifest, site_dir)
                out and out.log("Updated manifest.json with new server timestamps.")
            except OSError as exc:
                out and out.warn(f"Could not update manifest.json: {exc}")

        if git.git_available():
            try:
                git.ensure_repo(site_dir)
                commit_paths = list(succeeded)
                if manifest_dirty:
                    commit_paths.append("manifest.json")
                committed = git.commit_files(
                    site_dir,
                    commit_paths,
                    f"voog push: {len(succeeded)} file(s)",
                )
                if committed:
                    out and out.info(
                        f"\nCommitted {len(succeeded)} pushed file(s) to git."
                    )
                else:
                    out and out.log("Nothing new to commit in git after push.")
            except RuntimeError as exc:
                out and out.warn(f"Git commit after push failed: {exc}")

    # -- Summary ------------------------------------------------------

    if dry_run:
        out and out.info(f"[dry-run] Would push {len(succeeded)} file(s).")
    else:
        out and out.summary(succeeded, failed)
        if conflicts:
            out and out.info(
                f"\n{len(conflicts)} conflict(s) skipped — "
                "run 'voog pull' to sync server changes first."
            )

    return succeeded, failed
