"""Push locally modified layouts and assets to the Voog server.

Only manifest.json files are pushed. A file whose server updated_at differs
from the manifest's is a conflict and is skipped unless forced.
"""

import os

from .api import APIError
from .manifest import load, lookup_by_file, layout_file_path, asset_file_path
from . import git


TEXT_ASSET_TYPES = frozenset(("stylesheet", "javascript"))


def _create_from_entry(api, site_dir, rel_path, entry, dry_run, out):
    """Re-create a manifest-tracked file that is missing on the server.

    Returns the server response dict, or None on failure.
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
    """Push locally modified files to the Voog server.

    files: relative paths to push; None means git-changed manifest files.
    create: also create files missing from the manifest or the server.
    Returns (succeeded, failed).
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

    to_create = []
    swept_creates = []   # found by scanning, not named by the user
    skipped_creates = []

    if files:
        candidates = []
        for f in files:
            f = f.replace("\\", "/")
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
        if not git.git_available():
            out and out.error(
                "git is not available — cannot detect changed files. "
                "Specify files explicitly: voog push layouts/page.tpl"
            )
            return succeeded, failed

        changed = git.changed_files(site_dir)
        candidates = [f for f in changed if f in by_file]

        if create:
            # `git diff HEAD` omits untracked files, so scan them separately.
            # Use only untracked files: `changed` also lists deleted ones.
            from .new_cmd import _classify_file, is_publishable

            for f in git.untracked_files(site_dir):
                if f in by_file or f == "manifest.json":
                    continue
                if not _classify_file(f):
                    continue
                if not os.path.isfile(os.path.join(site_dir, f)):
                    continue
                if is_publishable(f):
                    swept_creates.append(f)
                else:
                    skipped_creates.append(f)

            to_create.extend(swept_creates)

        skipped_dev = [
            f for f in changed
            if f not in by_file and f != "manifest.json" and f not in to_create
        ]
        if skipped_dev:
            out and out.log(
                f"  ({len(skipped_dev)} non-manifest file(s) skipped: "
                + ", ".join(skipped_dev[:3])
                + ("…" if len(skipped_dev) > 3 else "") + ")"
            )

    if skipped_creates:
        out and out.info(
            f"{len(skipped_creates)} local-only file(s) not publishable to Voog:"
        )
        for f in skipped_creates:
            out and out.info(f"  - {f}")
        out and out.info(
            "  Voog takes only .tpl layouts, .css, .js, images and assets/ "
            "files, each with a flat filename.\n"
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

    # Scanned (not named) files would publish to a live site, so confirm first.
    if swept_creates and not dry_run:
        try:
            answer = input(
                f"Create {len(swept_creates)} new file(s) on the server? [y/N] "
            ).strip().lower()
        except EOFError:
            # No TTY: decline the creates but still push existing files.
            answer = "n"
            out and out.info("")
        except KeyboardInterrupt:
            out and out.info("\nAborted.")
            return succeeded, failed

        if answer not in ("y", "yes"):
            out and out.info(
                "Not creating new files; pushing changes to existing files only.\n"
            )
            to_create = [f for f in to_create if f not in swept_creates]
            swept_creates = []

    # -- Fetch server state for conflict detection --------------------

    out and out.info("Checking server state…")

    checked = candidates + to_create
    has_layouts = any(f.startswith(("layouts/", "components/")) for f in checked)
    has_assets  = any(not f.startswith(("layouts/", "components/")) for f in checked)

    server_by_file = {}  # {rel_path: {"id", "updated_at"}}

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

        manifest_ts = entry.get("updated_at", "")
        server_ts   = server_info.get("updated_at", "")
        if not force and manifest_ts and server_ts and manifest_ts != server_ts:
            out and out.progress_done()
            out and out.attention(
                f"CONFLICT — {rel_path} was NOT pushed",
                [
                    "The server was modified after your last pull.",
                    f"    pulled : {manifest_ts[:10]}",
                    f"    server : {server_ts[:10]}",
                    "Skipped to avoid overwriting server changes.",
                    "-> Run 'voog pull' to sync, or 'voog push --force' to overwrite.",
                ],
            )
            conflicts.append(rel_path)
            failed.append((rel_path, "conflict"))
            continue

        if dry_run:
            succeeded.append(rel_path)
            continue

        abs_path   = os.path.join(site_dir, rel_path)
        is_layout  = rel_path.startswith(("layouts/", "components/"))
        asset_type = entry.get("asset_type", "")
        is_binary  = not is_layout and asset_type not in TEXT_ASSET_TYPES
        try:
            if is_binary:
                with open(abs_path, "rb") as fh:
                    content = fh.read()
            else:
                with open(abs_path, encoding="utf-8") as fh:
                    content = fh.read()
        except OSError as exc:
            out and out.progress_done()
            out and out.warn(f"Could not read {rel_path}: {exc}")
            failed.append((rel_path, str(exc)))
            continue

        try:
            if is_layout:
                resp = api.update_layout(server_info["id"], content)
            elif not is_binary:
                resp = api.update_layout_asset(server_info["id"], content)
            else:
                filename     = entry.get("filename", os.path.basename(rel_path))
                content_type = entry.get("content_type", "application/octet-stream")
                resp = api.update_layout_asset_binary(
                    server_info["id"], filename, content, content_type
                )

            new_ts = (resp or {}).get("updated_at", "")
            if new_ts:
                entry["updated_at"] = new_ts
                manifest_dirty = True

            succeeded.append(rel_path)

        except APIError as exc:
            out and out.progress_done()
            if is_binary:
                # Voog returns 500 for any multipart PUT to layout_assets/:id;
                # binary content can only be created (POST), not replaced.
                out and out.warn(
                    f"Cannot replace {rel_path} — Voog's API does not support "
                    f"updating binary assets ({exc}). Re-upload it in the Voog "
                    "editor, or give the new file a different name and run "
                    f"'voog new {rel_path}'."
                )
                failed.append((rel_path, "binary update unsupported by Voog"))
            else:
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

            # Stale manifest: creating again would duplicate the layout or
            # hit the asset filename uniqueness rule.
            if rel_path in server_by_file:
                out and out.warn(
                    f"{rel_path}: already exists on the server — not creating a "
                    "duplicate. Run 'voog pull' to pick it up, then push normally."
                )
                failed.append((rel_path, "already on server"))
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
    # The PUT response's updated_at may be missing or formatted differently
    # from the list endpoint, which would cause false conflicts next push.

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
