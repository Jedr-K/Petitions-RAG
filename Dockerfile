FROM python:3.11-slim

WORKDIR /app

# System deps (only needed for Pillow/Gemini path)
RUN apt-get update && apt-get install -y \
    libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/ src/

ARG EXTRAS="gemini"
RUN pip install --no-cache-dir -e ".${EXTRAS:+[$EXTRAS]}"

ENTRYPOINT ["archival-htr"]
CMD ["serve"]
