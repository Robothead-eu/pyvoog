"""
pull.py -- Pull layouts and design assets from the Voog API to disk.

Layouts: text content from layout.body -> written as UTF-8 .tpl files.
Assets:  CSS/JS as text (from API 'data' field), images/fonts as binary
         (downloaded from public_url). Uses /admin/api/layout_assets.

The server is always the source of truth -- local files are overwritten.
Use git to review/undo changes after a pull.
"""

TEXT_ASSET_TYPES = frozenset(("stylesheet", "javascript"))

import os

from .api import APIError
from .manifest import layout_file_path, asset_file_path, build_from_api, save as save_manifest


# ------------------------------------------------------------------
# Internal file writer
# ------------------------------------------------------------------

def _write_text(abs_path, content, dry_run=False):
    if dry_run:
        return
    os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
    with open(abs_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)


def _write_binary(abs_path, data, dry_run=False):
    if dry_run:
        return
    os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
    with open(abs_path, "wb") as f:
        f.write(data)


# ------------------------------------------------------------------
# Main pull entry point
# ------------------------------------------------------------------

def pull(api, site_dir, subset=None, files=None, dry_run=False, reset=False, out=None):
    """
    Pull layouts and/or assets from the Voog API.

    api      -- VoogAPI instance
    site_dir -- absolute path to the site directory
    subset   -- None (both), 'layouts', or 'assets'
    files    -- optional list of specific relative paths to pull; when given,
                only those files are fetched (subset/reset are ignored)
    dry_run  -- show what would be written, but don't write
    reset    -- if True, also remove local files absent from the server
    out      -- Output instance for printing

    Returns (succeeded, failed) where each is a list of relative paths.
    """
    succeeded = []
    failed = []

    if files:
        return _pull_files(api, site_dir, files, dry_run=dry_run, out=out)

    if subset == "assets":
        return _pull_assets(api, site_dir, dry_run, out)

    # -- Fetch layout list from API -----------------------------------

    out and out.info("Fetching layout list...")
    try:
        layouts = api.get_layouts()
    except APIError as exc:
        out and out.error(str(exc))
        return succeeded, [("layouts", str(exc))]

    total = len(layouts)
    out and out.info(f"Pulling {total} layouts...")

    # -- Fetch body + write each layout (with progress bar) -----------

    for i, layout in enumerate(layouts, 1):
        name = layout.get("layout_name", "")
        component = layout.get("component", False)
        rel_path = layout_file_path(name, component)
        abs_path = os.path.join(site_dir, rel_path)

        out and out.progress(i, total, rel_path)

        try:
            detail = api.get_layout(layout["id"])
            body = detail.get("body", "").replace("\r\n", "\n")
        except APIError as exc:
            out and out.progress_done()
            out and out.warn(f"Failed to fetch {rel_path}: {exc}")
            failed.append((rel_path, str(exc)))
            continue

        try:
            _write_text(abs_path, body, dry_run=dry_run)
            succeeded.append(rel_path)
        except OSError as exc:
            out and out.progress_done()
            out and out.warn(f"Failed to write {rel_path}: {exc}")
            failed.append((rel_path, str(exc)))

    out and out.progress_done()

    # -- Save updated manifest ----------------------------------------

    if not dry_run and layouts:
        try:
            manifest = _load_or_empty(site_dir)
            manifest["layouts"] = build_from_api(layouts, [])["layouts"]
            save_manifest(manifest, site_dir)
            out and out.log("Updated manifest.json (layouts).")
        except OSError as exc:
            out and out.warn(f"Could not write manifest.json: {exc}")

    # -- Handle --reset (remove orphaned .tpl files) ------------------

    if reset and not dry_run:
        remote_paths = {
            layout_file_path(l.get("layout_name", ""), l.get("component", False))
            for l in layouts
        }
        _remove_orphaned_layouts(site_dir, remote_paths, out)

    # -- Also pull assets when pulling everything ---------------------

    if subset is None:
        asset_ok, asset_fail = _pull_assets(api, site_dir, dry_run, out)
        succeeded.extend(asset_ok)
        failed.extend(asset_fail)

    return succeeded, failed


# ------------------------------------------------------------------
# Reset helper (layouts only)
# ------------------------------------------------------------------

def _remove_orphaned_layouts(site_dir, keep_paths, out):
    """Remove local .tpl files that are not in keep_paths."""
    removed = []
    for d in ("layouts", "components"):
        abs_dir = os.path.join(site_dir, d)
        if not os.path.isdir(abs_dir):
            continue
        for fname in os.listdir(abs_dir):
            if not fname.endswith(".tpl"):
                continue
            rel = f"{d}/{fname}"
            abs_path = os.path.join(abs_dir, fname)
            if rel not in keep_paths:
                os.remove(abs_path)
                removed.append(rel)
                out and out.log(f"Removed orphaned: {rel}")
    if removed:
        out and out.info(f"  Removed {len(removed)} orphaned local file(s).")


# ------------------------------------------------------------------
# Asset pull
# ------------------------------------------------------------------

