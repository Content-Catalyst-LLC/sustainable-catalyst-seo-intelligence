# SEO Studio / evidence importer image (staging). Built from this repository only; no private evidence is copied in.
FROM python:3.12.8-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN python -m pip install --no-cache-dir 'cryptography==44.0.1' \
 && groupadd --system --gid 10001 seo && useradd --system --uid 10001 --gid seo --no-create-home seo
WORKDIR /app
COPY seo_*.py ./
COPY scripts/reconcile_sitemaps.py scripts/bounded_public_crawl.py ./scripts/
COPY studio ./studio
RUN mkdir -p /data && chown 10001:10001 /data && chmod 0750 /data
USER 10001:10001
EXPOSE 18980
# Default: the read-only Studio. The importer overrides the command (compose profile "import").
ENTRYPOINT ["python", "-s", "-E", "seo_studio_server.py"]
