# Build target-architecture packages without privileged QEMU/binfmt installation.
#
# Claude Code ships a self-contained native binary per platform, so the final image
# needs no Node runtime. Pulling only that binary instead of the previous Codex +
# Node pair removes about 233MB from the image.
FROM --platform=$BUILDPLATFORM node:22-bookworm-slim AS claude
ARG CLAUDE_VERSION=2.1.283
ARG TARGETARCH
RUN case "$TARGETARCH" in amd64) native_arch=x64 ;; arm64) native_arch=arm64 ;; *) exit 1 ;; esac \
    # --ignore-scripts skips the postinstall that links the binary, which is what we
    # want on a cross-architecture build: it would link the BUILD platform's binary.
    # We copy the target platform's binary out by hand instead.
    && npm install --global --omit=optional --ignore-scripts --force \
       "@anthropic-ai/claude-code@${CLAUDE_VERSION}" \
       "@anthropic-ai/claude-code-linux-${native_arch}@${CLAUDE_VERSION}" \
    && install -m 0755 \
       "/usr/local/lib/node_modules/@anthropic-ai/claude-code-linux-${native_arch}/claude" \
       /out-claude

FROM --platform=$BUILDPLATFORM node:22-bookworm-slim AS codex
ARG TARGETARCH
ARG CODEX_VERSION=0.159.0
RUN case "$TARGETARCH" in amd64) native_arch=x64; triple=x86_64 ;; arm64) native_arch=arm64; triple=aarch64 ;; *) exit 1 ;; esac \
    && npm install --prefix /codex --ignore-scripts --force "@openai/codex@${CODEX_VERSION}-linux-${native_arch}" \
    && install -m 0755 "/codex/node_modules/@openai/codex/vendor/${triple}-unknown-linux-musl/bin/codex" /out-codex

FROM --platform=$BUILDPLATFORM python:3.14-slim AS dependencies
ARG TARGETARCH
COPY core/requirements-stock.lock /requirements.txt
RUN case "$TARGETARCH" in amd64) wheel_arch=x86_64 ;; arm64) wheel_arch=aarch64 ;; *) exit 1 ;; esac \
    && pip install --no-cache-dir --no-compile --target=/packages --only-binary=:all: \
       --platform "manylinux_2_28_${wheel_arch}" --platform "manylinux_2_27_${wheel_arch}" \
       --platform "manylinux2014_${wheel_arch}" --python-version=3.14 --implementation=cp --abi=cp314 \
       -r /requirements.txt \
    && useradd --uid 10001 --create-home stock \
    && mkdir -p /runtime-data /runtime-home/.claude /runtime-home/.stock-codex \
    && touch /runtime-data/.keep /runtime-home/.claude/.keep \
    && chmod 700 /runtime-home/.claude

FROM python:3.14-slim
LABEL org.opencontainers.image.title="Stock Quant AI Portfolio" \
      org.opencontainers.image.version="3.23.0" \
      org.opencontainers.image.description="Global KR/US multi-horizon AI quant portfolio scheduler, profit-locking risk engine, and Toss execution"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/core \
    TZ=Asia/Seoul SQT_DB_PATH=/data/stock.sqlite3 HOME=/home/stock \
    CLAUDE_CONFIG_DIR=/home/stock/.claude SQT_CODEX_HOME=/home/stock/.stock-codex CODEX_HOME=/home/stock/.stock-codex
WORKDIR /app
COPY --from=claude /out-claude /usr/local/bin/claude
COPY --from=codex /out-codex /usr/local/bin/codex
COPY --from=dependencies /packages /usr/local/lib/python3.14/site-packages
COPY --from=dependencies /etc/passwd /etc/passwd
COPY --from=dependencies /etc/group /etc/group
COPY --from=dependencies --chown=10001:10001 /runtime-data /data
COPY --from=dependencies --chown=10001:10001 /runtime-home /home/stock
COPY core/sqt /app/core/sqt
COPY stock-web /app/stock-web
USER 10001:10001
EXPOSE 8080
CMD ["python", "-m", "uvicorn", "sqt.api:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
