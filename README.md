# Collab: Creator Marketplace Backend

Django + DRF API for the brand app, the creator app (React on Vercel), and the ops team.

**Start here:** [docs/README.md](docs/README.md). It covers architecture, system design, security, payments, the data model, and the roadmap.

## Local setup (once code exists)

```bash
cp .env.example backend/.env       # fill with TEST keys only
docker compose up -d               # Postgres + Redis
cd backend && uv sync
uv run python manage.py migrate
uv run python manage.py runserver  # API
uv run celery -A config worker -B -l info   # jobs
cd backend && uv run python manage.py runserver 0.0.0.0:8000
```

Never commit `.env` or paste secrets into chat or tickets. See [docs/03-security.md §8](docs/03-security.md).

## Current status (2026-11-04)
- Phase 0 + most of Phase 1 is built in `backend/`: OTP auth, brands, creators, discovery, lists, cart, campaigns, bids, offers, orders + state machine, deliverables/revisions/disputes/reviews, notifications, and the hardened Django admin.
- Endpoint reference: [backend/API.md](backend/API.md). Swagger at `/v1/docs/` when running locally.
- Tests: `cd backend && uv run pytest` (526 tests, 87% coverage).
