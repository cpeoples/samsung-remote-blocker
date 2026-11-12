FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/data

LABEL org.opencontainers.image.source="https://github.com/cpeoples/samsung-remote-blocker" \
      org.opencontainers.image.description="Neutralize streaming shortcut buttons on Samsung TV remotes" \
      org.opencontainers.image.licenses="MIT"

WORKDIR /app
COPY pyproject.toml README.md ./
COPY samsung_remote_blocker ./samsung_remote_blocker
RUN pip install . && rm -rf /app

# /data holds the TV pairing token and Plex token; keep it on a volume.
# Starts as root to chown /data, then drops privileges; see privileges.py.
RUN useradd --uid 1000 --no-create-home --shell /usr/sbin/nologin app \
 && mkdir /data && chown app:app /data && chmod 700 /data
WORKDIR /data
VOLUME /data

ENTRYPOINT ["samsung-remote-blocker"]
CMD ["watch"]
