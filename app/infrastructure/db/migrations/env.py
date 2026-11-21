import asyncio
import os

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

DATABASE_URL = context.config.get_main_option("sqlalchemy.url") or os.environ["DATABASE_URL"]


def run(connection) -> None:
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_online() -> None:
    engine = create_async_engine(DATABASE_URL)
    async with engine.connect() as connection:
        await connection.run_sync(run)
    await engine.dispose()


asyncio.run(run_online())
