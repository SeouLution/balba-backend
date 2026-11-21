from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine


class UoW:
    """한 트랜잭션의 핸들. 모듈은 이 연결로만 DB를 읽고 쓴다."""

    def __init__(self, conn: AsyncConnection):
        self.conn = conn

    async def execute(self, statement, parameters=None):
        return await self.conn.execute(statement, parameters)


class Database:
    def __init__(self, url: str):
        self.engine = create_async_engine(url, pool_size=5, max_overflow=5)

    @asynccontextmanager
    async def write(self):
        async with self.engine.begin() as conn:
            yield UoW(conn)

    @asynccontextmanager
    async def read(self):
        async with self.engine.connect() as conn:
            conn = await conn.execution_options(isolation_level="REPEATABLE READ", postgresql_readonly=True)
            async with conn.begin():
                yield UoW(conn)

    async def dispose(self) -> None:
        await self.engine.dispose()
