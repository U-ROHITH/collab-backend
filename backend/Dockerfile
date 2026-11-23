FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings.production

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libpq5 \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev --no-install-project

COPY . .

RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/staticfiles \
    && chown -R app:app /app /opt/venv

USER app

EXPOSE 8000

CMD ["sh", "-c", "python manage.py collectstatic --noinput && exec uvicorn config.asgi:application --host 0.0.0.0 --port 8000 --workers 2 --timeout-keep-alive 30"]
