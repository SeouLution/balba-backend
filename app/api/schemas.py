"""요청 DTO. 공개 필드 이름은 openapi.yaml을 따르고, 모르는 추가 키는 무시한다."""

import re
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, StrictBool, StrictStr, StringConstraints

from app.shared.types import to_qty

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _iso_date(value) -> date:
    if not isinstance(value, str) or not ISO_DATE.match(value):
        raise ValueError("date must be YYYY-MM-DD")
    return date.fromisoformat(value)


Qty = Annotated[Decimal, BeforeValidator(to_qty)]
IsoDate = Annotated[date, BeforeValidator(_iso_date)]
LocalTimeText = Annotated[StrictStr, StringConstraints(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")]
Day = Literal["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
Category = Literal["BUNSIK", "KOREAN_MEAL", "GUKBAP", "BANCHAN", "BAKERY", "OTHER"]


class DayHoursIn(BaseModel):
    day: Day
    closed: StrictBool
    open: LocalTimeText | None = None
    close: LocalTimeText | None = None


class AddressIn(BaseModel):
    roadAddress: StrictStr


class StoreCreateIn(BaseModel):
    name: StrictStr
    category: Category
    address: AddressIn
    businessHours: list[DayHoursIn]


class CredentialsIn(BaseModel):
    phoneNumber: Annotated[StrictStr, StringConstraints(pattern=r"^01[0-9]{8,9}$")]
    password: Annotated[StrictStr, StringConstraints(min_length=4, max_length=128)]


class PushTokenIn(BaseModel):
    token: StrictStr


class RecipeItemIn(BaseModel):
    ingredientId: StrictStr
    quantity: Qty


class RecipeMenuIn(BaseModel):
    menuId: StrictStr
    items: list[RecipeItemIn]


class RecipeBatchIn(BaseModel):
    items: list[RecipeMenuIn]


class QuantityIn(BaseModel):
    quantity: Qty


class SendRecordIn(BaseModel):
    channel: Literal["SMS", "KAKAOTALK", "COPY"]
    recipientPhone: StrictStr | None = None


class OrderDaysItemIn(BaseModel):
    supplierId: StrictStr
    orderDays: list[Day]


class OrderDaysIn(BaseModel):
    items: list[OrderDaysItemIn]


class ReceiptAnswerIn(BaseModel):
    questionId: StrictStr
    answer: Literal["SAME", "DIFFERENT"]


class ReceiptConfirmIn(BaseModel):
    answers: list[ReceiptAnswerIn] = []


class PurchaseOrderLineIn(BaseModel):
    ingredientId: StrictStr
    quantity: Qty


class PurchaseOrderSupplierIn(BaseModel):
    supplierId: StrictStr
    lines: list[PurchaseOrderLineIn]


class PurchaseOrderCreateIn(BaseModel):
    targetDate: IsoDate
    suppliers: list[PurchaseOrderSupplierIn]
