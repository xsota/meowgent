FROM debian:bookworm-slim

USER root

# Install prerequisites to fetch uv
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
  && rm -rf /var/lib/apt/lists/*

# Install uv (standalone binary)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app

# Copy project metadata and lock first for better layer caching
COPY pyproject.toml uv.lock ./

# Install Python via uv and sync dependencies using the lockfile
RUN uv python install 3.12 \
 && uv sync --frozen --no-dev --python 3.12

# Copy application source
COPY src ./src

# Run the bot via uv, which ensures the managed Python is used
CMD ["uv", "run", "--python", "3.12", "python", "src/bot.py"]
