FROM python:3.12-slim

ARG DASHBOARD_PORT=8080
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# default-jre-headless runs BuildTools.jar (Spigot/CraftBukkit) and the
# Forge/NeoForge installer jars - both invoked from the dashboard, not the
# game-runtime container (plan.md section 4.4).
# `gamedata` (fixed GID 10002, also created in the base Dockerfile for the
# game-runtime image) is a supplementary group shared with the `game` user
# over there - see the comment on that Dockerfile's groupadd for why. It
# grants nothing beyond read/write on DATA_DIR/game: no shared uid, no
# access to the game container's process or any other mount.
RUN apt-get update && apt-get install -y --no-install-recommends default-jre-headless gosu \
	&& rm -rf /var/lib/apt/lists/* \
	&& groupadd -g 10001 dashboard \
	&& useradd -u 10001 -g dashboard -m -d /home/dashboard -s /usr/sbin/nologin dashboard \
	&& groupadd -g 10002 gamedata \
	&& usermod -aG gamedata dashboard

COPY dashboard/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Shared config/adapter modules (see config/game_config.py, runtime/adapters)
# so the dashboard and the game-runtime entrypoint validate the same .env
# contract and agree on file categories/preserved paths identically.
COPY config /app/config
COPY runtime /app/runtime
COPY dashboard/app /app/app

RUN chown -R dashboard:dashboard /app

EXPOSE ${DASHBOARD_PORT}

# Single worker on purpose: the install lock (services/installer.py) and
# Flask-Limiter's default in-memory storage are process-local.
# `app.main:create_app()` (a factory call, not a pre-built module-level
# `app` object) so importing app.main never requires a valid environment to
# already be in place - only actually starting the server does.
# No `:group` suffix on the gosu call: it only resolves supplementary
# groups (this user's `gamedata` membership from groupadd/usermod above,
# needed for DATA_DIR/game to be writable here) via NSS lookup when given a
# bare username - `gosu user:group` skips that lookup and leaves the
# process in just the one named group, silently dropping gamedata (verified
# empirically against this exact gosu build). A comma-separated group list
# after the colon is not valid gosu syntax at all.
# umask 002 (not the default 022): anything this process creates directly
# under DATA_DIR/game (a fresh server.jar, extracted zip-kind installs)
# stays group-writable for the game-runtime side too - a directory's own
# permission bits are never inherited by files created inside it, so this
# is the only thing that keeps new files group-writable regardless of
# which group they land in.
CMD ["sh", "-c", "umask 002 && mkdir -p /data/panel /data/install /data/backups && (chown -R dashboard:dashboard /data/panel /data/install /data/backups || echo 'warning: chown on /data/* failed (expected on some bind-mount backends); continuing') && exec gosu dashboard gunicorn --bind 0.0.0.0:${DASHBOARD_PORT:-8080} --workers 1 --threads 8 --timeout 1900 'app.main:create_app()'"]
