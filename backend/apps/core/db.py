"""Database hardening for Supabase.

Supabase exposes tables in `public` through its Data API (PostgREST) to the anon/authenticated
roles. Our clients must only ever talk to Django. Enabling RLS with *no policies* makes every table
deny-all for those roles, while Django's connection role (table owner) is unaffected.
"""

import logging

from django.db import connection

logger = logging.getLogger(__name__)


def enable_rls_on_all_tables(**_kwargs: object) -> None:
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT c.relname FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = current_schema() AND c.relkind = 'r' AND NOT c.relrowsecurity
            """
        )
        tables = [row[0] for row in cur.fetchall()]
        for table in tables:
            cur.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')  # noqa: S608 - names from catalog
    if tables:
        logger.info("Enabled RLS (deny-all) on %d tables", len(tables))
