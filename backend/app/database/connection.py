from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.core.config import Settings
from app.core.errors import ConfigurationError


def create_pool(settings: Settings) -> AsyncConnectionPool:
    if not settings.database_url.get_secret_value():
        raise ConfigurationError("DATABASE_URL is not configured")
    return AsyncConnectionPool(
        settings.database_url.get_secret_value(),
        open=False,
        min_size=1,
        max_size=6,
        timeout=10,
        kwargs={"row_factory": dict_row, "autocommit": True, "connect_timeout": 10},
    )
