# Stage 1: build the React app. Only the static files in dist/ reach the final image.
FROM node:22-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: the Python backend, which serves the API and the built frontend on port 8000.
FROM python:3.12-slim AS runtime

# ffmpeg assembles the show audio. Nothing else from apt is needed.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# uv comes from its official image. The tag matches the uv that wrote uv.lock (0.13);
# bump both together.
COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /uvx /bin/

WORKDIR /app
# UV_NO_SYNC stops `uv run` in the entrypoint from re-syncing (and pulling the dev group)
# at container start; the environment is complete after `uv sync` below.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv UV_NO_SYNC=1

# Dependencies first so they are cached between code changes. hatchling needs README.md
# because pyproject.toml declares it as the package readme.
COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
RUN uv sync --frozen --no-dev

# Prompts and the catalog metadata. The audio is git-ignored and comes from a volume.
COPY prompts/ prompts/
COPY catalog/ catalog/
COPY --from=frontend-build /app/frontend/dist frontend/dist

COPY docker-entrypoint.sh /app/
EXPOSE 8000
ENTRYPOINT ["/app/docker-entrypoint.sh"]
