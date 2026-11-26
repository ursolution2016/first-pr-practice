# Statement Check server: Flask app behind a reverse proxy that terminates TLS (see deploy/).
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 app \
 && mkdir /data && chown app /data

WORKDIR /srv
COPY pyproject.toml README.md ./
COPY stmtcheck ./stmtcheck
RUN pip install --no-cache-dir ".[server]"

USER app
ENV STMTCHECK_HOME=/data \
    STMTCHECK_TRUST_PROXY=1 \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8000
# STMTCHECK_ACCESS_CODE (>= 12 characters) is required; the app refuses to start without it.
CMD ["python", "-m", "stmtcheck", "serve", "--host", "0.0.0.0", "--port", "8000"]
