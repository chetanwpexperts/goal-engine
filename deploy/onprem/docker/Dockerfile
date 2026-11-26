FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 \
    SITE_MODE=open ENGINE_MODE=ml AGENT_SCHEDULER=on DBABA_LOCAL=1 \
    COOKIE_SECURE=false EXPOSE_INTERNALS=0 DATABASE_URL=sqlite:////data/dbaba.db \
    GEMINI_API_KEY="" GROQ_API_KEY="" AI_PROVIDER_ORDER="" UVICORN_HOST=0.0.0.0
WORKDIR /opt/dbaba
COPY requirements.txt .
RUN python -m pip install --no-compile --only-binary=:all: -r requirements.txt \
    && groupadd --gid 10001 dbaba \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /data --shell /usr/sbin/nologin dbaba \
    && mkdir -p /data \
    && chown 10001:10001 /data
COPY app/ app/
COPY connector/ connector/
COPY deploy/onprem/docker/entrypoint.sh /usr/local/bin/dbaba-entrypoint
RUN chmod 755 /usr/local/bin/dbaba-entrypoint \
    && ln -s /data/dbaba.json dbaba.json \
    && ln -s /data data
USER 10001:10001
VOLUME ["/data"]
EXPOSE 8765
HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=12 CMD ["python", "-c", "import json,urllib.request; p=json.load(open('/data/dbaba.json'))['port']; r=urllib.request.build_opener(urllib.request.ProxyHandler({})).open(f'http://127.0.0.1:{p}/api/health',timeout=3); assert json.load(r).get('ok') is True"]
ENTRYPOINT ["/usr/local/bin/dbaba-entrypoint"]
