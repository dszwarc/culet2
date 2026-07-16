from collections.abc import Iterator
from typing import Any

from django.db import connections


OLD_DATABASE_ALIAS = "old_culet"


def fetch_old_rows(
    sql: str,
    params: list[Any] | tuple[Any, ...] | None = None,
) -> list[dict[str, Any]]:
    """
    Execute a read-only query against the restored old Culet MySQL database.

    Results are returned as dictionaries keyed by column name.
    """
    params = params or []

    with connections[OLD_DATABASE_ALIAS].cursor() as cursor:
        cursor.execute(sql, params)

        column_names = [column[0] for column in cursor.description]

        return [
            dict(zip(column_names, row, strict=True))
            for row in cursor.fetchall()
        ]


def iterate_old_rows(
    sql: str,
    params: list[Any] | tuple[Any, ...] | None = None,
    chunk_size: int = 1000,
) -> Iterator[dict[str, Any]]:
    """
    Iterate over a large result set from the old Culet MySQL database
    without loading every row into memory at once.
    """
    params = params or []

    with connections[OLD_DATABASE_ALIAS].cursor() as cursor:
        cursor.execute(sql, params)

        column_names = [column[0] for column in cursor.description]

        while True:
            rows = cursor.fetchmany(chunk_size)

            if not rows:
                break

            for row in rows:
                yield dict(zip(column_names, row, strict=True))