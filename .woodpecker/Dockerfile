# Woodpecker CI runner image for .woodpecker/test.yml. Bundles uv, just
# and git-pass-helper (the sync step's git credential wrapper) on a
# generic Debian base -- no Python baked in. test.yml's PYTHON_VERSION
# matrix instead picks its interpreter at `uv sync` time via the
# UV_PYTHON env var; uv downloads whichever version that names on
# demand, so this image only needs building and pushing once instead of
# once per matrix value (see .woodpecker/ci-image.yml's own comment for
# why that split exists, and the ci-image-build/ci-image-push justfile
# recipes for the equivalent by-hand commands, still useful for local
# testing). Modelled on atklab/enochecker-exploit's .woodpecker/Dockerfile.
FROM debian:bookworm-slim

ARG JUST_VERSION=1.58.0

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV UV_LINK_MODE=copy
ENV UV_TOOL_BIN_DIR=/usr/local/bin

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        ca-certificates \
        curl \
        git \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# uv tool install rather than pip install: this image no longer carries
# a system Python for pip to install into, so git-pass-helper's own
# venv (managed by uv, Python downloaded on demand) is what runs it.
RUN uv tool install git-pass-helper==0.2.1

RUN curl -fsSL \
        "https://github.com/casey/just/releases/download/${JUST_VERSION}/just-${JUST_VERSION}-x86_64-unknown-linux-musl.tar.gz" \
        -o /tmp/just.tar.gz \
    && tar -xzf /tmp/just.tar.gz -C /usr/local/bin just \
    && rm /tmp/just.tar.gz

WORKDIR /woodpecker/src
