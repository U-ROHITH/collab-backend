FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

ENV DJANGO_SETTINGS_MODULE=config.settings.production
WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev --no-install-project --no-cache

RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/staticfiles \
    && chown app:app /app/staticfiles

COPY --chown=app:app . .

USER app

EXPOSE 8000

CMD ["gunicorn", "config.asgi:application", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "60"]
