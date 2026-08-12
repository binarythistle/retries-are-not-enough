# Instruqt sandbox image for the "Retries Are Not Enough" track.
#
# Everything the lab needs is baked here so lab start does no network I/O:
# no git clone, no pip install uv, no uv sync. Track setup only copies
# /opt/workshop to /root/workshop and starts the mock server.
#
# Build context is this repo's root, so the Dockerfile lives one level down:
#   docker build -f docker/Dockerfile -t <tag> .
#
# Instruqt sandboxes are linux/amd64. Build in CI, or pass --platform
# linux/amd64 if you build from an Apple Silicon Mac. The Temporal CLI download
# below is amd64-only, so a native arm64 build fails fast at `temporal
# --version` rather than producing an image that dies at lab start.

FROM python:3.14-slim

# --- System packages ----------------------------------------------------------
# git and curl are for the attendee, not the build. vim/nano/less because a
# terminal without an editor is a bad workshop.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git jq less nano procps vim \
    && rm -rf /var/lib/apt/lists/*

# --- uv -----------------------------------------------------------------------
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Use the base image's CPython rather than downloading a uv-managed one. The
# base tag and .python-version must agree for this to resolve. This one is a
# runtime guard too: an attendee running `uv add` can't trigger a CPython
# download inside the sandbox.
ENV UV_PYTHON_PREFERENCE=only-system

# --- Temporal CLI ---------------------------------------------------------------
# Pinned deliberately. `latest` would make the image non-reproducible and could
# change the dev server's behaviour between a rehearsal and the live session.
ARG TEMPORAL_CLI_VERSION=1.8.2
RUN curl -sSL \
      "https://github.com/temporalio/cli/releases/download/v${TEMPORAL_CLI_VERSION}/temporal_cli_${TEMPORAL_CLI_VERSION}_linux_amd64.tar.gz" \
    | tar -xz -C /usr/local/bin temporal \
    && chmod +x /usr/local/bin/temporal \
    && temporal --version

# --- Bake the workshop source -------------------------------------------------
# /opt/workshop is the pristine read-only copy. The attendee works in
# /root/workshop, which track setup stages from here.
COPY . /opt/workshop/
WORKDIR /opt/workshop

# --- Pre-warm the venv --------------------------------------------------------
# --frozen pins to uv.lock exactly, so a build never silently re-resolves.
#
# COMPILE_BYTECODE and LINK_MODE are set on this RUN only, deliberately. As
# persistent ENVs, uv would recompile the venv's bytecode the first time it ran
# from the staged /root/workshop path and print "Bytecode compiled N files"
# into the attendee's terminal and into mock.log.
RUN UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy uv sync --frozen

# --- Attendee helpers -----------------------------------------------------------
# restart-mock  : restart the mock LLM server after an .env change.
# start-temporal: bring up the dev server, idempotently. Challenge 05 onward.
#
# These ship as real files under docker/bin/ rather than being generated with
# printf, because they are long enough that generating them is unreadable.
# Dockerfile heredocs would be the other option but need the
# docker/dockerfile:1 frontend, which adds a Docker Hub pull to every CI build.
# docker/ is then removed from the baked tree so the attendee never sees it.
RUN install -m 0755 /opt/workshop/docker/bin/* /usr/local/bin/ \
    && rm -rf /opt/workshop/docker \
    && ls -l /usr/local/bin/restart-mock /usr/local/bin/start-temporal

# --- Shell environment --------------------------------------------------------
# TEMPORAL_ADDRESS saves every `temporal` command in the assignments needing an
# --address flag. Overridable, so pointing at Temporal Cloud later is one export.
RUN echo 'cd /root/workshop' >> /root/.bashrc \
    && echo 'export TEMPORAL_ADDRESS="${TEMPORAL_ADDRESS:-127.0.0.1:7233}"' >> /root/.bashrc

WORKDIR /root
CMD ["/bin/bash"]