def _pull_assets(api, site_dir, dry_run, out):
    """
    Pull design assets (CSS, JS, images, fonts) from the Voog API.

    Text assets (stylesheet, javascript): fetched individually for their
    'data' field, written as UTF-8.
    Binary assets (image, font, svg, etc.): downloaded from public_url.

    Returns (succeeded, failed).
    """
    succeeded = []
    failed = []

    out and out.info("Fetching layout asset list...")
    try:
        assets = api.get_layout_assets()
    except APIError as exc:
        out and out.error(str(exc))
        return succeeded, [("assets", str(exc))]

    total = len(assets)
    out and out.info(f"Pulling {total} assets...")

    for i, asset in enumerate(assets, 1):
        filename = asset.get("filename", "")
        asset_type = asset.get("asset_type", "unknown")
        rel_path = asset_file_path(filename, asset_type)
        abs_path = os.path.join(site_dir, rel_path)

        out and out.progress(i, total, rel_path)

        try:
            if asset_type in TEXT_ASSET_TYPES:
                detail = api.get_layout_asset(asset["id"])
                body = detail.get("data", "").replace("\r\n", "\n")
                _write_text(abs_path, body, dry_run=dry_run)
            else:
                data = api.download_url(asset["public_url"])
                _write_binary(abs_path, data, dry_run=dry_run)
            succeeded.append(rel_path)
        except (APIError, OSError) as exc:
            out and out.progress_done()
            out and out.warn(f"Failed: {rel_path}: {exc}")
            failed.append((rel_path, str(exc)))

    out and out.progress_done()

    # Update manifest with asset entries
    if not dry_run and assets:
        try:
            manifest = _load_or_empty(site_dir)
            manifest["assets"] = build_from_api([], assets)["assets"]
            save_manifest(manifest, site_dir)
            out and out.log("Updated manifest.json (assets).")
        except OSError as exc:
            out and out.warn(f"Could not write manifest.json: {exc}")

    return succeeded, failed


# ------------------------------------------------------------------
# Single-file pull
# ------------------------------------------------------------------

def _pull_files(api, site_dir, files, dry_run=False, out=None):
    """
    Pull only the specified relative file paths from the server.

    Each path is matched against the server's layout/asset list by its
    computed file path. The matching manifest entry is refreshed (id +
    updated_at) so a later push doesn't see a false conflict.

    Returns (succeeded, failed).
    """
    succeeded = []
    failed = []

    want_layouts = any(f.startswith(("layouts/", "components/")) for f in files)
    want_assets  = any(not f.startswith(("layouts/", "components/")) for f in files)

    server_layouts = {}  # rel_path -> layout dict
    server_assets  = {}   # rel_path -> asset dict

    try:
        if want_layouts:
            out and out.info("Fetching layout list...")
            for lay in api.get_layouts():
                fp = layout_file_path(
                    lay.get("layout_name", ""), lay.get("component", False)
                )
                server_layouts[fp] = lay
        if want_assets:
            out and out.info("Fetching layout asset list...")
            for asset in api.get_layout_assets():
                fp = asset_file_path(
                    asset.get("filename", ""), asset.get("asset_type", "")
                )
                server_assets[fp] = asset
    except APIError as exc:
        out and out.error(str(exc))
        return succeeded, [(f, str(exc)) for f in files]

    manifest = _load_or_empty(site_dir)
    manifest_dirty = False

    total = len(files)
    out and out.info(f"Pulling {total} file(s)...")

    for i, rel_path in enumerate(files, 1):
        out and out.progress(i, total, rel_path)
        abs_path = os.path.join(site_dir, rel_path)

        try:
            if rel_path in server_layouts:
                lay = server_layouts[rel_path]
                detail = api.get_layout(lay["id"])
                body = detail.get("body", "").replace("\r\n", "\n")
                _write_text(abs_path, body, dry_run=dry_run)
                if not dry_run:
                    manifest_dirty |= _refresh_manifest_entry(
                        manifest, "layouts", rel_path, [lay], []
                    )
                succeeded.append(rel_path)

            elif rel_path in server_assets:
                asset = server_assets[rel_path]
                asset_type = asset.get("asset_type", "unknown")
                if asset_type in TEXT_ASSET_TYPES:
                    detail = api.get_layout_asset(asset["id"])
                    body = detail.get("data", "").replace("\r\n", "\n")
                    _write_text(abs_path, body, dry_run=dry_run)
                else:
                    data = api.download_url(asset["public_url"])
                    _write_binary(abs_path, data, dry_run=dry_run)
                if not dry_run:
                    manifest_dirty |= _refresh_manifest_entry(
                        manifest, "assets", rel_path, [], [asset]
                    )
                succeeded.append(rel_path)

            else:
                out and out.progress_done()
                out and out.warn(
                    f"{rel_path}: not found on server — skipping. "
                    "Check the path, or run 'voog pull' to list everything."
                )
                failed.append((rel_path, "not on server"))

        except (APIError, OSError) as exc:
            out and out.progress_done()
            out and out.warn(f"Failed: {rel_path}: {exc}")
            failed.append((rel_path, str(exc)))

    out and out.progress_done()

    if not dry_run and manifest_dirty:
        try:
            save_manifest(manifest, site_dir)
            out and out.log("Updated manifest.json.")
        except OSError as exc:
            out and out.warn(f"Could not write manifest.json: {exc}")

    return succeeded, failed


def _refresh_manifest_entry(manifest, key, rel_path, layouts, assets):
    """
    Replace (or append) the manifest entry for rel_path under manifest[key],
    rebuilt from the API data. Returns True (manifest changed).
    """
    built = build_from_api(layouts, assets)[key]
    if not built:
        return False
    new_entry = built[0]
    entries = manifest.setdefault(key, [])
    for idx, entry in enumerate(entries):
        if entry.get("file") == rel_path:
            entries[idx] = new_entry
            return True
    entries.append(new_entry)
    return True


def _load_or_empty(site_dir):
    """Load existing manifest.json or return a blank one."""
    path = os.path.join(site_dir, "manifest.json")
    if os.path.isfile(path):
        import json
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"layouts": [], "assets": []}
