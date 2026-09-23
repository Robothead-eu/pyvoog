"""Build, load, save and display manifest.json (voog-kit compatible format)."""

import json
import os


# ------------------------------------------------------------------
# Path resolution — same logic the Ruby kit uses
# ------------------------------------------------------------------

def layout_file_path(layout_name, component):
    """Return the relative file path for a layout/component."""
    if component:
        return f"components/{layout_name}.tpl"
    return f"layouts/{layout_name}.tpl"


# Voog asset_type -> local directory; other types go to assets/.
ASSET_DIR_MAP = {
    "stylesheet": "stylesheets",
    "javascript": "javascripts",
    "image": "images",
}


def asset_file_path(filename, asset_type):
    """Return the relative file path for an asset based on its asset_type."""
    folder = ASSET_DIR_MAP.get((asset_type or "").lower(), "assets")
    return f"{folder}/{filename}"


# ------------------------------------------------------------------
# Build manifest from API data
# ------------------------------------------------------------------

def build_from_api(layouts, assets):
    """Build a manifest from API layout and layout_asset lists.

    id and updated_at are stored so push can detect conflicts without extra lookups.
    """
    layout_entries = []
    for layout in layouts:
        name = layout.get("layout_name", "")
        component = layout.get("component", False)
        entry = {
            "id": layout.get("id"),
            "title": layout.get("title", name),
            "layout_name": name,
            "content_type": layout.get("content_type", "page"),
            "component": component,
            "file": layout_file_path(name, component),
        }
        if "updated_at" in layout:
            entry["updated_at"] = layout["updated_at"]
        layout_entries.append(entry)

    asset_entries = []
    for asset in assets:
        filename = asset.get("filename", "")
        asset_type = asset.get("asset_type", "unknown")
        entry = {
            "id": asset.get("id"),
            "asset_type": asset_type,
            "filename": filename,
            "file": asset_file_path(filename, asset_type),
            "content_type": asset.get("content_type", ""),
        }
        if "updated_at" in asset:
            entry["updated_at"] = asset["updated_at"]
        asset_entries.append(entry)

    return {"layouts": layout_entries, "assets": asset_entries}


def lookup_by_file(manifest):
    """Map file path -> manifest entry across layouts and assets."""
    result = {}
    for entry in manifest.get("layouts", []):
        if "file" in entry:
            result[entry["file"]] = entry
    for entry in manifest.get("assets", []):
        if "file" in entry:
            result[entry["file"]] = entry
    return result


# ------------------------------------------------------------------
# Load / save
# ------------------------------------------------------------------

def load(site_dir):
    """Load manifest.json from site_dir, or None if it doesn't exist."""
    path = os.path.join(site_dir, "manifest.json")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(manifest, site_dir):
    """Write manifest.json to site_dir (overwrites)."""
    path = os.path.join(site_dir, "manifest.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.write("\n")


# ------------------------------------------------------------------
# Display
# ------------------------------------------------------------------

def display(manifest, out, verbose=False):
    """Print a summary (and optionally full file list) of a manifest."""
    layouts = manifest.get("layouts", [])
    assets = manifest.get("assets", [])

    components = [l for l in layouts if l.get("component")]
    page_layouts = [l for l in layouts if not l.get("component")]

    out.info(f"  Layouts:    {len(page_layouts)}")
    out.info(f"  Components: {len(components)}")
    out.info(f"  Assets:     {len(assets)}")

    if verbose:
        if page_layouts:
            out.info("\n  Layouts:")
            for entry in sorted(page_layouts, key=lambda x: x.get("file", "")):
                out.info(f"    {entry['file']}")
        if components:
            out.info("\n  Components:")
            for entry in sorted(components, key=lambda x: x.get("file", "")):
                out.info(f"    {entry['file']}")
        if assets:
            out.info("\n  Assets:")
            for entry in sorted(assets, key=lambda x: x.get("file", "")):
                out.info(f"    {entry['file']}")

