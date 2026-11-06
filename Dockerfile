# Obraz API i ingestii USBot (ten sam obraz: usługi api, ingest i models w compose.prod.yml).
#
#   docker build -t usbot-api .
#
# Wieloetapowy: zależności (torch z CUDA ~3 GB) instalowane w etapie `deps` do venv,
# do obrazu końcowego trafia tylko venv i kod. Działa jako użytkownik bez roota (app, 10001).
# Modele Hugging Face NIE są w obrazie — pobiera je usługa `models` na wolumen cache'u
# (uzasadnienie: docs/uruchomienie.md, 4.5). Sekrety (.env) nie trafiają do obrazu
# (.dockerignore) — przekazuje je compose przy uruchomieniu.

ARG PYTHON_IMAGE=python:3.12-slim-bookworm

# --- zależności Pythona ------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS deps
# Koła torch z CUDA 12.6 działają ze sterownikiem NVIDIA >= 525 (biblioteki CUDA są w
# kołach — obraz bazowy nie potrzebuje CUDA). Bez GPU: --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cpu
ARG TORCH_INDEX=https://download.pytorch.org/whl/cu126
ARG TORCH_VERSION=2.14.1
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
RUN pip install --index-url ${TORCH_INDEX} torch==${TORCH_VERSION}
COPY requirements.txt /tmp/requirements.txt
# triton (~0,9 GB) służy tylko torch.compile — embeddingi i reranker go nie używają
RUN pip install -r /tmp/requirements.txt && pip uninstall -y triton

# --- supercronic (harmonogram ingestii w kontenerze, bez roota i bez gniazda Dockera) ---
FROM ${PYTHON_IMAGE} AS supercronic
ARG SUPERCRONIC_VERSION=v0.2.51
ARG SUPERCRONIC_SHA256=cc232108374e8e2b537045f2d5eaa1229f619db314c3ba6a14d38b35dc0aa123
ADD https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-amd64 /supercronic
RUN echo "${SUPERCRONIC_SHA256}  /supercronic" | sha256sum -c - && chmod 0755 /supercronic

# --- obraz końcowy ----------------------------------------------------------------------
FROM ${PYTHON_IMAGE}
# curl — healthcheck; tzdata — harmonogram w czasie lokalnym (TZ); flock jest w util-linux
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /models /data/logs /data/state \
    && chown -R app:app /models /data

COPY --from=deps /opt/venv /opt/venv
COPY --from=supercronic /supercronic /usr/local/bin/supercronic

WORKDIR /app
COPY --chown=root:root usbot/ usbot/
COPY --chown=root:root ingestion/ ingestion/
COPY --chown=root:root cli.py ./
COPY --chown=root:root infra/ingest/ infra/ingest/
RUN chmod 0755 infra/ingest/*.sh

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/models/hf \
    HF_HUB_OFFLINE=1 \
    LOG_FORMAT=json \
    CHAT_LOG_DIR=/data/logs \
    TZ=Europe/Warsaw

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=300s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health > /dev/null || exit 1

# --proxy-headers: adres klienta z X-Forwarded-For od Caddy (rate limiting na IP). API nie
# jest wystawione poza sieć compose, więc ufamy każdemu nadawcy w tej sieci.
CMD ["uvicorn", "usbot.api.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header"]
