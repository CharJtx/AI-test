---
name: muveectl
description: Operate the Muvee self-hosted PaaS via the muveectl CLI. Manages projects (create, update, deploy, delete), datasets (create, scan, delete), and API tokens. Use when the user wants to interact with their Muvee server from the command line, trigger deployments, or manage infrastructure resources.
---

# muveectl – Muvee CLI

## Installation

Install `muveectl` from the official prebuilt assets on [GitHub Releases](https://github.com/hoveychen/muvee/releases/latest). Prefer the latest stable release unless the user asks for a specific version. Do not build from source and do not guess ad-hoc download URLs; use the published release assets directly.

```bash
# Choose the matching asset from the latest GitHub Release:
# macOS (Apple Silicon): muveectl_<VERSION>_darwin_arm64.tar.gz
# macOS (Intel):         muveectl_<VERSION>_darwin_amd64.tar.gz
# Linux (amd64):         muveectl_<VERSION>_linux_amd64.tar.gz
# Linux (arm64):         muveectl_<VERSION>_linux_arm64.tar.gz
#
# After extracting, rename the binary to `muveectl` and place it on PATH,
# for example in /usr/local/bin/muveectl.
```

```powershell
# Windows assets on GitHub Releases:
# muveectl_<VERSION>_windows_amd64.zip
# muveectl_<VERSION>_windows_arm64.zip
#
# Extract the zip and place the executable on PATH as `muveectl.exe`.
```

## Authentication

```bash
muveectl login --server https://momoso.com   # opens browser for Google OAuth
muveectl whoami
```

Config saved at `~/.config/muveectl/config.json`.

## Projects

```bash
muveectl projects list
muveectl projects create --name NAME --git-url URL \
  [--branch BRANCH] [--domain PREFIX] [--dockerfile PATH] \
  [--auth-required] [--auth-domains example.com,corp.com]
muveectl projects create --name NAME --git-source hosted \
  [--domain PREFIX] [--dockerfile PATH]
muveectl projects get PROJECT_ID
muveectl projects update PROJECT_ID [--branch BRANCH] [--auth-required] [--no-auth] [--auth-domains DOMAINS]
muveectl projects deploy PROJECT_ID
muveectl projects deployments PROJECT_ID
muveectl projects delete PROJECT_ID
```

### Google OAuth protection (`--auth-required`)

When enabled, Traefik intercepts every request and redirects unauthenticated users to Google OAuth login before forwarding to the container.

- `--auth-required` — enable protection
- `--no-auth` — disable protection
- `--auth-domains example.com,corp.com` — restrict to specific email domains (optional)

The authenticated user's email is forwarded to the container via the **`X-Forwarded-User`** HTTP header:

```python
# Python / Flask
user_email = request.headers.get("X-Forwarded-User")  # e.g. "alice@example.com"
```

```go
// Go
userEmail := r.Header.Get("X-Forwarded-User")
```

```typescript
// Node.js / Express
const userEmail = req.headers["x-forwarded-user"]
```

## Local Port Forwarding

Forward a project's running container to a local port for development. Authentication is automatically handled using your CLI identity — the container receives your email in the `X-Forwarded-User` header, just like in production.

```bash
# Auto-pick a free local port
muveectl projects port-forward PROJECT_ID

# Use a specific local port
muveectl projects port-forward PROJECT_ID --port 3000
```

Then call the project's API locally:

```bash
curl http://127.0.0.1:3000/api/some-endpoint
```

This is useful for local development when your code needs to call APIs exposed by a deployed project, without dealing with OAuth login flows or TLS certificates.

## Datasets

```bash
muveectl datasets list
muveectl datasets create --name NAME --nfs-path NFS_PATH
muveectl datasets get DATASET_ID
muveectl datasets scan DATASET_ID
muveectl datasets delete DATASET_ID
```

## API Tokens (project-scoped)

Tokens are scoped to a single project. Use them for git push authentication and project-level CLI access.

```bash
muveectl tokens PROJECT_ID list
muveectl tokens PROJECT_ID create [--name NAME]   # token value shown once on creation
muveectl tokens PROJECT_ID delete TOKEN_ID
```

## Secrets

```bash
muveectl secrets list
muveectl secrets create --name GITHUB_TOKEN --type password --value github_pat_xxxx
muveectl secrets create --name DEPLOY_KEY --type ssh_key --value-file ~/.ssh/id_ed25519
muveectl secrets delete SECRET_ID
```

### Project Secret Bindings

```bash
# Runtime env var
muveectl projects bind-secret PROJECT_ID --secret-id SECRET_ID --env-var GITHUB_TOKEN

# Git clone auth
muveectl projects bind-secret PROJECT_ID --secret-id SECRET_ID --use-for-git --git-username x-access-token

# Build-time secret
muveectl projects bind-secret PROJECT_ID --secret-id SECRET_ID --use-for-build --build-secret-id github_token
```

## Global Flags

| Flag | Description |
|------|-------------|
| `--server URL` | Override the configured server URL for this call |
| `--json`      | Output raw JSON (pipe-friendly) |

## Git Repository Requirements

For a project to deploy successfully the repository must satisfy:

### Build
- Accessible via `git clone --depth=1` over HTTPS (public or token secret) or SSH (SSH key secret)
- The configured branch must exist (default: `main`)
- A `Dockerfile` must exist at the configured path (default: `Dockerfile` in repo root)
- Image must build for **`linux/amd64`** (`docker buildx build --platform linux/amd64`)
- For private dependencies during build, bind a secret with `--use-for-build --build-secret-id <id>` and read `/run/secrets/<id>` in Dockerfile

### Runtime
- Container must serve **HTTP** on port **8080** — Traefik handles TLS termination
- Do not start HTTPS inside the container
- App will be reachable at `https://<domain_prefix>.<base_domain>`

### Dataset mounts
Datasets are injected as Docker volumes at `/data/<dataset_name>` inside the container:

| Mode | Access |
|------|--------|
| `dependency` | Read-only — rsync-cached local copy |
| `readwrite`  | Read-write — direct NFS mount |

## Hosted Git Repositories

When creating a project with `--git-source hosted`, Muvee creates a bare git repository on the server. After creation, you receive a push URL. Push your code using any git username and your Muvee API token as the password:

```bash
muveectl projects create --name my-app --git-source hosted
# → Created project my-app (ID: abc-123)
# → Git Push URL: https://muvee.example.com/git/abc-123.git

git remote add muvee https://muvee.example.com/git/abc-123.git
git push muvee main
# When prompted: username = anything, password = your mvt_* project token
# Create a token first: muveectl tokens PROJECT_ID create --name "git push"
```

### Repository Browser

```bash
muveectl projects repo PROJECT_ID tree [--ref main] [--path src/]
muveectl projects repo PROJECT_ID log [--ref main] [--limit 20]
muveectl projects repo PROJECT_ID branches
muveectl projects repo PROJECT_ID show main:README.md
```

## Typical Workflow

1. Get project IDs: `muveectl projects list --json`
2. Deploy a project: `muveectl projects deploy PROJECT_ID`
3. Check status: `muveectl projects deployments PROJECT_ID`
4. Forward to local port: `muveectl projects port-forward PROJECT_ID --port 3000`