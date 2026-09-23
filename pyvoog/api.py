"""Voog Admin REST API client (urllib, X-API-Token auth)."""

import json
import time
import urllib.error
import urllib.request


class APIError(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class VoogAPI:
    def __init__(self, config, output=None):
        """output is an optional Output used for verbose logging."""
        self._config = config
        self._out = output

    def _log(self, msg):
        if self._out:
            self._out.log(msg)

    # ------------------------------------------------------------------
    # Shared error handling
    # ------------------------------------------------------------------

    def _handle_http_error(self, exc, url):
        """Translate urllib HTTPError into APIError."""
        if exc.code == 401:
            raise APIError(
                "Authentication failed (401). "
                "Check api_token in your .voog file.",
                status_code=401,
            )
        if exc.code == 404:
            raise APIError(
                f"Not found (404): {url}",
                status_code=404,
            )
        raise APIError(f"HTTP {exc.code} for {url}", status_code=exc.code)

    # ------------------------------------------------------------------
    # Internal request helper
    # ------------------------------------------------------------------

    def _get(self, path, binary=False, _retry=1):
        """Authenticated GET; returns raw bytes if binary, else parsed JSON."""
        url = f"{self._config.base_url}{path}"
        self._log(f"GET {url}")
        req = urllib.request.Request(
            url,
            headers={"X-API-Token": self._config.api_token},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return raw if binary else json.loads(raw)
        except urllib.error.HTTPError as exc:
            self._handle_http_error(exc, url)
        except urllib.error.URLError as exc:
            if _retry > 0:
                self._log(f"Network error ({exc.reason}), retrying in 2s…")
                time.sleep(2)
                return self._get(path, binary=binary, _retry=_retry - 1)
            raise APIError(f"Network error: {exc.reason}")

    def _download(self, url, _retry=1):
        """Download a binary resource from an arbitrary URL (no auth required)."""
        self._log(f"GET {url}")
        req = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except urllib.error.URLError as exc:
            if _retry > 0:
                self._log(f"Network error ({exc.reason}), retrying in 2s…")
                time.sleep(2)
                return self._download(url, _retry=_retry - 1)
            raise APIError(f"Failed to download {url}: {exc.reason}")

    def get_json(self, path):
        """GET any Admin API path (e.g. '/admin/api/pages/1') and parse it as JSON."""
        return self._get(path)

    # ------------------------------------------------------------------
    # Layouts
    # ------------------------------------------------------------------

    def get_layouts(self):
        """List all layouts and components, without bodies. 250 is Voog's per_page max."""
        layouts = []
        page = 1
        while True:
            page_data = self._get(f"/admin/api/layouts?per_page=250&page={page}")
            if not page_data:
                break
            layouts.extend(page_data)
            if len(page_data) < 250:
                break
            page += 1
        return layouts

    def get_layout(self, layout_id):
        """Fetch a single layout by ID, including its body content."""
        return self._get(f"/admin/api/layouts/{layout_id}")

    def update_layout(self, layout_id, body):
        """PUT a new layout body; returns the updated layout."""
        return self._put(f"/admin/api/layouts/{layout_id}", {"body": body})

    def _put(self, path, data, _retry=1):
        """Perform an authenticated PUT request with a JSON body."""
        url = f"{self._config.base_url}{path}"
        self._log(f"PUT {url}")
        payload = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "X-API-Token": self._config.api_token,
                "Content-Type": "application/json",
            },
            method="PUT",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise APIError(
                    "Authentication failed (401). "
                    "Check api_token in your .voog file.",
                    status_code=401,
                )
            if exc.code == 404:
                raise APIError(
                    f"Not found (404): {url}",
                    status_code=404,
                )
            raise APIError(f"HTTP {exc.code} for {url}", status_code=exc.code)
        except urllib.error.URLError as exc:
            if _retry > 0:
                self._log(f"Network error ({exc.reason}), retrying in 2s\u2026")
                time.sleep(2)
                return self._put(path, data, _retry=_retry - 1)
            raise APIError(f"Network error: {exc.reason}")

    # ------------------------------------------------------------------
    # Assets
    # ------------------------------------------------------------------

    def get_layout_assets(self):
        """List all design assets (layout_assets), not media-library uploads (/assets)."""
        assets = []
        page = 1
        while True:
            page_data = self._get(f"/admin/api/layout_assets?per_page=250&page={page}")
            if not page_data:
                break
            assets.extend(page_data)
            if len(page_data) < 250:
                break
            page += 1
        return assets

    def get_layout_asset(self, asset_id):
        """Fetch a single layout asset by ID (text assets include 'data')."""
        return self._get(f"/admin/api/layout_assets/{asset_id}")

    def update_layout_asset(self, asset_id, data):
        """PUT new text content (CSS/JS) to a layout asset; returns the updated asset."""
        return self._put(f"/admin/api/layout_assets/{asset_id}", {"data": data})

    def update_layout_asset_binary(self, asset_id, filename, file_bytes, content_type=None):
        """Replace a binary layout asset via multipart PUT.

        Voog currently answers 500 to any multipart PUT (POST works). Kept in
        case Voog adds support; push reports the failure with guidance.
        """
        return self._put_multipart(
            f"/admin/api/layout_assets/{asset_id}",
            fields={},
            file_field="file",
            file_name=filename,
            file_bytes=file_bytes,
            file_content_type=content_type or "application/octet-stream",
        )

    def download_url(self, url):
        """Download binary content from a public URL (no auth needed)."""
        return self._download(url)

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def delete_layout(self, layout_id):
        """Delete a layout. Voog refuses if a page uses it or the site has no custom design."""
        return self._delete(f"/admin/api/layouts/{layout_id}")

    def delete_layout_asset(self, asset_id):
        """Delete a layout asset (CSS, JS, image, font) on the server."""
        return self._delete(f"/admin/api/layout_assets/{asset_id}")

    def _delete(self, path, _retry=1, _retried=False):
        """Authenticated DELETE; returns True (204 has no body).

        A 404 on a retry counts as success: the first attempt may have deleted
        the resource before its response was lost.
        """
        url = f"{self._config.base_url}{path}"
        self._log(f"DELETE {url}")
        req = urllib.request.Request(
            url,
            headers={"X-API-Token": self._config.api_token},
            method="DELETE",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp.read()
                return True
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and _retried:
                self._log("404 on retry \u2014 already deleted, treating as success.")
                return True
            self._handle_http_error(exc, url)
        except urllib.error.URLError as exc:
            if _retry > 0:
                self._log(f"Network error ({exc.reason}), retrying in 2s\u2026")
                time.sleep(2)
                return self._delete(path, _retry=_retry - 1, _retried=True)
            raise APIError(f"Network error: {exc.reason}")
        except OSError as exc:
            # Socket read timeouts are OSError, not URLError.
            raise APIError(f"Network error: {exc}")

    # ------------------------------------------------------------------
    # Create (POST)
    # ------------------------------------------------------------------

    def create_layout(self, title, content_type, body, component=False, layout_name=None):
        """POST a new layout; returns the created layout."""
        data = {
            "title": title,
            "content_type": content_type,
            "component": component,
            "body": body,
        }
        if layout_name:
            data["layout_name"] = layout_name
        return self._post("/admin/api/layouts", data)

    def create_layout_asset(self, filename, data=None, file_bytes=None, content_type=None):
        """POST a new layout asset: data= for text, file_bytes= for binary (multipart).

        Returns the created asset.
        """
        if file_bytes is not None:
            return self._post_multipart(
                "/admin/api/layout_assets",
                fields={"filename": filename},
                file_field="file",
                file_name=filename,
                file_bytes=file_bytes,
                file_content_type=content_type or "application/octet-stream",
            )
        payload = {"filename": filename}
        if data is not None:
            payload["data"] = data
        if content_type:
            payload["content_type"] = content_type
        return self._post("/admin/api/layout_assets", payload)

    def _post(self, path, data, _retry=1):
        """Perform an authenticated POST request with a JSON body."""
        url = f"{self._config.base_url}{path}"
        self._log(f"POST {url}")
        payload = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "X-API-Token": self._config.api_token,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            self._handle_http_error(exc, url)
        except urllib.error.URLError as exc:
            if _retry > 0:
                self._log(f"Network error ({exc.reason}), retrying in 2s…")
                time.sleep(2)
                return self._post(path, data, _retry=_retry - 1)
            raise APIError(f"Network error: {exc.reason}")

    def _build_multipart(self, boundary, fields, file_field, file_name, file_bytes, file_content_type):
        """Assemble a multipart/form-data body."""
        parts = []
        for key, val in fields.items():
            parts.append(
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
                f"{val}\r\n"
            )
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{file_field}"; filename="{file_name}"\r\n'
            f"Content-Type: {file_content_type}\r\n\r\n"
        )
        payload = b"".join(p.encode("utf-8") for p in parts)
        payload += file_bytes
        payload += f"\r\n--{boundary}--\r\n".encode("utf-8")
        return payload

    def _post_multipart(self, path, fields, file_field, file_name, file_bytes,
                        file_content_type="application/octet-stream", _retry=1):
        """Perform an authenticated multipart/form-data POST (for binary uploads)."""
        import os
        boundary = f"----pyvoog-{os.urandom(16).hex()}"
        payload = self._build_multipart(boundary, fields, file_field, file_name, file_bytes, file_content_type)

        url = f"{self._config.base_url}{path}"
        self._log(f"POST {url} (multipart)")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "X-API-Token": self._config.api_token,
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            self._handle_http_error(exc, url)
        except urllib.error.URLError as exc:
            if _retry > 0:
                self._log(f"Network error ({exc.reason}), retrying in 2s…")
                time.sleep(2)
                return self._post_multipart(
                    path, fields, file_field, file_name, file_bytes,
                    file_content_type, _retry=_retry - 1,
                )
            raise APIError(f"Network error: {exc.reason}")

    def _put_multipart(self, path, fields, file_field, file_name, file_bytes,
                       file_content_type="application/octet-stream", _retry=1):
        """Perform an authenticated multipart/form-data PUT (for binary asset updates)."""
        import os
        boundary = f"----pyvoog-{os.urandom(16).hex()}"
        payload = self._build_multipart(boundary, fields, file_field, file_name, file_bytes, file_content_type)

        url = f"{self._config.base_url}{path}"
        self._log(f"PUT {url} (multipart)")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "X-API-Token": self._config.api_token,
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="PUT",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            self._handle_http_error(exc, url)
        except urllib.error.URLError as exc:
            if _retry > 0:
                self._log(f"Network error ({exc.reason}), retrying in 2s…")
                time.sleep(2)
                return self._put_multipart(
                    path, fields, file_field, file_name, file_bytes,
                    file_content_type, _retry=_retry - 1,
                )
            raise APIError(f"Network error: {exc.reason}")
