"""Load and write the .voog site config (INI, voog-kit compatible).

Optional keys: env_* for env-diff/env-copy; content_pull enables `pull content`,
off by default because that pull includes unpublished drafts.
"""

import os
import configparser


class ConfigError(Exception):
    pass


# Blank counts as off, like the empty env_* placeholders.
_BOOLEANS = {
    "": False, "false": False, "no": False, "off": False, "0": False,
    "true": True, "yes": True, "on": True, "1": True,
}


class SiteConfig:
    def __init__(self, section, host, api_token, protocol="https",
                 env_name=None, env_peer_name=None, env_peer_path=None,
                 content_pull=False):
        self.section = section
        self.host = host
        self.api_token = api_token
        self.protocol = protocol
        self.env_name = env_name
        self.env_peer_name = env_peer_name
        self.env_peer_path = env_peer_path  # local path to the peer environment
        # True/False, or the raw string if .voog holds a non-boolean.
        self.content_pull = content_pull

    @property
    def base_url(self):
        return f"{self.protocol}://{self.host}"

    def __repr__(self):
        return f"<SiteConfig host={self.host!r}>"


def find_voog_file(start_dir=None):
    """Walk up from start_dir (default cwd) to the nearest .voog; return its path or None."""
    d = os.path.abspath(start_dir or os.getcwd())
    while True:
        candidate = os.path.join(d, ".voog")
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def load_config(site_dir=None, site_name=None):
    """Load a SiteConfig from the nearest .voog. Raises ConfigError on any problem.

    site_name picks a section by name or host; default is the first section.
    """
    voog_file = find_voog_file(site_dir)
    if not voog_file:
        raise ConfigError(
            "No .voog config file found in this directory (or any parent).\n"
            "Run  pyvoog init --host <host> --token <token>  to set up a site here."
        )

    # interpolation=None: '%' in a token is literal. Otherwise cfg.get() raises
    # InterpolationSyntaxError, which is not a ConfigError.
    cp = configparser.ConfigParser(interpolation=None)
    try:
        cp.read(voog_file, encoding="utf-8")
    except configparser.Error as exc:
        raise ConfigError(f"Could not parse {voog_file}:\n  {exc}") from exc

    sections = cp.sections()
    if not sections:
        raise ConfigError(
            f".voog file has no sections: {voog_file}\n"
            "Expected format:\n"
            "  [site.voog.com]\n"
            "  host=site.voog.com\n"
            "  api_token=<your-token>"
        )

    if site_name:
        section = None
        for s in sections:
            if s == site_name or cp[s].get("host", "") == site_name:
                section = s
                break
        if not section:
            available = ", ".join(sections)
            raise ConfigError(
                f"Site '{site_name}' not found in {voog_file}.\n"
                f"Available sections: {available}"
            )
    else:
        section = sections[0]

    cfg = cp[section]

    host = cfg.get("host", section)
    api_token = cfg.get("api_token", "").strip()
    if not api_token:
        raise ConfigError(
            f"No api_token in .voog section [{section}].\n"
            "Add:  api_token=<your-token>"
        )

    # Keep a bad value rather than raise: only `pull content` uses it, and reports it.
    content_pull = cfg.get("content_pull", "").strip()
    content_pull = _BOOLEANS.get(content_pull.lower(), content_pull)

    return SiteConfig(
        section=section,
        host=host,
        api_token=api_token,
        protocol=cfg.get("protocol", "https"),
        env_name=cfg.get("env_name", "").strip() or None,
        env_peer_name=cfg.get("env_peer_name", "").strip() or None,
        env_peer_path=cfg.get("env_peer_path", "").strip() or None,
        content_pull=content_pull,
    )


def write_voog_file(path, host, api_token, protocol="https"):
    """Write a fresh .voog config file with empty env fields as placeholders."""
    content = (
        f"[{host}]\n"
        f"host={host}\n"
        f"api_token={api_token}\n"
        f"protocol={protocol}\n"
        f"env_name=\n"
        f"env_peer_name=\n"
        f"env_peer_path=\n"
        f"content_pull=false\n"
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def update_env_config(voog_file, section, env_name, env_peer_name, env_peer_path):
    """Set the env_* fields in a .voog section, keeping all other keys."""
    cp = configparser.ConfigParser(interpolation=None)
    try:
        cp.read(voog_file, encoding="utf-8")
    except configparser.Error as exc:
        raise ConfigError(f"Could not parse {voog_file}:\n  {exc}") from exc

    if section not in cp:
        raise ConfigError(f"Section [{section}] not found in {voog_file}")

    cp[section]["env_name"] = env_name or ""
    cp[section]["env_peer_name"] = env_peer_name or ""
    cp[section]["env_peer_path"] = env_peer_path or ""

    with open(voog_file, "w", encoding="utf-8") as f:
        cp.write(f, space_around_delimiters=False)
