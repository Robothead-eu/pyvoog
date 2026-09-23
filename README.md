# pyvoog

Manage Voog CMS templates and design assets over the REST API. A Python 3.11+ replacement for the Ruby [voog-kit](https://github.com/Voog/voog-kit): stdlib only, no dependencies, works on Windows, macOS and Linux.

## Why pyvoog

pyvoog fixes these problems in the Ruby voog-kit:

- **Hyphenated layouts:** voog-kit silently skips layouts whose `layout_name` contains hyphens. pyvoog pulls every layout.
- **Overwritten server edits:** `kit push` uploads every file without checking the server, so edits made in the Voog editor or by another developer are lost. pyvoog pushes only the files you changed, and skips any file that changed on the server since your last pull.
- **Missed layouts on push:** voog-kit matches layouts by title, so after a title change in the Voog editor it doesn't update that layout and tries to create a new one. pyvoog matches by server id and `layout_name`.
- **No undo:** voog-kit keeps no history. pyvoog commits every pull and push to git.
- **Unmaintained:** voog-kit hasn't been updated since 2021 and needs Ruby with old pinned gems. pyvoog needs only Python 3.11+.

pyvoog also adds `remove`, `check`, `pull content` (site content for local rendering with voog-server), and experimental staging → production comparison.

## Installation

```bash
git clone https://github.com/Robothead-eu/pyvoog.git
```

Optional alias:

```bash
alias pyvoog="python ~/path/to/pyvoog/pyvoog.py"                  # macOS / Linux
function pyvoog { python "C:\path\to\pyvoog\pyvoog.py" @args }     # Windows PowerShell
```

Update with `git pull` in the pyvoog directory.

## Quick start

```bash
pyvoog init ./my-site --host mysite.voog.com --token YOUR_API_TOKEN   # or run in an existing dir
cd ./my-site
pyvoog pull
# edit files
pyvoog push
```

The API token is in the Voog admin under **Settings → Integrations → API**.

## Commands

Run `pyvoog help <command>` for full options.

### `init [DIR] --host HOST --token TOKEN`

Creates `.voog` (config), `.gitignore` (excludes `.voog`) and a git repo.

### `pull [layouts|assets|FILE ...] [--dry-run] [--reset]`

Downloads layouts, components and design assets. The server always wins. Pulled files and `manifest.json` are committed to git; other files are not staged.

```bash
pyvoog pull                           # everything
pyvoog pull layouts                   # .tpl files only
pyvoog pull components/footer.tpl     # specific files
pyvoog pull --reset                   # also delete local .tpl files not on the server
```

### `pull content [--published-only] [--dry-run]`

Saves the site's content (pages, articles, languages, products, content areas, …) as raw API JSON to `.voog-content/`, for rendering locally with voog-server. Format: voog-content v1, specified in the voog-server repo.

- **Off by default** — enable per site with `content_pull=true` in `.voog`.
- Includes unpublished pages, articles and draft products unless `--published-only`.
- Emails, bank details and API tokens are removed.
- Endpoints the site's plan lacks (401/403/404) are written empty and listed in `.voog-content/manifest.json`; other errors fail the pull and keep the previous copy.
- The folder is gitignored and never committed or pushed.

### `push [FILE ...] [--dry-run] [--force] [--create]`

Uploads changed layouts and CSS/JS files. Changes are detected with `git diff HEAD`, limited to files in `manifest.json`. Files changed on the server since the last pull are skipped as conflicts.

- `--force` — skip the conflict check.
- `--create` — create files not yet on the server.

Binary assets (images, fonts) can't be updated: the Voog API rejects it.

### `new FILE [--type TYPE] [--dry-run]` · `new --all` · `new --list`

Creates a layout or asset on the server from a local file. The type comes from the directory (`layouts/`, `components/`, `stylesheets/`, …). Use `--type` for special layouts such as `blog`. `--all` creates every local-only file after confirmation, and `--list` just lists them.

### `remove FILE ... [--local-only|--remote-only] [--dry-run] [--yes]`

Deletes files locally and on the server and removes them from `manifest.json`. Always asks for confirmation unless `--yes` is given. Server deletes can't be undone.

### `check` · `status` · `manifest [--save]`

- `check` — compare local files with the server (missing / modified / extra). Writes nothing.
- `status` — site, manifest summary, content pull, last commit.
- `manifest` — show the server's file list; `--save` writes `manifest.json`.

### Experimental: `env-setup` · `env-diff` · `env-copy`

Compare and copy manifest-tracked files between two local copies of a site (e.g. staging → production). `env-copy` overwrites files in the target without undo. Use `--dry-run` first. See `pyvoog help experimental`.

## Global options

| Flag | |
|---|---|
| `--verbose`, `-v` | Show API calls, file writes and git operations |
| `--site NAME` | Use a named `.voog` section |
| `--version` | Print version |

## `.voog` config

INI format, compatible with the Ruby kit. **Never commit it**: it holds the API token.

```ini
[mysite.voog.com]
host=mysite.voog.com
api_token=your_api_token_here
protocol=https
env_name=
env_peer_name=
env_peer_path=
content_pull=false
```

| Field | |
|---|---|
| `env_name`, `env_peer_name`, `env_peer_path` | Optional. Used by the experimental env commands; set them with `pyvoog experimental env-setup`. |
| `content_pull` | `true` enables `pull content` for this site. Default `false`. |

A file can hold several sections; select one with `--site NAME`.

## Site directory

```
site-dir/
├── .voog            config (not in git)
├── manifest.json    server ids and timestamps, updated on pull/push
├── layouts/         page layouts (.tpl)
├── components/      components (.tpl)
├── stylesheets/  javascripts/  images/  assets/
└── .voog-content/   site content from `pull content` (not in git)
```

## Troubleshooting

| Error | Fix |
|---|---|
| `No .voog config file found` | Run `pyvoog init` in the site directory. |
| `Authentication failed (401)` | The token in `.voog` is invalid or expired. |
| `HTTP 404` | Check `host=` in `.voog`. |
| `CONFLICT — server was modified after last pull` | `pyvoog pull`, then re-apply your changes. |
| git not found | pyvoog still works, but without auto-commits or change detection for push. |

## License

MIT
