# pyvoog

> **Work in progress** — has not been tested end-to-end in all scenarios. Issues and feedback are welcome.

A Python replacement for the Ruby [voog-kit](https://github.com/Voog/voog-kit).  
Manage Voog CMS site templates and design assets directly via the REST API.

**Why?**

- **Fixes the hyphen bug** — the Ruby kit silently fails to pull any layout whose `layout_name` contains hyphens, a common naming pattern. This tool calls the API directly, so every file is handled correctly.
- **Proper git tracking** — every pull and push is automatically committed to git, giving you real diff history, easy rollback, and change detection for push.
- **No Ruby required** — no gem/bundle/rbenv toolchain needed. Just Python 3.11+ (stdlib only, zero dependencies). Works on Windows and macOS.

---

## Features

- **Pull** all layouts, components, CSS, JS, images and fonts in one command
- **Push** locally modified files back to the server (layouts + text assets)
- **Conflict detection** — push warns when the server has changed since your last pull
- **Manifest-scoped git** — only Voog-tracked files are committed; developer files are ignored
- `pyvoog check` compares local files against the server without changing anything
- `pyvoog new` creates new layouts/assets on the server from local files
- `pyvoog manifest` inspects the remote file structure
- Partial pulls: `pyvoog pull layouts` or `pyvoog pull assets`
- Dry-run mode for both pull and push
- Multi-site support via `--site` flag
- **Experimental:** `pyvoog experimental env-diff` / `env-copy` — compare and sync two local environment copies (e.g. staging → production)

---

## Installation

1. **Clone this repo** into any directory (it lives separately from your site repos):

   ```bash
   git clone https://github.com/Robothead-eu/pyvoog.git
   ```

2. **Set up a shell alias** (optional but recommended):

   **Windows (PowerShell profile)**:
   ```powershell
   function pyvoog { python "C:\path\to\pyvoog\pyvoog.py" @args }
   ```

   **macOS / Linux (~/.bashrc or ~/.zshrc)**:
   ```bash
   alias pyvoog="python ~/path/to/pyvoog/pyvoog.py"
   ```

3. **Python 3.11+** must be on your PATH. Verify: `python --version`

---

## Quick start

```bash
# 1. Create a new site directory
pyvoog init ./my-site --host mysite.voog.com --token YOUR_API_TOKEN

# 2. Pull everything
cd ./my-site
pyvoog pull

# 3. Edit files locally, then push changes
pyvoog push

# 4. Check sync status at any time
pyvoog check
```

### Adding pyvoog to an existing site directory

```bash
cd /path/to/existing-site
pyvoog init --host mysite.voog.com --token YOUR_API_TOKEN
pyvoog pull
```

---

## Commands

### `pyvoog init [DIR] --host HOST --token TOKEN`

Initialise a site directory.

```bash
pyvoog init --host mysite.voog.com --token abc123
pyvoog init ./my-site --host mysite.voog.com --token abc123
```

Creates:
- `.voog` — site config (kept out of git via `.gitignore`)
- `.gitignore` — excludes `.voog` and cache files
- `.git/` — git repository (for undo/history)

---

### `pyvoog pull [layouts|assets] [--dry-run] [--reset]`

Pull files from the server. The server is always the source of truth.

```bash
pyvoog pull                  # pull everything (layouts + assets)
pyvoog pull layouts          # only .tpl files (layouts + components)
pyvoog pull assets           # only CSS, JS, images, fonts
pyvoog pull --dry-run        # see what would change, without writing
pyvoog pull --reset          # also remove orphaned local .tpl files
```

After a successful pull, changed files are automatically committed to git.
Only manifest-tracked files are staged — developer files in the same directories are left untouched.

---

### `pyvoog push [FILE ...] [--dry-run]`

Push locally modified files to the server.

```bash
pyvoog push                              # push all changed manifest-tracked files
pyvoog push layouts/page.tpl             # push a specific file
pyvoog push stylesheets/main.css         # push a CSS file
pyvoog push --dry-run                    # see what would be pushed
```

**How push works:**

1. Detects changed files via `git diff HEAD`
2. Filters to only files present in `manifest.json` (developer files are ignored)
3. Checks the server for conflicts (`updated_at` comparison)
4. Uploads safe files; warns and skips conflicting ones
5. Auto-commits pushed files to git

**What can be pushed:**
- Layouts and components (`.tpl` files)
- Text assets: CSS and JavaScript files

**Not supported (yet):**
- Binary assets (images, fonts) — these must be uploaded via the Voog editor

---

### `pyvoog check`

Compare local files against the server without changing anything.

```bash
pyvoog check
pyvoog check --verbose
```

Reports:
- **Missing** — on server, not local
- **Modified** — local file differs from server
- **Extra** — local file, not on server
- **In sync** — matches server exactly

---

### `pyvoog manifest [--save]`

Fetch and display the remote file structure.

```bash
pyvoog manifest              # show summary
pyvoog manifest --verbose    # show full file list
pyvoog manifest --save       # write manifest.json to site directory
```

---

### `pyvoog status`

Show site info, manifest summary, and last git commit.

```bash
pyvoog status
```

---

### `pyvoog help [command]`

```bash
pyvoog help
pyvoog help pull
pyvoog help push
pyvoog help experimental
```

---

### `pyvoog new FILE [--type TYPE] [--dry-run]`

Create new layouts or assets on the Voog server from local files.
The file must already exist locally.

```bash
pyvoog new layouts/blog.tpl              # create a new layout (defaults to content_type=page)
pyvoog new components/sidebar.tpl        # create a new component
pyvoog new stylesheets/custom.css        # create a new CSS asset
pyvoog new layouts/blog.tpl --type blog  # override content_type for special layouts
```

#### Create all new files at once

```bash
pyvoog new --all                         # find all local-only files, confirm, then create
pyvoog new --all --dry-run               # preview what would be created
```

#### List new files

```bash
pyvoog new --list                        # list local files not yet on the server
```

**How it works:**
1. File type is inferred from the directory (`layouts/` → page layout, `components/` → component, `stylesheets/` → CSS asset, etc.)
2. For `--all`: fetches server state, compares against local files, shows the list, and asks for `y/N` confirmation
3. Creates via POST to the Voog API
4. Updates `manifest.json` with new server IDs and timestamps
5. Auto-commits to git

---

---

## Experimental commands

> **Warning:** These commands write directly to local files without undo. Use `--dry-run` and review the diff before confirming a copy.

### `pyvoog experimental env-setup`

Interactive wizard to configure `env_name`, `env_peer_name`, and `env_peer_path` in `.voog`. Run once per machine.

```bash
pyvoog experimental env-setup
```

### `pyvoog experimental env-diff SOURCE TARGET`

Compare manifest-tracked files between two local environment directories. Shows modified, added, removed, and identical files. Does not write anything.

```bash
pyvoog experimental env-diff staging production
pyvoog experimental env-diff staging production --verbose
```

### `pyvoog experimental env-copy SOURCE TARGET [--dry-run]`

Shows the same diff as `env-diff`, then asks for confirmation before copying changed files from SOURCE to TARGET. Does not commit or push.

```bash
pyvoog experimental env-copy staging production            # review diff, confirm, then copy
pyvoog experimental env-copy staging production --dry-run  # preview only
```

For full details: `pyvoog help experimental`

---

## .voog config format

The `.voog` file is INI-style, compatible with the Ruby voog-kit:

```ini
[mysite.voog.com]
host=mysite.voog.com
api_token=your_api_token_here
protocol=https
env_name=
env_peer_name=
env_peer_path=
```

**Never commit `.voog` — it contains your API token.** The `pyvoog init` command adds it to `.gitignore` automatically.

The `env_*` fields are optional and used only by the experimental `env-diff` / `env-copy` commands:

| Field | Purpose |
|---|---|
| `env_name` | Friendly name for this environment (e.g. `staging`) |
| `env_peer_name` | Friendly name for the peer environment (e.g. `production`) |
| `env_peer_path` | Path to the peer environment's local directory |

Run `pyvoog experimental env-setup` to fill these in interactively.

### Finding your API token

In the Voog admin panel: **Settings → Integrations → API** (or similar — the exact path varies by Voog version).

### Multiple sites

You can have multiple sections in `.voog` and switch between them with `--site`:

```ini
[staging.mysite.voog.com]
host=staging.mysite.voog.com
api_token=token_a
env_name=staging
env_peer_name=production
env_peer_path=C:\path\to\production-site

[production.mysite.voog.com]
host=production.mysite.voog.com
api_token=token_b
env_name=production
env_peer_name=staging
env_peer_path=C:\path\to\staging-site
```

```bash
pyvoog pull --site staging.mysite.voog.com
pyvoog pull --site production.mysite.voog.com
```

---

## Directory structure

pyvoog creates/pulls files into this structure (same as the Ruby kit):

```
site-dir/
├── .voog                 ← config (not in git)
├── .gitignore
├── manifest.json         ← updated on every pull/push
├── layouts/              ← page layouts (.tpl)
├── components/           ← reusable components (.tpl)
├── stylesheets/          ← CSS files
├── javascripts/          ← JS files
├── images/               ← image assets
└── assets/               ← other assets (fonts, SVGs, etc.)
```

---

## Global options

| Flag | Description |
|---|---|
| `--verbose` / `-v` | Show API calls, file writes, and git operations |
| `--site NAME` | Use a named `.voog` section (multi-site) |
| `--version` | Print version and exit |

---

## Updating pyvoog

```bash
cd ~/path/to/pyvoog
git pull
```

---

## Migrating from the Ruby voog-kit

1. Keep your existing site directory as-is
2. Run `pyvoog init --host your.voog.com --token YOUR_TOKEN` inside it
3. Run `pyvoog pull` — all files are re-pulled reliably (including those with hyphens in filenames that the Ruby kit missed)
4. The Ruby kit is no longer needed

---

## Troubleshooting

**`No .voog config file found`**  
Run `pyvoog init --host YOUR_HOST --token YOUR_TOKEN` in your site directory.

**`Authentication failed (401)`**  
Your API token in `.voog` is invalid or expired. Get a new one from the Voog admin panel.

**`HTTP 404`**  
The site hostname in `.voog` is wrong. Check the `host=` line.

**`CONFLICT — server was modified after last pull`**  
Someone edited the file on the server since your last `pyvoog pull`. Run `pyvoog pull` to sync, then re-apply your local changes.

**Files with hyphens not pulling with the Ruby kit**  
This is a known Ruby kit bug. Use `pyvoog pull` instead — it calls the API directly.

**Git not found**  
Install git or add it to your PATH. The tool still works without git — you just won't get auto-commits or change detection for push.

---

## License

MIT
