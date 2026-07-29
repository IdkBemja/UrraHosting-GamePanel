# UrraHosting GamePanel

A self-hosted, per-instance game server template and control panel. Each
deployment (each copy of this repo, or each `--env-file`) runs **one** game
server instance plus a Flask dashboard to manage it — install/update
software, browse files, run console commands, take backups, and manage
panel users — without ever giving the dashboard direct Docker socket access
or shell access into the game container.

Built by reusing and adapting two internal reference implementations:
`UrraHosting-MinecraftServer` (panel UX, catalog/installer, security
patterns) and `UrraHosting-WebPanel` (privilege separation, hardening,
archive extraction, security headers). Neither reference directory's
behavior was changed; they remain regression baselines.

## Supported games

| Juego | Edición | Software instalable |
| --- | --- | --- |
| Minecraft | Java Edition | Vanilla, Paper, Purpur, Spigot, CraftBukkit, Fabric, Forge, NeoForge |
| Minecraft | Bedrock Dedicated Server | estable y preview oficiales para Linux |
| Terraria | Dedicated Server | Vanilla oficial para Linux |
| Terraria | Dedicated Server modded | tModLoader estable o preview |

`GAME_FAMILY`/`GAME_EDITION` are fixed for the lifetime of an instance (they
determine the published port/protocol and which compose overlay you used);
`GAME_SOFTWARE`/version are switchable from the dashboard's Software tab
within that family/edition (e.g. Paper → Purpur, or Terraria vanilla →
tModLoader is not switchable without redeploying, since they are different
`GAME_SOFTWARE` values under the same `terraria` family — installing a
different `GAME_SOFTWARE` value than the one in `.env` is intentionally
rejected by `config/game_config.py`).

## Quickstart

```bash
cp .env.example .env
# Edit .env: generate a new INSTANCE_ID, unique DATA_DIR, and change every
# secret (RCON_PASSWORD, GAME_CONTROL_TOKEN, APP_PASSWORD, APP_SECRET).
python -c "import uuid; print(uuid.uuid4())"          # INSTANCE_ID
python -c "import secrets; print(secrets.token_hex(24))"  # GAME_CONTROL_TOKEN
python -c "import secrets; print(secrets.token_hex(32))"  # APP_SECRET

# Validate before starting anything:
docker compose --env-file .env -f compose.yml config -q

# Local development, no Traefik, dashboard on 127.0.0.1 only:
docker compose --env-file .env -f compose.yml -f compose.dev.yml up --build

# Production, behind Traefik (requires an external `traefik-public` network
# and a wildcard DNS record pointed at the Traefik host):
docker compose --env-file .env -f compose.yml -f compose.traefik.yml up -d --build
```

A second instance is just another `.env` (own `INSTANCE_ID`, `DATA_DIR`,
ports, secrets) run with `--env-file`. Every network, container name and
volume is derived from `INSTANCE_ID`, so instances never collide or share
state.

Accept the license of whatever you're about to install (Mojang's EULA for
Minecraft, Re-Logic/tModLoader's terms for Terraria) by setting
`LICENSE_ACCEPTED=true` — the server refuses to start otherwise, and every
install is recorded in the activity log.

## Architecture

```
Browser -- HTTPS --> Traefik (optional) --> dashboard (Flask)
                                            | internal network
                                     docker-proxy (scoped Docker API)
                                            |
                                   game-runtime (one game)
                                   |  game process + control agent
                                   |  RCON (Java/Bedrock) or stdin pipe (Terraria)
                                   v
                              DATA_DIR volumes (isolated per instance)
```

- **`game-runtime`**: one image (`Dockerfile`, Ubuntu 22.04), dispatches to
  the right adapter (`runtime/adapters/`) based on
  `GAME_FAMILY`/`GAME_EDITION`/`GAME_SOFTWARE`. Runs as a non-root user,
  mounts only its data directories, publishes only the game port.
