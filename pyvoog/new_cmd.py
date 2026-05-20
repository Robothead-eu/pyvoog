"""
new_cmd.py — Create new layouts and assets on the Voog server.

Supports:
  pyvoog new <file>            — create a single file on the server
  pyvoog new --all [--dry-run] — find all local-only files and create them

Layout content_type defaults:
  layouts/    → content_type='page',      component=False
  components/ → content_type='component', component=True

Override with --type for special layouts (blog, blog_article, etc.).
"""

import os
import sys

from .api import APIError
from .manifest import (
    layout_file_path, asset_file_path, build_from_api,
    load as load_manifest, save as save_manifest, lookup_by_file,
)


TEXT_ASSET_TYPES = frozenset(("stylesheet", "javascript"))

# Map local directory → asset_type for the API
DIR_TO_ASSET_TYPE = {
    "stylesheets": "stylesheet",
    "javascripts": "javascript",
    "images": "image",
    "assets": "unknown",
}

# MIME types for common extensions
EXTENSION_CONTENT_TYPES = {
    ".css": "text/css",
    ".js": "text/javascript",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".eot": "application/vnd.ms-fontobject",
    ".otf": "font/otf",
}


def _classify_file(rel_path):
    """
    Classify a file path into its type.

    Returns a dict:
      kind       — 'layout', 'component', or 'asset'
      rel_path   — normalised relative path
      filename   — just the filename part
      dir        — the directory prefix (layouts, components, stylesheets, etc.)
    Or None if the path doesn't match a known directory.
    """
    rel_path = rel_path.replace("\\", "/")
    parts = rel_path.split("/", 1)
    if len(parts) != 2:
        return None

    directory, filename = parts[0], parts[1]

    if directory == "layouts":
        return {"kind": "layout", "rel_path": rel_path, "filename": filename, "dir": directory}
    elif directory == "components":
        return {"kind": "component", "rel_path": rel_path, "filename": filename, "dir": directory}
    elif directory in DIR_TO_ASSET_TYPE:
        return {"kind": "asset", "rel_path": rel_path, "filename": filename, "dir": directory}
    return None


def _guess_content_type(filename):
    """Guess MIME content type from file extension."""
    ext = os.path.splitext(filename)[1].lower()
    return EXTENSION_CONTENT_TYPES.get(ext, "application/octet-stream")


def _is_text_asset_dir(directory):
    """Return True if the directory holds text-editable assets."""
    return directory in ("stylesheets", "javascripts")


# ------------------------------------------------------------------
# Create a single file on the server
# ------------------------------------------------------------------

def _create_layout(api, site_dir, info, content_type_override=None, dry_run=False, out=None):
    """Create a layout or component on the server."""
    rel_path = info["rel_path"]
    abs_path = os.path.join(site_dir, rel_path)

    name = os.path.splitext(info["filename"])[0]
    is_component = info["kind"] == "component"

    if is_component:
        title = name
        content_type = "component"
    else:
        title = name.replace("_", " ").capitalize()
        content_type = content_type_override or "page"

    # Read local body
    try:
        with open(abs_path, encoding="utf-8") as f:
            body = f.read()
    except OSError as exc:
        out and out.error(f"Could not read {rel_path}: {exc}")
        return None

    if dry_run:
        out and out.info(f"  Would create {rel_path}  (title={title!r}, content_type={content_type!r}, component={is_component})")
        return {"dry_run": True}

    out and out.info(f"  Creating {rel_path}...")
    try:
        result = api.create_layout(
            title=title,
            content_type=content_type,
            body=body,
            component=is_component,
            layout_name=name,
        )
        out and out.info(f"    Created (id={result.get('id')})")
        return result
    except APIError as exc:
        out and out.error(f"    Failed to create {rel_path}: {exc}")
        return None


def _create_asset(api, site_dir, info, dry_run=False, out=None):
    """Create an asset (text or binary) on the server."""
    rel_path = info["rel_path"]
    abs_path = os.path.join(site_dir, rel_path)
    filename = info["filename"]
    content_type = _guess_content_type(filename)

    if dry_run:
        out and out.info(f"  Would create {rel_path}  (content_type={content_type!r})")
        return {"dry_run": True}

    out and out.info(f"  Creating {rel_path}...")

    try:
        if _is_text_asset_dir(info["dir"]):
            # Text asset — send content as string
            with open(abs_path, encoding="utf-8") as f:
                data = f.read()
            result = api.create_layout_asset(
                filename=filename, data=data, content_type=content_type,
            )
        else:
            # Binary asset — send raw bytes via multipart
            with open(abs_path, "rb") as f:
                file_bytes = f.read()
            result = api.create_layout_asset(
                filename=filename, file_bytes=file_bytes, content_type=content_type,
            )
        out and out.info(f"    Created (id={result.get('id')})")
        return result
    except APIError as exc:
        out and out.error(f"    Failed to create {rel_path}: {exc}")
        return None


# ------------------------------------------------------------------
# Find local files not on the server
# ------------------------------------------------------------------

def _find_new_files(site_dir, server_layout_paths, server_asset_paths):
    """
    Scan local directories for files that exist locally but not on the server.
    Returns a list of classified file info dicts.
    """
    new_files = []

    dirs_to_scan = ["layouts", "components", "stylesheets", "javascripts", "images", "assets"]
    for d in dirs_to_scan:
        abs_dir = os.path.join(site_dir, d)
        if not os.path.isdir(abs_dir):
            continue
        for fname in sorted(os.listdir(abs_dir)):
            abs_path = os.path.join(abs_dir, fname)
            if os.path.isdir(abs_path):
                continue
            rel_path = f"{d}/{fname}"
            info = _classify_file(rel_path)
            if not info:
                continue

            # Check if already on server
            if info["kind"] in ("layout", "component"):
                if rel_path not in server_layout_paths:
                    new_files.append(info)
            else:
                if rel_path not in server_asset_paths:
                    new_files.append(info)

    return new_files


