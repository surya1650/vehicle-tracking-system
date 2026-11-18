FROM node:24-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN --mount=type=secret,id=build_ca \
    if [ -f /run/secrets/build_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/build_ca; fi \
    && npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements-api.txt ./
RUN --mount=type=secret,id=build_ca \
    if [ -f /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi \
    && python -m pip install --no-cache-dir -r requirements-api.txt \
    && useradd --no-create-home --uid 10001 anpr
COPY backend/ ./backend/
COPY alembic.ini ./
COPY --from=frontend /build/dist/ ./frontend/dist/
RUN chmod -R a+rX /app/backend /app/alembic.ini /app/frontend/dist
USER 10001
EXPOSE 8010
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn backend.main:create_app --factory --host 0.0.0.0 --port 8010 --no-access-log"]
