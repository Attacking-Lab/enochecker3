# Woodpecker CI runner image for .woodpecker/test.yml. Bundles uv, just and
# git-pass-helper (the sync step's git credential wrapper) on top of the
# stock python:${PYTHON_VERSION} image -- one tag per PYTHON_VERSION matrix
# value, so a run pays for these installs once, at image build time,
# instead of on every step. Built and pushed automatically by test.yml's
# own `ci-image` step whenever this file changes (see the
# ci-image-build/ci-image-push justfile recipes for the equivalent by-hand
# commands, still useful for local testing). Modelled on
# atklab/enochecker-exploit's .woodpecker/Dockerfile.
ARG PYTHON_VERSION=3.13
FROM python:${PYTHON_VERSION}-slim-bookworm

ARG JUST_VERSION=1.58.0

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV UV_LINK_MODE=copy

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        ca-certificates \
        curl \
        git \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

RUN pip install --no-cache-dir git-pass-helper==0.2.1

RUN curl -fsSL \
        "https://github.com/casey/just/releases/download/${JUST_VERSION}/just-${JUST_VERSION}-x86_64-unknown-linux-musl.tar.gz" \
        -o /tmp/just.tar.gz \
    && tar -xzf /tmp/just.tar.gz -C /usr/local/bin just \
    && rm /tmp/just.tar.gz

WORKDIR /woodpecker/src
