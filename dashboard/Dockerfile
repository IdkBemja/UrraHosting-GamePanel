FROM python:3.12-slim

ARG DASHBOARD_PORT=8080
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# gosu only here; Java comes from the Temurin matrix below (BuildTools.jar
# and the Forge/NeoForge installer jars run from the dashboard, not the
# game-runtime container - plan.md section 4.4).
# `gamedata` (fixed GID 10002, also created in the base Dockerfile for the
# game-runtime image) is a supplementary group shared with the `game` user
# over there - see the comment on that Dockerfile's groupadd for why. It
# grants nothing beyond read/write on DATA_DIR/game: no shared uid, no
# access to the game container's process or any other mount.
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates gosu \
	&& rm -rf /var/lib/apt/lists/* \
	&& groupadd -g 10001 dashboard \
	&& useradd -u 10001 -g dashboard -m -d /home/dashboard -s /usr/sbin/nologin dashboard \
	&& groupadd -g 10002 gamedata \
	&& usermod -aG gamedata dashboard

# Same Temurin major-version matrix as the game-runtime image (see that
# Dockerfile's comment): BuildTools/Forge/NeoForge installers need to run
# under a Java as new as the Minecraft version they target (e.g. NeoForge
# 21.1.x/MC 1.21.1 needs Java 21) - a single fixed JRE (previously
# default-jre-headless, Java 17 on this base image) made every installer run
# for a version newer than that fail silently, surfacing as "El instalador
# no genero run.sh correctamente" regardless of which NeoForge/Forge version
# was actually selected. installer.py resolves the right one per-install via
# config/runtime_matrix.py, same as the game-runtime launch path.
RUN set -eux; \
	for v in 8 16 17 21 25; do \
		mkdir -p "/opt/java/${v}"; \
		curl -fsSL "https://api.adoptium.net/v3/binary/latest/${v}/ga/linux/x64/jdk/hotspot/normal/eclipse?project=jdk" -o "/tmp/temurin-${v}.tar.gz"; \
		tar -xzf "/tmp/temurin-${v}.tar.gz" -C "/opt/java/${v}" --strip-components=1; \
		rm "/tmp/temurin-${v}.tar.gz"; \
		"/opt/java/${v}/bin/java" -version; \
	done

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
