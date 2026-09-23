"""
content.py — `pyvoog pull content`: site content to .voog-content/.

Format: voog-content v1 (docs/voog-server.md). Gitignored, never committed.
"""

import datetime
import http.client
import json
import os
import shutil

from . import __version__
from .api import APIError


FORMAT_NAME = "voog-content"
FORMAT_VERSION = 1

CONTENT_DIR = ".voog-content"
TMP_DIR = CONTENT_DIR + ".tmp"
OLD_DIR = CONTENT_DIR + ".old"

API = "/admin/api"
PER_PAGE = 250
MAX_PAGES = 400  # backstop against endpoints that ignore ?page=

# Removed at every depth before writing.
STRIPPED_KEYS = frozenset((
    "api_token",
    "email",
    "reply_email", "reply_email_with_idn",
    "notification_email", "notification_email_with_idn",
    "bank_details",
))

# Written as empty and listed in the manifest; any other error fails the pull.
UNAVAILABLE_STATUSES = frozenset((401, 403, 404))


class ContentPullError(Exception):
    pass


class _Unavailable(Exception):
    def __init__(self, endpoint, status):
        super().__init__(f"{endpoint}: HTTP {status}")
        self.endpoint = endpoint
        self.status = status


# ------------------------------------------------------------------
# Fetching
# ------------------------------------------------------------------

class _Fetcher:
    """Paginates and classifies failures."""

    def __init__(self, api):
        self.api = api
        self.unavailable = []

    def _get(self, path):
        try:
            return self.api.get_json(path)
        except APIError as exc:
            if exc.status_code in UNAVAILABLE_STATUSES:
                raise _Unavailable(path.split("?", 1)[0], exc.status_code)
            raise ContentPullError(str(exc)) from exc
        except ValueError as exc:
            raise ContentPullError(f"Invalid JSON from {path}: {exc}") from exc
        except (OSError, http.client.HTTPException) as exc:
            # Timeouts and truncated reads aren't URLError; VoogAPI doesn't catch them.
            raise ContentPullError(f"Network error on {path}: {exc!r}") from exc

    def get(self, path):
        """GET one object."""
        return self._get(API + path)

    def get_all(self, path):
        """
        GET a whole collection. Repeated ids are dropped (an item created
        mid-pull shifts pages); a page with nothing new ends the loop.
        """
        sep = "&" if "?" in path else "?"
        items, seen = [], set()
        for page in range(1, MAX_PAGES + 1):
            data = self._get(f"{API}{path}{sep}per_page={PER_PAGE}&page={page}")
            if not isinstance(data, list):
                raise ContentPullError(
                    f"Expected a list from {API}{path}, got {type(data).__name__}"
                )
            new = []
            for o in data:
                oid = o.get("id") if isinstance(o, dict) else None
                if oid is not None:
                    if oid in seen:
                        continue
                    seen.add(oid)
                new.append(o)
            items.extend(new)
            if not new or len(data) < PER_PAGE:
                return items
        raise ContentPullError(f"{API}{path}: more than {MAX_PAGES} pages — giving up")

    def record(self, exc):
        self.unavailable.append({"endpoint": exc.endpoint, "status": exc.status})

    def all_or_empty(self, path):
        """get_all(), but an unavailable endpoint is recorded and yields []."""
        try:
            return self.get_all(path)
        except _Unavailable as exc:
            self.record(exc)
            return []

    def one_or_empty(self, path):
        """get(), but an unavailable endpoint is recorded and yields {}."""
        try:
            return self.get(path)
        except _Unavailable as exc:
            self.record(exc)
            return {}

    def details(self, items, path_fmt, label, out):
        """Fetch each item's detail form. A 404 means deleted mid-pull: skipped."""
        result = []
        total = len(items)
        for i, item in enumerate(items, 1):
            out and out.progress(i, total, f"{label} {item['id']}")
            try:
                result.append(self.get(path_fmt.format(id=item["id"])))
            except _Unavailable as exc:
                if exc.status != 404:
                    out and out.progress_done()
                    raise ContentPullError(
                        f"{exc.endpoint}: HTTP {exc.status}"
                    ) from exc
                out and out.log(f"{exc.endpoint} vanished during pull — skipped.")
        if total:
            out and out.progress_done()
        return result


# ------------------------------------------------------------------
# Cleaning and serialisation
# ------------------------------------------------------------------

def strip_private(obj):
    """Remove STRIPPED_KEYS at every depth, keeping key order."""
    if isinstance(obj, dict):
        return {k: strip_private(v) for k, v in obj.items() if k not in STRIPPED_KEYS}
    if isinstance(obj, list):
        return [strip_private(v) for v in obj]
    return obj


