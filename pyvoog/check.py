"""Compare local layouts and assets with the remote site (read-only)."""

import os

from .api import APIError
from .manifest import layout_file_path, asset_file_path

TEXT_ASSET_TYPES = frozenset(("stylesheet", "javascript"))
ASSET_DIRS = ("stylesheets", "javascripts", "images", "assets")


def check(api, site_dir, out=None):
    """Compare remote layouts and assets with local files.

    Returns {"layouts": {missing, modified, in_sync, extra},
             "assets": {missing, present, extra}, "error": str or None},
    each category a list of relative paths.
    """
    result = {
        "layouts": {"missing": [], "modified": [], "in_sync": [], "extra": []},
        "assets":  {"missing": [], "present": [], "extra": []},
        "error": None,
    }

    # -- Layouts -------------------------------------------------------

    out and out.log("Fetching layout list…")
    try:
        layouts = api.get_layouts()
    except APIError as exc:
        result["error"] = str(exc)
        return result

    total = len(layouts)
    remote_layout_paths = set()
    for i, layout in enumerate(layouts, 1):
        name = layout.get("layout_name", "")
        component = layout.get("component", False)
        rel_path = layout_file_path(name, component)
        abs_path = os.path.join(site_dir, rel_path)
        remote_layout_paths.add(rel_path)

        out and out.progress(i, total, rel_path)

        if not os.path.isfile(abs_path):
            result["layouts"]["missing"].append(rel_path)
        else:
            try:
                detail = api.get_layout(layout["id"])
                body = detail.get("body", "")
            except APIError as exc:
                out and out.progress_done()
                out and out.warn(f"Could not fetch {rel_path}: {exc}")
                continue
            with open(abs_path, encoding="utf-8") as f:
                local_body = f.read()
            if local_body.replace("\r\n", "\n") == body.replace("\r\n", "\n"):
                result["layouts"]["in_sync"].append(rel_path)
            else:
                result["layouts"]["modified"].append(rel_path)

    out and out.progress_done()

    for d in ("layouts", "components"):
        abs_dir = os.path.join(site_dir, d)
        if not os.path.isdir(abs_dir):
            continue
        for fname in sorted(os.listdir(abs_dir)):
            if fname.endswith(".tpl"):
                rel = f"{d}/{fname}"
                if rel not in remote_layout_paths:
                    result["layouts"]["extra"].append(rel)

    # -- Assets --------------------------------------------------------

    out and out.log("Fetching layout asset list…")
    try:
        assets = api.get_layout_assets()
    except APIError as exc:
        out and out.warn(f"Could not fetch assets: {exc}")
        return result

    remote_asset_paths = set()
    total_assets = len(assets)
    for i, asset in enumerate(assets, 1):
        filename = asset.get("filename", "")
        asset_type = asset.get("asset_type", "unknown")
        rel_path = asset_file_path(filename, asset_type)
        abs_path = os.path.join(site_dir, rel_path)
        remote_asset_paths.add(rel_path)

        out and out.progress(i, total_assets, rel_path)

        if os.path.isfile(abs_path):
            result["assets"]["present"].append(rel_path)
        else:
            result["assets"]["missing"].append(rel_path)

    out and out.progress_done()

    for d in ASSET_DIRS:
        abs_dir = os.path.join(site_dir, d)
        if not os.path.isdir(abs_dir):
            continue
        for fname in sorted(os.listdir(abs_dir)):
            rel = f"{d}/{fname}"
            if rel not in remote_asset_paths:
                result["assets"]["extra"].append(rel)

    return result


def display_check_result(result, out):
    """Print the check result in a readable format."""
    layouts = result["layouts"]
    assets = result["assets"]

    if result.get("error"):
        out.error(result["error"])
        return

    total_layouts = (
        len(layouts["missing"])
        + len(layouts["modified"])
        + len(layouts["in_sync"])
    )
    out.info(f"\nLayouts ({total_layouts} on server):")
    if layouts["missing"]:
        out.info(f"  Missing locally ({len(layouts['missing'])}):")
        for f in layouts["missing"]:
            out.info(f"    - {f}")
    if layouts["modified"]:
        out.info(f"  Modified locally ({len(layouts['modified'])}) — local differs from server:")
        for f in layouts["modified"]:
            out.info(f"    ~ {f}")
    if layouts["extra"]:
        out.info(f"  Extra locally ({len(layouts['extra'])}) — not on server:")
        for f in layouts["extra"]:
            out.info(f"    + {f}")
    in_sync_count = len(layouts["in_sync"])
    out.info(f"  In sync: {in_sync_count}")

    total_assets = len(assets["missing"]) + len(assets["present"])
    if total_assets or assets["extra"]:
        out.info(f"\nAssets ({total_assets} on server):")
        if assets["missing"]:
            out.info(f"  Missing locally ({len(assets['missing'])}):")
            for f in assets["missing"]:
                out.info(f"    - {f}")
        if assets["extra"]:
            out.info(f"  Extra locally ({len(assets['extra'])}) — not on server:")
            for f in assets["extra"]:
                out.info(f"    + {f}")
        out.info(f"  Present: {len(assets['present'])}")

    total_issues = (
        len(layouts["missing"])
        + len(layouts["modified"])
        + len(assets["missing"])
    )
    out.info("")
    if total_issues == 0:
        out.info("Everything is in sync.")
    else:
        out.info(
            f"{total_issues} issue(s) found. "
            "Run  pyvoog pull  to sync from server."
        )
