# game-runtime: one image, one entrypoint dispatcher, every supported
# family/edition (plan.md section 3). Ubuntu 22.04 LTS (not Alpine) because
# Bedrock Dedicated Server for Linux officially requires Ubuntu 22.04+ and
# ships glibc-linked binaries; Alpine's musl libc cannot run it.
FROM ubuntu:22.04

ARG GAME_PORT=25565
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
	&& apt-get install -y --no-install-recommends \
		bash coreutils python3 ca-certificates gosu tini curl \
		libcurl4 libssl3 libicu70 zlib1g \
	&& rm -rf /var/lib/apt/lists/*

# Bedrock Dedicated Server binaries built before Ubuntu dropped OpenSSL 1.1
# still dlopen libssl.so.1.1 at runtime even though libssl3 (above) is what
# Ubuntu 22.04 ships by default. libssl1.1 is built from the `openssl`
# SOURCE package, so on Ubuntu's archive it lives under
# pool/main/o/openssl/ (NOT pool/main/o/openssl1.1/, which doesn't exist -
# that was the actual cause of a 404 here before). If this exact version
# ever disappears from the archive, find the current one under
# http://security.ubuntu.com/ubuntu/pool/main/o/openssl/ .
RUN set -eux; \
	curl_pkg="libssl1.1_1.1.1f-1ubuntu2.24_amd64.deb"; \
	curl -fsSL "http://security.ubuntu.com/ubuntu/pool/main/o/openssl/${curl_pkg}" -o "/tmp/${curl_pkg}"; \
	dpkg -i "/tmp/${curl_pkg}" || apt-get install -f -y --no-install-recommends; \
	rm -f "/tmp/${curl_pkg}"

# Every Temurin major version any Minecraft Java loader in the catalog might
# require (config/runtime_matrix.py), downloaded as tarballs (not apt) so
# the build stays reproducible even for EOL feature versions.
RUN set -eux; \
	for v in 8 16 17 21 25; do \
		mkdir -p "/opt/java/${v}"; \
		curl -fsSL "https://api.adoptium.net/v3/binary/latest/${v}/ga/linux/x64/jdk/hotspot/normal/eclipse?project=jdk" -o "/tmp/temurin-${v}.tar.gz"; \
		tar -xzf "/tmp/temurin-${v}.tar.gz" -C "/opt/java/${v}" --strip-components=1; \
		rm "/tmp/temurin-${v}.tar.gz"; \
		"/opt/java/${v}/bin/java" -version; \
	done

# `gamedata` (fixed GID 10002, also created in dashboard/Dockerfile) is a
# group shared by the `game` user here and the `dashboard` user in the
# other image - NOT a shared uid (plan.md section 8 still holds: neither
# container can impersonate the other, exec into it, or touch anything
# outside DATA_DIR/game). It exists solely so both sides can read/write the
# one bind mount (DATA_DIR/game) they both legitimately need write access
# to.
#
# It's `game`'s PRIMARY group here (not just a supplementary one) on
# purpose: entrypoint.sh's setgid bit on every directory (meant to make new
# files/dirs automatically inherit the gamedata group from their parent)
# turned out to not reliably propagate to new subdirectories created later,
# at runtime, on Docker Desktop's bind-mount backend - observed live as
# Minecraft's own bundler self-extracting libraries/<...>/<version>/*.jar
# and versions/<version>/ well after container start, landing as
# `game:game` instead of `game:gamedata` and leaving the dashboard's clean-
# install wipe with the exact same EACCES it had before this group existed.
# Making gamedata the PRIMARY gid sidesteps that unreliable inheritance
# entirely: every file/dir this process creates gets gamedata as its group
# from process defaults alone, setgid propagation or not. `game` (10000)
# still exists as a supplementary group, kept only for the `chown -R
# ${RUNTIME_UID}:...` in entrypoint.sh and anything reading file ownership
# expecting to see uid 10000.
RUN groupadd -g 10000 game \
	&& groupadd -g 10002 gamedata \
	&& useradd -u 10000 -g gamedata -G game -m -d /home/game -s /usr/sbin/nologin game

WORKDIR /data/game

COPY config /opt/urrahosting/config
COPY runtime /opt/urrahosting/runtime
RUN chmod +x /opt/urrahosting/runtime/entrypoint.sh

EXPOSE ${GAME_PORT}/tcp
EXPOSE ${GAME_PORT}/udp

ENTRYPOINT ["tini", "--", "/opt/urrahosting/runtime/entrypoint.sh"]