def _num(value):
    return value if isinstance(value, (int, float)) else -1


def sort_by_id(items):
    return sorted(items, key=lambda o: _num(o.get("id")))


def sort_contents(items):
    # id breaks (name, position) ties so output stays deterministic.
    return sorted(items, key=lambda o: (
        o.get("name") or "", _num(o.get("position")), _num(o.get("id")),
    ))


def serialise(obj):
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


# ------------------------------------------------------------------
# Collecting
# ------------------------------------------------------------------

def collect(api, published_only=False, out=None):
    """Fetch everything. Returns (files by relative path, counts, unavailable)."""
    f = _Fetcher(api)
    files = {}

    # Required: if /site fails (bad token or host), so would everything else.
    out and out.info("Fetching site...")
    try:
        files["site.json"] = f.get("/site")
    except _Unavailable as exc:
        hint = {401: " — check api_token in .voog",
                404: " — check host in .voog"}.get(exc.status, "")
        raise ContentPullError(f"{exc.endpoint}: HTTP {exc.status}{hint}") from exc

    out and out.info("Fetching languages, nodes, pages, articles...")
    languages = f.details(f.all_or_empty("/languages"), "/languages/{id}", "language", out)
    nodes = f.all_or_empty("/nodes")

    pages = f.all_or_empty("/pages")
    articles = f.all_or_empty("/articles")
    if published_only:
        pages = [p for p in pages if p.get("publishing") is not False]
        articles = [a for a in articles if a.get("published") is not False]
    pages = f.details(pages, "/pages/{id}", "page", out)
    articles = f.details(articles, "/articles/{id}", "article", out)

    out and out.info("Fetching tags, media sets, elements, forms...")
    tags = f.all_or_empty("/tags")
    media_sets = f.details(f.all_or_empty("/media_sets"), "/media_sets/{id}", "media set", out)
    elements = f.all_or_empty("/elements?include_values=true")
    element_definitions = f.all_or_empty("/element_definitions")
    forms = f.all_or_empty("/forms")
    buy_buttons = f.all_or_empty("/buy_buttons")

    out and out.info("Fetching ecommerce...")
    settings = f.one_or_empty("/ecommerce/v1/settings")
    products = f.all_or_empty("/ecommerce/v1/products")
    if published_only:
        products = [p for p in products if p.get("status") == "live"]
    products = f.details(
        products, "/ecommerce/v1/products/{id}?include=variant_types,translations",
        "product", out,
    )
    categories = f.all_or_empty("/ecommerce/v1/categories")

    owners = (
        [("page", p["id"], "/pages/{id}/contents") for p in pages]
        + [("article", a["id"], "/articles/{id}/contents") for a in articles]
        + [("language", l["id"], "/languages/{id}/contents") for l in languages]
        + [("element", e["id"], "/elements/{id}/contents") for e in elements]
    )
    out and out.info(f"Fetching contents for {len(owners)} pages/articles/languages/elements...")
    for i, (kind, oid, path) in enumerate(owners, 1):
        out and out.progress(i, len(owners), f"{kind} {oid}")
        contents = f.all_or_empty(path.format(id=oid))
        files[f"contents/{kind}-{oid}.json"] = sort_contents(contents)
    if owners:
        out and out.progress_done()

    files.update({
        "languages.json": sort_by_id(languages),
        "nodes.json": sort_by_id(nodes),
        "pages.json": sort_by_id(pages),
        "articles.json": sort_by_id(articles),
        "tags.json": sort_by_id(tags),
        "media_sets.json": sort_by_id(media_sets),
        "elements.json": sort_by_id(elements),
        "element_definitions.json": sort_by_id(element_definitions),
        "forms.json": sort_by_id(forms),
        "buy_buttons.json": sort_by_id(buy_buttons),
        "ecommerce/settings.json": settings,
        "ecommerce/products.json": sort_by_id(products),
        "ecommerce/categories.json": sort_by_id(categories),
    })
    files = {path: strip_private(data) for path, data in files.items()}

    counts = {
        "pages": len(pages),
        "articles": len(articles),
        "languages": len(languages),
        "products": len(products),
    }
    unavailable = sorted(f.unavailable, key=lambda u: u["endpoint"])
    return files, counts, unavailable


def build_manifest(config, counts, unavailable, published_only):
    return {
        "format": FORMAT_NAME,
        "format_version": FORMAT_VERSION,
        "generator": f"pyvoog {__version__}",
        "host": config.host,
        "protocol": config.protocol,
        "pulled_at": datetime.datetime.now(datetime.timezone.utc)
                     .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "options": {"published_only": published_only},
        "counts": counts,
        "unavailable": unavailable,
    }