# ------------------------------------------------------------------
# Main entry points
# ------------------------------------------------------------------

def new_single(api, site_dir, file_path, content_type_override=None, dry_run=False, out=None):
    """
    Create a single new file on the server.
    Returns True on success, False on failure.
    """
    rel_path = file_path.replace("\\", "/")
    abs_path = os.path.join(site_dir, rel_path)

    if not os.path.isfile(abs_path):
        out and out.error(f"File not found: {rel_path}")
        return False

    info = _classify_file(rel_path)
    if not info:
        out and out.error(
            f"Cannot determine file type from path: {rel_path}\n"
            "Expected: layouts/*.tpl, components/*.tpl, stylesheets/*, javascripts/*, images/*, assets/*"
        )
        return False

    # Create on server
    if info["kind"] in ("layout", "component"):
        result = _create_layout(api, site_dir, info, content_type_override, dry_run, out)
    else:
        result = _create_asset(api, site_dir, info, dry_run, out)

    if result is None:
        return False

    if dry_run:
        return True

    # Update manifest
    _update_manifest_after_create(api, site_dir, out)
    return True


def list_new(api, site_dir, out=None):
    """
    List local files that don't exist on the server.
    Fast — only compares file paths, no content fetching.
    Returns the list of info dicts.
    """
    out and out.info("Fetching server layouts...")
    try:
        server_layouts = api.get_layouts()
    except APIError as exc:
        out and out.error(f"Could not fetch layouts: {exc}")
        return []

    out and out.info("Fetching server assets...")
    try:
        server_assets = api.get_layout_assets()
    except APIError as exc:
        out and out.error(f"Could not fetch assets: {exc}")
        return []

    server_layout_paths = set()
    for lay in server_layouts:
        name = lay.get("layout_name", "")
        component = lay.get("component", False)
        server_layout_paths.add(layout_file_path(name, component))

    server_asset_paths = set()
    for asset in server_assets:
        server_asset_paths.add(
            asset_file_path(asset.get("filename", ""), asset.get("asset_type", ""))
        )

    new_files = _find_new_files(site_dir, server_layout_paths, server_asset_paths)

    if not new_files:
        out and out.info("\nNo new local files. Everything is on the server.")
    else:
        out and out.info(f"\n{len(new_files)} new local file(s) not on server:")
        for info in new_files:
            out and out.info(f"  + {info['rel_path']}  ({info['kind']})")
        out and out.info(f"\nUse  pyvoog new <file>  or  pyvoog new --all  to create them.")

    return new_files


def new_all(api, site_dir, dry_run=False, out=None):
    """
    Find all local files not on the server and create them.
    Asks for confirmation before proceeding.
    Returns (succeeded, failed) lists.
    """
    succeeded = []
    failed = []

    # Fetch server state
    out and out.info("Fetching server layouts...")
    try:
        server_layouts = api.get_layouts()
    except APIError as exc:
        out and out.error(f"Could not fetch layouts: {exc}")
        return succeeded, failed

    out and out.info("Fetching server assets...")
    try:
        server_assets = api.get_layout_assets()
    except APIError as exc:
        out and out.error(f"Could not fetch assets: {exc}")
        return succeeded, failed

    # Build sets of server file paths
    server_layout_paths = set()
    for lay in server_layouts:
        name = lay.get("layout_name", "")
        component = lay.get("component", False)
        server_layout_paths.add(layout_file_path(name, component))

    server_asset_paths = set()
    for asset in server_assets:
        server_asset_paths.add(
            asset_file_path(asset.get("filename", ""), asset.get("asset_type", ""))
        )

    # Find new local files
    new_files = _find_new_files(site_dir, server_layout_paths, server_asset_paths)

    if not new_files:
        out and out.info("No new local files to create on the server.")
        return succeeded, failed

    # Show what will be created
    out and out.info(f"\n{len(new_files)} new file(s) to create on server:")
    for info in new_files:
        kind_label = info["kind"]
        out and out.info(f"  + {info['rel_path']}  ({kind_label})")

    if dry_run:
        out and out.info(f"\n[dry-run] Would create {len(new_files)} file(s).")
        return [f["rel_path"] for f in new_files], failed

    # Ask for confirmation
    out and out.info("")
    try:
        answer = input("Proceed? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        out and out.info("\nAborted.")
        return succeeded, failed

    if answer not in ("y", "yes"):
        out and out.info("Aborted.")
        return succeeded, failed

    # Create each file
    out and out.info("")
    for info in new_files:
        if info["kind"] in ("layout", "component"):
            result = _create_layout(api, site_dir, info, dry_run=False, out=out)
        else:
            result = _create_asset(api, site_dir, info, dry_run=False, out=out)

        if result:
            succeeded.append(info["rel_path"])
        else:
            failed.append(info["rel_path"])

    # Update manifest once after all creates
    if succeeded:
        _update_manifest_after_create(api, site_dir, out)

    return succeeded, failed


def _update_manifest_after_create(api, site_dir, out):
    """Re-fetch server state and rebuild manifest after creating files."""
    try:
        layouts = api.get_layouts()
        assets = api.get_layout_assets()
        manifest = build_from_api(layouts, assets)
        save_manifest(manifest, site_dir)
        out and out.log("Updated manifest.json.")
    except (APIError, OSError) as exc:
        out and out.warn(f"Could not update manifest: {exc}")
