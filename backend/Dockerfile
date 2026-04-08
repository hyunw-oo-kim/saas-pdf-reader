FROM python:3.11-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[dev]"

# Copy app code
COPY . .

# Create storage directory
RUN mkdir -p /app/storage

EXPOSE 8000

CMD ["bash", "entrypoint.sh"]
