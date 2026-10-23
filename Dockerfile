FROM node:22-bookworm-slim
ARG AGY_VERSION=1.4.6
ARG AGY_URL=https://storage.googleapis.com/antigravity-public/antigravity-cli/1.4.6-4884751325200384/linux-x64/cli_linux_x64.tar.gz
ARG AGY_SHA512=4e1244b8b8c1280552f59c1b24b76a9245db9c80032aec6db28fb86033d6088abc30a90dad432d2b246c28fdd87d82abb48727d5ff88ba1d5d4ec76b33348e7d
LABEL org.opencontainers.image.title="DND AI Arahort" org.opencontainers.image.authors="Arahort <alex@arahort.pro>"
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl libsecret-1-0 libasound2 libnss3 libgbm1 libgtk-3-0 && rm -rf /var/lib/apt/lists/*
RUN test "$(dpkg --print-architecture)" = amd64 && curl --fail --silent --show-error --location --proto '=https' --connect-timeout 20 --max-time 180 "$AGY_URL" -o /tmp/agy.tgz && printf '%s  /tmp/agy.tgz\n' "$AGY_SHA512" | sha512sum --check --status && tar -xzf /tmp/agy.tgz -C /usr/local/bin antigravity && mv /usr/local/bin/antigravity /usr/local/bin/agy && chmod 755 /usr/local/bin/agy && rm /tmp/agy.tgz && agy --version | grep -F "$AGY_VERSION"
RUN groupmod -n dnd node && usermod -l dnd -d /home/dnd node && mkdir -p /home/dnd /data/uploads /data/tts-cache /run/dnd-updater && chown -R 1000:1000 /home/dnd /data
WORKDIR /app
COPY app/package*.json ./
RUN npm ci --omit=dev --ignore-scripts && npm cache clean --force
COPY app/ ./
ENV NODE_ENV=production HOME=/home/dnd PATH=/home/dnd/.local/bin:/usr/local/bin:/usr/bin:/bin PORT=3334 APP_ROOT=/app ASSETS_DIR=/app/client PROMPTS_DIR=/app/prompts DND_DATA_DIR=/app/data/dnd-ru UPLOADS_DIR=/data/uploads TTS_CACHE_DIR=/data/tts-cache DND_AGY_BIN=/usr/local/bin/agy
USER 1000:1000
EXPOSE 3334
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 CMD node -e "fetch('http://127.0.0.1:3334/api/health').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"
CMD ["node", "server/index.mjs"]