- **`game_control_agent.py`**: the actual PID-1 child inside `game-runtime`.
  Spawns the game process, exposes a token-authenticated HTTP API on the
  internal network only (`/health`, `/command`), and forwards console
  commands to RCON (Minecraft) or the process's stdin pipe (Terraria/
  tModLoader — there is no RCON-equivalent protocol for those). The
  dashboard never runs `docker exec` and never speaks RCON directly.
- **`docker-proxy`**: `tecnativa/docker-socket-proxy`, ACL'd to container
  status/logs/start/stop/restart only — no exec, build, images, networks or
  volumes. The dashboard talks to Docker exclusively through this proxy.
- **`dashboard`**: Flask app (`dashboard/app`), non-root, single gunicorn
  worker (the install lock and rate limiter are process-local by design).

## Persistence layout (`DATA_DIR`)

```
game/             installed binaries, config and world(s)
plugins/          Bukkit/Paper/Purpur plugins
mods/             Fabric/Forge/NeoForge/tModLoader mods
resourcepacks/    optional Minecraft resource packs
uploads/          scratch space for uploads
backups/          game/ archives + manifests
panel/            users db, activity log
install/          installation manifest, snapshots, staging
```

`worlds/`, `modconfigs/` and `players/` (Terraria/tModLoader) live as
subfolders inside `game/`, not separate volumes — see
`runtime/adapters/*.py`.

## Security notes

- Every reinstall of a zip-distributed server (Bedrock/Terraria/tModLoader)
  snapshots the previous `game/` directory first and only overwrites the
  paths each adapter's `preserved_paths()` doesn't protect (worlds, hand-
  edited config) — a version update never destroys a world. Use the
  Software tab's "Revertir" button to restore the last snapshot.
- Downloads only ever hit an explicit per-provider host allowlist
  (`dashboard/app/services/catalog/*.py`), checked again on every redirect
  hop — a compromised/typo'd upstream can never smuggle a download from an
  unexpected host.
- Backups are hashed (SHA-256) at creation and re-verified before restore;
  restoring is refused while the instance is running.
- CSP is `'self'`-only in every directive: Bootstrap Icons are vendored
  under `dashboard/app/static/vendor/`, and each software's official icon
  (`dashboard/app/static/icons/`) is downloaded once and served locally too
  - there is no external font/CDN/image hotlinking dependency anywhere.
- Known operational caveat: Bedrock Dedicated Server binaries can still
  `dlopen` OpenSSL 1.1, which Ubuntu 22.04 no longer ships by default; the
  `Dockerfile` installs the compat package from Ubuntu's security archive.
  If that specific package version is pulled from the archive, bump it to
  whatever `apt-cache search libssl1.1` resolves to at build time.

## Testing

```bash
pip install -r tests/requirements-dev.txt
pytest tests/ -q --cov=config --cov=runtime --cov=dashboard/app
ruff check config runtime dashboard tests
docker compose --env-file .env.example -f compose.yml config -q
docker compose --env-file .env.example -f compose.yml -f compose.dev.yml config -q
docker compose --env-file .env.example -f compose.yml -f compose.traefik.yml config -q
```

`tests/` covers: the typed config contract, the Java version matrix, every
adapter's `prepare`/`launch_command`/`preserved_paths`, every catalog
provider (mocked HTTP, including host-allowlist/redirect rejection), the
installer's jar/buildtools/installer/zip install kinds and rollback, the
backup service, storage path-traversal/symlink guards, archive zip/tar-slip
guards, the control agent's RCON/stdin dispatch and rate limiting, and a
Flask-level smoke test (auth, CSRF, RBAC, security headers).

CI (`.github/workflows/ci.yml`) runs ruff, pytest with coverage, pip-audit,
`docker compose config -q` for all three overlay combinations, builds both
images (never tagged `latest`), generates an SBOM, and runs a Trivy
filesystem scan.

## What was intentionally not automated

Consistent with `plan.md` section 1: no third-party mod/plugin downloads
(Modrinth/CurseForge) in this version — upload your own files via the Files
tab. Spigot/CraftBukkit are compiled locally via Spigot's own BuildTools
rather than redistributing a jar, since BuildTools compiles from Mojang
mappings under terms that are ambiguous for redistribution.
