# syntax=docker/dockerfile:1.7
#
# A buzz agent: upstream's harness plus opencode, on any Docker host.
#
# The base is upstream's own agent image. `sprig` is one multi-call binary and
# `buzz-acp`, `buzz`, `rg`, `tree`, `git-credential-nostr` and `git-sign-nostr`
# are symlinks to it, so harness and CLI can never drift apart. Its entrypoint
# scopes the nostr git credential helper to the relay and execs buzz-acp.
#
# Pin the digest. Get the current one with:
#   docker pull ghcr.io/block/buzz-sprig:main
#   docker image inspect ghcr.io/block/buzz-sprig:main --format '{{index .RepoDigests 0}}'
ARG SPRIG_IMAGE=ghcr.io/block/buzz-sprig:main
FROM ${SPRIG_IMAGE}

USER root

# opencode is a Bun-compiled binary. Even the musl build links libstdc++ and
# libgcc, which Alpine does not ship — without them it dies at load with
# "Error relocating ...: __cxa_guard_acquire: symbol not found", which reads
# like a corrupt download rather than a missing package.
RUN apk add --no-cache libstdc++ libgcc

# Uncomment whatever the agent actually needs to do its job. Every addition is
# capability you are handing to something that acts on chat messages.
# RUN apk add --no-cache aws-cli ansible-core py3-boto3 openssh-client make jq

# Install from the npm platform tarball rather than via npm: the package holds a
# single binary, so pulling in nodejs to unpack it costs ~50MB for nothing.
# Pin the version — an agent whose behaviour changes overnight is miserable to debug.
ARG OPENCODE_VERSION=1.18.18
RUN set -eux; \
    arch="$(apk --print-arch)"; \
    case "$arch" in \
      x86_64)  pkg="opencode-linux-x64-musl" ;; \
      aarch64) pkg="opencode-linux-arm64-musl" ;; \
      *) echo "unsupported architecture: $arch" >&2; exit 1 ;; \
    esac; \
    wget -qO /tmp/oc.tgz \
      "https://registry.npmjs.org/${pkg}/-/${pkg}-${OPENCODE_VERSION}.tgz"; \
    tar xzf /tmp/oc.tgz -C /tmp; \
    install -m0755 /tmp/package/bin/opencode /usr/local/bin/opencode; \
    rm -rf /tmp/oc.tgz /tmp/package; \
    opencode --version

# The base turns on nostr-signed commits system-wide. That suits repos hosted on
# a buzz relay and confuses every ordinary git host, which cannot verify the
# x509-shaped signatures. Drop it unless you specifically want it.
RUN git config --system --unset-all commit.gpgSign || true; \
    git config --system --unset-all tag.gpgSign || true; \
    git config --system commit.gpgSign false; \
    git config --system tag.gpgSign false; \
    git config --system --add safe.directory /workspace

# Anything installed above ran as root with HOME=/home/agent and may have left
# root-owned dotfiles there. Ansible in particular then fails for the agent user
# with "Unhandled exception when retrieving DEFAULT_LOCAL_TMP: Permission
# denied" — an error that names a temp directory and never mentions ownership.
RUN chown -R agent:agent /home/agent

USER agent
WORKDIR /workspace
