FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58
WORKDIR /app
ENV UV_LINK_MODE=copy UV_NO_CACHE=1 PATH="/app/.venv/bin:$PATH"
COPY --chown=10001:10001 pyproject.toml uv.lock ./
RUN --mount=type=secret,id=build_ca \
    if [ -f /run/secrets/build_ca ]; then export SSL_CERT_FILE=/run/secrets/build_ca UV_NATIVE_TLS=true; fi && \
    uv sync --locked --no-dev --no-install-project && useradd --system --uid 10001 order
COPY --chown=10001:10001 order_extractor order_extractor
COPY --chown=10001:10001 alembic.ini ./
COPY --chown=10001:10001 migrations migrations
COPY --chown=10001:10001 infra/entrypoint.sh ./
USER 10001
ENV OE_BIND=0.0.0.0
EXPOSE 8100
ENTRYPOINT ["/bin/sh","/app/entrypoint.sh"]
