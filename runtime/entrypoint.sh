#!/bin/bash
set -euo pipefail

export PYTHONPATH="/opt/urrahosting${PYTHONPATH:+:$PYTHONPATH}"

RUNTIME_UID=10000
RUNTIME_GROUP="game"
DATA_DIR="/data/game"

log() {
  echo "[entrypoint] $*"
}

log "Validando configuracion de la instancia..."
if ! python3 -m config.validate_cli; then
  log "Configuracion invalida, abortando arranque (revisa los errores anteriores, nunca se listan valores de secretos)."
  exit 1
fi

# Best-effort: some bind-mount backends (notably Docker Desktop's Windows
# file sharing) reject chown on the host-mounted path entirely ("Operation
# not permitted") even though the mapped UID can still read/write through
# it just fine. A real Linux host's bind mount supports this and chown
# actually matters there (it is what lets the non-root `game` user below
# write into it); treat a failure here as a warning, not a fatal error, so
# local development on those backends isn't blocked.
#
# Group is `gamedata`, not `game`: every one of these directories is also
# bind-mounted into the dashboard container (a different uid entirely, see
# dashboard/Dockerfile), which needs to both read software the game process
# wrote AND write its own uploads into mods/plugins/resourcepacks - it only
# has gamedata as a SUPPLEMENTARY group (not primary), so it can never fix a
# wrong owner/mode itself; it has no shared uid with this container to chown
# with. This pass is only a one-time catch-up for whatever already exists
# right now, at container start - it does NOT reliably cover files the game
# process creates later, at runtime (e.g. Minecraft's bundler self-
# extracting libraries/ and versions/ on first boot, well after this line
# runs): the setgid bit below was originally meant to make those inherit the
# gamedata group automatically, but that propagation to new subdirectories
# turned out to be unreliable on Docker Desktop's bind-mount backend, so
# this repo's Dockerfile now makes `gamedata` the game user's PRIMARY group
# instead - new files get the right group from process defaults alone,
# independent of setgid. `umask 002` below (not the default 022) is what
# then keeps them group-writable too, since a directory's own permission
# bits are never inherited by files created inside it, setgid or not.
#
# mods/plugins/resourcepacks are SEPARATE bind mounts overlaid at these
# paths (compose.yml: `${DATA_DIR}/mods:/data/game/mods`), not plain
# subdirectories of the `${DATA_DIR}/game:/data/game` mount - a single
# recursive chown/chmod on just "$DATA_DIR" was observed to leave them
# root-owned in practice (dashboard uploads to mods/plugins/resourcepacks
# then fail with EACCES). Looping over each mount explicitly, rather than
# relying on one recursive pass to cross into all of them, means a failure
# on any single one is still logged and never silently skips the rest.
for dir in "$DATA_DIR" "$DATA_DIR/mods" "$DATA_DIR/plugins" "$DATA_DIR/resourcepacks"; do
  mkdir -p "$dir"
  chown -R "${RUNTIME_UID}:gamedata" "$dir" || log "advertencia: no se pudo aplicar chown a $dir (normal en bind mounts de Docker Desktop en Windows); continuando"
  chmod -R g+rwX "$dir" || log "advertencia: no se pudo aplicar chmod a $dir; continuando"
  find "$dir" -type d -exec chmod g+s {} + 2>/dev/null || log "advertencia: no se pudo aplicar el bit setgid en $dir; continuando"
done
umask 002

# Uses the EFFECTIVE environment (raw env + instance_state.py's override
# file), not the raw $GAME_FAMILY/$GAME_EDITION/$GAME_SOFTWARE - those still
# hold whatever `.env` originally shipped with, which no longer matches
# reality once the dashboard has reprovisioned the instance to a different
# game/software (game_control_agent.py itself already resolves the override
# correctly; this log line was the one place still reading the stale value).
ADAPTER_LABEL=$(python3 -c "
import os
from config.instance_state import effective_environ
e = effective_environ(os.environ)
print(f\"{e.get('GAME_FAMILY', '')}-{e.get('GAME_EDITION', '')}/{e.get('GAME_SOFTWARE', '')}\")
")
log "Adaptador seleccionado: ${ADAPTER_LABEL}"
log "Delegando control a game_control_agent.py como usuario sin privilegios..."

# No `:group` suffix on purpose: gosu only resolves this user's groups (its
# gamedata PRIMARY group plus the game supplementary one, from this image's
# useradd - see the Dockerfile) via NSS lookup when given a bare user/uid -
# `gosu uid:gid` skips that lookup entirely and leaves the process in just
# the one given group (verified empirically: `gosu 10000 id` ->
# groups=gamedata,game; `gosu 10000:game id` -> groups=game only, losing
# gamedata). A comma-separated group list after the colon (`uid:group,
# group`) is not gosu syntax at all - it fails outright ("unable to find
# group ...") rather than adding groups.
exec gosu "${RUNTIME_UID}" python3 /opt/urrahosting/runtime/game_control_agent.py
