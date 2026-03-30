FROM python:3.12-slim

WORKDIR /app

# Install uv
RUN pip install uv

# Copy dependency files first for caching
COPY pyproject.toml uv.lock* ./

# Install dependencies
RUN uv sync --no-dev

# Copy the rest of the code
COPY . .

WORKDIR /app/live_trading_bot

ENV PYTHONUNBUFFERED=1
CMD ["uv", "run", "python", "-u", "harness/side_by_side.py", "--dry-runs", "2", "--live-runs", "1"]