# ------------------------------------------------------------------
# Writing
# ------------------------------------------------------------------

def _write_tree(root, rendered):
    for rel, text in rendered.items():
        abs_path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)


def _swap_in(site_dir):
    """
    Replace .voog-content/ with .voog-content.tmp/. A directory can't be
    renamed over a non-empty one, so the live copy is moved to .old first.
    """
    live = os.path.join(site_dir, CONTENT_DIR)
    tmp = os.path.join(site_dir, TMP_DIR)
    old = os.path.join(site_dir, OLD_DIR)

    had_live = os.path.isdir(live)
    if had_live:
        os.rename(live, old)
    try:
        os.rename(tmp, live)
    except OSError as exc:
        if had_live:
            try:
                os.rename(old, live)
            except OSError:
                raise OSError(
                    f"{exc}; the previous copy is in {OLD_DIR}/ — "
                    f"rename it back to {CONTENT_DIR}/"
                ) from exc
        raise
    if had_live:
        shutil.rmtree(old, ignore_errors=True)


def _recover_old(site_dir, out=None):
    """Restore .old from an interrupted swap, or delete it if a live copy exists."""
    live = os.path.join(site_dir, CONTENT_DIR)
    old = os.path.join(site_dir, OLD_DIR)
    if not os.path.lexists(old):
        return
    if os.path.lexists(live):
        shutil.rmtree(old)
    else:
        os.rename(old, live)
        out and out.log(f"Restored {CONTENT_DIR}/ from an interrupted earlier pull.")


def ensure_gitignored(site_dir, out=None):
    """Add any missing .voog-content rules to .gitignore."""
    path = os.path.join(site_dir, ".gitignore")
    text = ""
    if os.path.isfile(path):
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    rules = {line.strip().lstrip("/") for line in text.splitlines()}
    missing = []
    if not rules & {".voog-content/", ".voog-content"}:
        missing.append(".voog-content/")
    if not rules & {".voog-content.*/", ".voog-content.*"}:
        missing.append(".voog-content.*/")
    if not missing:
        return False
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        if text and not text.endswith("\n"):
            fh.write("\n")
        fh.write("\n# pyvoog — local content copy (pyvoog pull content), never commit this\n")
        fh.write("".join(rule + "\n" for rule in missing))
    out and out.info("Added " + ", ".join(missing) + " to .gitignore")
    return True


def pull_content(api, config, site_dir, published_only=False, dry_run=False, out=None):
    """Pull content into .voog-content/. On failure the previous copy is kept."""
    try:
        files, counts, unavailable = collect(api, published_only=published_only, out=out)
    except ContentPullError as exc:
        out and out.error(f"Content pull failed: {exc}")
        out and out.info("The previous .voog-content/ (if any) was left unchanged.")
        return False

    files["manifest.json"] = build_manifest(config, counts, unavailable, published_only)
    rendered = {rel: serialise(data) for rel, data in sorted(files.items())}

    # The token must never reach disk, under any key.
    token = config.api_token
    leaked = [rel for rel, text in rendered.items() if token and token in text]
    if leaked:
        out and out.error(
            "API token found in pulled content ("
            + ", ".join(leaked) + "). Nothing was written."
        )
        return False

    for u in unavailable:
        out and out.warn(f"Unavailable: {u['endpoint']} (HTTP {u['status']}) — written as empty.")

    summary = ", ".join(f"{n} {k}" for k, n in counts.items())
    if dry_run:
        out and out.info(f"\n[dry-run] Would write {len(rendered)} files to {CONTENT_DIR}/ ({summary}).")
        for rel in rendered:
            out and out.log(f"{CONTENT_DIR}/{rel}")
        return True

    # Before writing, so an interrupted pull leaves no unignored .tmp.
    try:
        ensure_gitignored(site_dir, out)
    except OSError as exc:
        out and out.warn(
            f"Could not update .gitignore: {exc}. "
            "Add  .voog-content/  to it by hand — the folder must not be committed."
        )

    tmp = os.path.join(site_dir, TMP_DIR)
    try:
        _recover_old(site_dir, out)
        if os.path.lexists(tmp):
            shutil.rmtree(tmp)
        _write_tree(tmp, rendered)
        _swap_in(site_dir)
    except OSError as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        out and out.error(f"Could not write {CONTENT_DIR}/: {exc}")
        return False

    scope = " (published only)" if published_only else ""
    out and out.info(f"\nDone: {len(rendered)} files written to {CONTENT_DIR}/{scope} — {summary}.")
    return True


def read_manifest(site_dir):
    """Return .voog-content/manifest.json, or None."""
    path = os.path.join(site_dir, CONTENT_DIR, "manifest.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None
