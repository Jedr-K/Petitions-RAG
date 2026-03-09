FROM python:3.11-slim

WORKDIR /app

# System deps (only needed for Pillow/Gemini path)
RUN apt-get update && apt-get install -y \
    libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY src/ src/

# Install base deps. Add [gemini] extras if BACKEND=gemini is needed.
RUN pip install --no-cache-dir -e .

# To use Gemini backend, build with:
#   docker build --build-arg EXTRAS=gemini -t archival-htr .
ARG EXTRAS=""
RUN if [ -n "$EXTRAS" ]; then pip install --no-cache-dir -e ".[$EXTRAS]"; fi

# Data volumes
VOLUME ["/data/input", "/data/output", "/data/chroma"]

ENTRYPOINT ["archival-htr"]
CMD ["--help"]
