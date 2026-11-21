from datetime import UTC, datetime

from app.shared.types import KST, StoreContext


class Clock:
    """업무 시계. fixed가 있으면 항상 그 시각을 돌려준다(DEMO_NOW, 테스트)."""

    def __init__(self, fixed: datetime | None = None):
        if fixed is not None and fixed.tzinfo is None:
            raise ValueError("fixed clock value needs a UTC offset")
        self.fixed = fixed

    def now(self) -> datetime:
        return (self.fixed or datetime.now(UTC)).astimezone(UTC)

    def context(self, store_id: str) -> StoreContext:
        now = self.now()
        return StoreContext(store_id=store_id, now=now, local_date=now.astimezone(KST).date())
