FROM python:3.11-slim

LABEL maintainer="RigbSecurity"
LABEL version="3.0.0"

WORKDIR /app

# Install only what's needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create runtime directories
RUN mkdir -p db logs captures

# Expose default port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -f http://localhost:8000/api/keepalive || exit 1

# Run with no-tunnel (tunnel handled externally in production)
ENTRYPOINT ["python3", "tracker.py", "--no-tunnel"]
CMD ["-p", "8000"]
