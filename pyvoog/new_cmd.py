"""Create new layouts and assets on the Voog server (`pyvoog new`)."""

import os
import re
import sys

from .api import APIError
from .manifest import (
    layout_file_path, asset_file_path, build_from_api,
    load as load_manifest, save as save_manifest, lookup_by_file,
)


TEXT_ASSET_TYPES = frozenset(("stylesheet", "javascript"))

DIR_TO_ASSET_TYPE = {
    "stylesheets": "stylesheet",
    "javascripts": "javascript",
    "images": "image",
    "assets": "unknown",
}

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
    """Classify a relative path by its top-level site directory.

    Returns {kind, rel_path, filename, dir}, kind being 'layout', 'component'
    or 'asset'; None if the directory is not a site directory.
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


_JUNK_SUFFIXES = (".bak", ".orig", ".rej", ".swp", ".swo", ".tmp")

# As in the Ruby kit, SVG is an asset, not an image.
_IMAGE_EXTENSIONS = frozenset(
    (".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".avif",
     ".tif", ".tiff", ".heic", ".heif")
)


def is_publishable(rel_path):
    """Return True if a scanned file may be created on the server.

    Used by bulk creates only; explicitly named files bypass it. Folder rules
    mirror the Ruby kit's valid_for_folder?.
    """
    info = _classify_file(rel_path)
    if not info:
        return False

    # Voog names are flat; a nested path would create a layout literally
    # named "partials/nested".
    if "/" in info["filename"]:
        return False

    basename = os.path.basename(info["filename"])
    if basename.startswith("."):          # .DS_Store, .page.tpl.swp
        return False
    if basename.endswith("~"):
        return False

    lower = basename.lower()
    if lower.endswith(_JUNK_SUFFIXES):
        return False

    directory = info["dir"]
    if directory in ("layouts", "components"):
        # Exactly one dot, and it is .tpl (Ruby kit rule).
        return re.fullmatch(r"[^.]+\.tpl", basename) is not None
    if directory == "stylesheets":
        return lower.endswith(".css")
    if directory == "javascripts":
        return lower.endswith(".js")
    if directory == "images":
        return os.path.splitext(lower)[1] in _IMAGE_EXTENSIONS
    return True


def _guess_content_type(filename):
    """Guess a MIME type from the file extension."""
    ext = os.path.splitext(filename)[1].lower()
    return EXTENSION_CONTENT_TYPES.get(ext, "application/octet-stream")


def _is_text_asset_dir(directory):
    """Return True if the directory holds text assets."""
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
            with open(abs_path, encoding="utf-8") as f:
                data = f.read()
            result = api.create_layout_asset(
                filename=filename, data=data, content_type=content_type,
            )
        else:
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

def _report_skipped(skipped, out):
    """Report local-only files that were not offered for creation."""
    if not skipped or not out:
        return
    out.info(f"\n{len(skipped)} local file(s) skipped (not publishable to Voog):")
    for rel in skipped:
        out.info(f"  - {rel}")
    out.info(
        "  Voog takes only .tpl layouts, .css, .js, images and assets/ files, "
        "each with a flat filename."
    )
    if any(f.startswith("images/") and f.lower().endswith(".svg") for f in skipped):
        out.info("  An SVG belongs in assets/, not images/ — move it there.")
    out.info("  Name a file explicitly to override:  pyvoog new <file>")


def _find_new_files(site_dir, server_layout_paths, server_asset_paths):
    """Find local files that are not on the server.

    Returns (new_files, skipped): _classify_file dicts, and relative paths
    that are not publishable.
    """
    new_files = []
    skipped = []

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

            if info["kind"] in ("layout", "component"):
                if rel_path in server_layout_paths:
                    continue
            elif rel_path in server_asset_paths:
                continue

            if is_publishable(rel_path):
                new_files.append(info)
            else:
                skipped.append(rel_path)

    return new_files, skipped


# ------------------------------------------------------------------
# Main entry points
# ------------------------------------------------------------------

def new_single(api, site_dir, file_path, content_type_override=None, dry_run=False, out=None):
    """Create a single new file on the server. Returns True on success."""
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

    if info["kind"] in ("layout", "component"):
        result = _create_layout(api, site_dir, info, content_type_override, dry_run, out)
    else:
        result = _create_asset(api, site_dir, info, dry_run, out)

    if result is None:
        return False

    if dry_run:
        return True

    _update_manifest_after_create(api, site_dir, out)
    return True


def list_new(api, site_dir, out=None):
    """List local files not on the server. Returns _classify_file dicts."""
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

    new_files, skipped = _find_new_files(
        site_dir, server_layout_paths, server_asset_paths
    )

    if not new_files:
        out and out.info("\nNo new local files. Everything is on the server.")
    else:
        out and out.info(f"\n{len(new_files)} new local file(s) not on server:")
        for info in new_files:
            out and out.info(f"  + {info['rel_path']}  ({info['kind']})")
        out and out.info(f"\nUse  pyvoog new <file>  or  pyvoog new --all  to create them.")

    _report_skipped(skipped, out)
    return new_files


def new_all(api, site_dir, dry_run=False, out=None):
    """Create all local files not on the server, after confirmation.

    Returns (succeeded, failed).
    """
    succeeded = []
    failed = []

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

    new_files, skipped = _find_new_files(
        site_dir, server_layout_paths, server_asset_paths
    )
    _report_skipped(skipped, out)

    if not new_files:
        out and out.info("No new local files to create on the server.")
        return succeeded, failed

    out and out.info(f"\n{len(new_files)} new file(s) to create on server:")
    for info in new_files:
        kind_label = info["kind"]
        out and out.info(f"  + {info['rel_path']}  ({kind_label})")

    if dry_run:
        out and out.info(f"\n[dry-run] Would create {len(new_files)} file(s).")
        return [f["rel_path"] for f in new_files], failed

    out and out.info("")
    try:
        answer = input("Proceed? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        out and out.info("\nAborted.")
        return succeeded, failed

    if answer not in ("y", "yes"):
        out and out.info("Aborted.")
        return succeeded, failed

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

    if succeeded:
        _update_manifest_after_create(api, site_dir, out)

    return succeeded, failed


def _update_manifest_after_create(api, site_dir, out):
    """Rebuild manifest.json from the server after creating files."""
    try:
        layouts = api.get_layouts()
        assets = api.get_layout_assets()
        manifest = build_from_api(layouts, assets)
        save_manifest(manifest, site_dir)
        out and out.log("Updated manifest.json.")
    except (APIError, OSError) as exc:
        out and out.warn(f"Could not update manifest: {exc}")
