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
# linux/amd64 if you build from an Apple Silicon Mac.

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

# --- Attendee helper ----------------------------------------------------------
# The mock server reads .env once at import, so a config change needs a
# restart. This is the command the assignments tell attendees to run.
RUN printf '%s\n' \
      '#!/bin/bash' \
      'cd /root/workshop' \
      'pkill -f mock_llm.py || true' \
      'sleep 1' \
      'nohup uv run mock_llm.py >> mock.log 2>&1 &' \
      'sleep 2' \
      'tail -n 12 mock.log' \
    > /usr/local/bin/restart-mock \
    && chmod +x /usr/local/bin/restart-mock

# --- Shell environment --------------------------------------------------------
RUN echo 'cd /root/workshop' >> /root/.bashrc

WORKDIR /root
CMD ["/bin/bash"]
