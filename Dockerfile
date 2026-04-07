# syntax=docker/dockerfile:1

FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev

COPY . .

RUN mkdir -p logs

WORKDIR /app

# No CMD by design. Each Railway service in this repo must set its own
# "Custom Start Command" via the dashboard. See railway.toml for the
# rationale and the list of expected commands per service.
