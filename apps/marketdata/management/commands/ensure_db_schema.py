from __future__ import annotations

import os
import re

from django.core.management.base import BaseCommand
from django.db import connection


_SAFE_SCHEMA_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


class Command(BaseCommand):
    help = (
        "Create the PostgreSQL schema named by DJANGO_DB_SCHEMA if it does not "
        "exist.  Run this before 'migrate' on a fresh database so that the "
        "search_path target schema is present before any DDL executes."
    )

    def handle(self, *args, **options):
        schema = (os.getenv("DJANGO_DB_SCHEMA") or "").strip()
        if not schema:
            self.stdout.write("DJANGO_DB_SCHEMA is not set — nothing to do.")
            return

        if not _SAFE_SCHEMA_RE.match(schema):
            raise ValueError(
                f"DJANGO_DB_SCHEMA value {schema!r} is not a valid PostgreSQL "
                "identifier.  Only letters, digits, and underscores are allowed."
            )

        with connection.cursor() as cursor:
            cursor.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')

        self.stdout.write(self.style.SUCCESS(f'Schema "{schema}" is ready.'))
