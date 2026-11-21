import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import insert, select, update

from app.bootstrap import Container
from app.infrastructure.db.tables import receipt_line, receipt_scan
from app.infrastructure.db.uow import UoW
from app.infrastructure.jobs import queue
from app.infrastructure.providers.ports import ProviderInvalidPayload, ReceiptReading
from app.modules import catalog, stores, suppliers
from app.shared import texts
from app.shared.types import INGREDIENT_UNITS, QTY_STEP, InvalidRequest, NotFound, new_id, normalize_name

MAX_IMAGES = 30
JPEG_SIGNATURE = b"\xff\xd8\xff"


@dataclass(frozen=True)
class ReceiptLot:
    receipt_line_id: int
    ingredient_id: str
    supplier_id: str | None
    purchased_on: date
    quantity: Decimal
    unit_price: Decimal | None
    product_name: str | None
    source: str = "RECEIPT"
    is_stock_reset: bool = False


@dataclass(frozen=True)
class LatestPurchaseFact:
    ingredient_id: str
    supplier_id: str
    product_name: str
    unit_price: Decimal | None
    purchased_on: date
    receipt_line_id: int


@dataclass(frozen=True)
class ReceiptScanView:
    id: str
    status: str
    groups: list[dict] | None
    questions: list[dict] | None
    notices: list[str] | None


# ───────── 읽기 포트 ─────────


async def lots(uow: UoW, store_id: str, through: date | None = None) -> list[ReceiptLot]:
    statement = (
        select(receipt_line)
        .where(receipt_line.c.store_id == store_id)
        .order_by(receipt_line.c.purchased_on, receipt_line.c.id)
    )
    if through is not None:
        statement = statement.where(receipt_line.c.purchased_on <= through)
    return [
        ReceiptLot(
            row.id,
            row.ingredient_id,
            row.supplier_id,
            row.purchased_on,
            row.quantity,
            row.unit_price,
            row.product_name,
            row.source,
            row.is_stock_reset,
        )
        for row in (await uow.execute(statement)).all()
    ]


async def latest(uow: UoW, store_id: str) -> dict[str, LatestPurchaseFact]:
    """재료별로 가장 최근 줄 하나에서 거래처·품명·단가를 함께 가져온다. 더 오래된 줄의 단가를 끌어오지 않는다."""
    facts: dict[str, LatestPurchaseFact] = {}
    for lot in await lots(uow, store_id):
        if lot.source != "RECEIPT":
            continue
        facts[lot.ingredient_id] = LatestPurchaseFact(
            lot.ingredient_id, lot.supplier_id, lot.product_name, lot.unit_price, lot.purchased_on, lot.receipt_line_id
        )
    return facts


async def record_manual_purchase(
    deps: Container,
    store_id: str,
    ingredient_id: str,
    purchased_on: date | None,
    quantity: Decimal,
    is_stock_reset: bool,
) -> tuple[ReceiptLot, catalog.IngredientSnapshot]:
    ctx = deps.clock.context(store_id)
    purchased_on = purchased_on or ctx.local_date
    if purchased_on > ctx.local_date:
        raise InvalidRequest("purchasedOn cannot be in the future")
    if quantity < 0 or (quantity == 0 and not is_stock_reset):
        raise InvalidRequest("quantity must be positive")
    async with deps.db.write() as uow:
        await stores.lock(uow, store_id)
        target = (await catalog.snapshot(uow, store_id)).ingredient_by_id.get(ingredient_id)
        if target is None:
            raise NotFound("ingredient")
        line_id = (
            await uow.execute(
                insert(receipt_line)
                .values(
                    store_id=store_id,
                    source="MANUAL",
                    is_stock_reset=is_stock_reset,
                    supplier_id=None,
                    purchased_on=purchased_on,
                    ingredient_id=ingredient_id,
                    product_name=None,
                    quantity=quantity,
                    unit_price=None,
                )
                .returning(receipt_line.c.id)
            )
        ).scalar_one()
        lot = ReceiptLot(line_id, ingredient_id, None, purchased_on, quantity, None, None, "MANUAL", is_stock_reset)
        return lot, target


# ───────── 접수 ─────────


async def create_scan(deps: Container, store_id: str, images: list) -> str:
    async with deps.db.read() as uow:
        await stores.require(uow, store_id)
    if not 1 <= len(images) <= MAX_IMAGES:
        raise InvalidRequest("images must be 1 to 30 files")
    keys: list[str] = []
    remaining_total = deps.policy.receipt_images_total_max_bytes
    try:
        for image in images:
            file_ref = await deps.files.put(
                store_id,
                "receipts",
                "jpg",
                image,
                min(deps.policy.receipt_image_max_bytes, remaining_total),
                "image/jpeg",
                lambda head: head.startswith(JPEG_SIGNATURE),
            )
            keys.append(file_ref.key)
            remaining_total -= file_ref.byte_size
    except BaseException:
        for key in keys:
            deps.files.delete(key)
        raise
    scan_id = new_id("scan")
    async with deps.db.write() as uow:
        await stores.lock(uow, store_id)
        await uow.execute(
            insert(receipt_scan).values(
                id=scan_id, store_id=store_id, status="PROCESSING", images=keys, created_at=deps.clock.now()
            )
        )
        await queue.enqueue(uow, "SCAN_RECEIPTS", {"storeId": store_id, "scanId": scan_id}, f"ocr:{scan_id}")
    return scan_id


# ───────── 읽은 결과 정리 ─────────


def supplier_key(name: str) -> str:
    return "".join(normalize_name(name).split())


def fingerprint(supplier_name: str, purchased_on: date, lines: list[dict]) -> str:
    """사진 한 장의 비교값. 거래처·날짜·(품명, 입고량, 줄 금액) 목록이 같으면 같은 영수증이다."""
    rows = sorted(
        [line["rawProductName"], str(Decimal(line["amount"]).normalize()), line["linePrice"]] for line in lines
    )
    canonical = json.dumps(
        [supplier_key(supplier_name), purchased_on.isoformat(), rows], ensure_ascii=False, default=str
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def build_scan_result(
    readings: list[ReceiptReading | None],
    ingredients: list[catalog.IngredientSnapshot],
    known_suppliers: list[suppliers.SupplierSnapshot],
    product_links: dict[str, str],
    applied_fingerprints: set[str],
    today: date,
) -> dict:
    ingredient_by_name = {item.name: item for item in ingredients}
    supplier_by_key = {supplier_key(item.name): item for item in known_suppliers}
    display_names: dict[str, str] = {}
    questions: dict[str, dict] = {}
    seen_fingerprints: set[str] = set()
    photos: list[dict] = []
    notices: list[str] = []

    for idx, reading in enumerate(readings):
        number = idx + 1
        usable = (
            reading is not None
            and reading.readable
            and bool(reading.supplier_name and reading.supplier_name.strip())
            and reading.purchased_on is not None
            and reading.purchased_on <= today
        )
        if not usable:
            photos.append({"number": number, "status": "UNREADABLE", "applied": False, "lines": []})
            notices.append(texts.unreadable_photo_notice(number))
            continue

        key = supplier_key(reading.supplier_name)
        known = supplier_by_key.get(key)
        supplier_name = display_names.setdefault(key, known.name if known else normalize_name(reading.supplier_name))
        lines = []
        for line in reading.lines:
            raw_name = normalize_name(line.raw_product_name or "")
            if not raw_name or line.amount is None or line.amount <= 0 or line.unit not in INGREDIENT_UNITS:
                continue
            entry = {
                "rawProductName": raw_name,
                "amount": str(line.amount),
                "unit": line.unit,
                "linePrice": line.line_price if line.line_price is not None and line.line_price >= 0 else None,
                "ingredientId": None,
                "questionId": None,
            }
            matched = ingredient_by_name.get(normalize_name(line.match_name or ""))
            if raw_name in product_links:
                entry["ingredientId"] = product_links[raw_name]
            elif line.match_kind == "exact" and matched:
                entry["ingredientId"] = matched.id
            elif line.match_kind == "similar" and matched:
                question = questions.setdefault(
                    raw_name,
                    {"id": new_id("q"), "rawName": raw_name, "candidateId": matched.id, "candidateName": matched.name},
                )
                entry["questionId"] = question["id"]
            lines.append(entry)

        dropped = len(reading.lines) - len(lines)
        if not lines:
            photos.append({"number": number, "status": "UNREADABLE", "applied": False, "lines": []})
            notices.append(texts.unreadable_photo_notice(number))
            continue
        if dropped:
            notices.append(texts.unreadable_lines_notice(reading.purchased_on, supplier_name, dropped))
        photo_fingerprint = fingerprint(supplier_name, reading.purchased_on, lines)
        duplicate = photo_fingerprint in seen_fingerprints or photo_fingerprint in applied_fingerprints
        seen_fingerprints.add(photo_fingerprint)
        if duplicate:
            notices.append(texts.duplicate_receipt_notice(reading.purchased_on, supplier_name))
        photos.append(
            {
                "number": number,
                "status": "DUPLICATE" if duplicate else "READ",
                "applied": False,
                "supplierName": supplier_name,
                "supplierId": known.id if known else None,
                "phone": reading.phone,
                "purchasedOn": reading.purchased_on.isoformat(),
                "fingerprint": photo_fingerprint,
                "lines": lines,
            }
        )

    used_question_ids = {
        line["questionId"]
        for photo in photos
        if photo["status"] == "READ"
        for line in photo["lines"]
        if line["questionId"]
    }
    return {
        "photos": photos,
        "questions": [question for question in questions.values() if question["id"] in used_question_ids],
        "notices": notices,
        "answers": None,
        "final": None,
    }


def public_groups(result: dict, ingredient_by_id: dict[str, catalog.IngredientSnapshot]) -> list[dict]:
    """검토 단계의 공개 묶음. 거래처 이름 순, 영수증은 날짜 순, 줄은 읽은 순서."""
    groups: dict[str, dict] = {}
    for photo in result["photos"]:
        if photo["status"] != "READ":
            continue
        group = groups.setdefault(
            photo["supplierName"],
            {
                "supplier": {
                    "id": photo["supplierId"],
                    "name": photo["supplierName"],
                    "isNew": photo["supplierId"] is None,
                },
                "receipts": {},
            },
        )
        receipt = group["receipts"].setdefault(photo["purchasedOn"], {"purchasedOn": photo["purchasedOn"], "lines": []})
        for line in photo["lines"]:
            linked = ingredient_by_id.get(line["ingredientId"]) if line["ingredientId"] else None
            receipt["lines"].append(
                {
                    "ingredient": {"id": linked.id, "name": linked.name, "isNew": False}
                    if linked
                    else {"id": None, "name": line["rawProductName"], "isNew": True},
                    "quantity": {"value": Decimal(line["amount"]), "unit": line["unit"]},
                }
            )
    return [
        {"supplier": group["supplier"], "receipts": [group["receipts"][day] for day in sorted(group["receipts"])]}
        for _, group in sorted(groups.items())
    ]


async def _applied_fingerprints(uow: UoW, store_id: str, except_scan_id: str | None = None) -> set[str]:
    rows = (
        await uow.execute(
            select(receipt_scan.c.id, receipt_scan.c.result).where(
                receipt_scan.c.store_id == store_id, receipt_scan.c.status == "CONFIRMED"
            )
        )
    ).all()
    return {
        photo["fingerprint"]
        for row in rows
        if row.id != except_scan_id
        for photo in row.result["photos"]
        if photo.get("applied")
    }


async def _require_scan(uow: UoW, store_id: str, scan_id: str):
    row = (
        await uow.execute(select(receipt_scan).where(receipt_scan.c.id == scan_id, receipt_scan.c.store_id == store_id))
    ).first()
    if row is None:
        raise NotFound("receipt scan")
    return row


async def scan_receipts(deps: Container, job: queue.Job) -> None:
    store_id, scan_id = job.payload["storeId"], job.payload["scanId"]
    async with deps.db.read() as uow:
        try:
            row = await _require_scan(uow, store_id, scan_id)
        except NotFound as error:
            raise queue.PermanentJobFailure("receipt scan not found") from error
        ingredients = list((await catalog.snapshot(uow, store_id)).ingredients)
    readings: list[ReceiptReading | None] = []
    if row.status == "PROCESSING":
        ingredient_units = {item.name: item.unit for item in ingredients}
        for key in row.images:
            if not deps.files.path(key).is_file():
                raise queue.PermanentJobFailure("receipt image is missing")
            try:
                readings.append(
                    await deps.providers.receipts.read_receipt(deps.files.read_bytes(key), ingredient_units)
                )
            except ProviderInvalidPayload:
                readings.append(None)

    async with deps.db.write() as uow:
        await stores.lock(uow, store_id)
        current = await _require_scan(uow, store_id, scan_id)
        if current.status == "PROCESSING":
            result = build_scan_result(
                readings,
                list((await catalog.snapshot(uow, store_id)).ingredients),
                await suppliers.list_all(uow, store_id),
                {lot.product_name: lot.ingredient_id for lot in await lots(uow, store_id)},
                await _applied_fingerprints(uow, store_id),
                deps.clock.context(store_id).local_date,
            )
            await uow.execute(
                update(receipt_scan).where(receipt_scan.c.id == scan_id).values(status="NEEDS_REVIEW", result=result)
            )
        await queue.succeed(uow, job)


async def get_scan(deps: Container, store_id: str, scan_id: str) -> ReceiptScanView:
    async with deps.db.read() as uow:
        await stores.require(uow, store_id)
        row = await _require_scan(uow, store_id, scan_id)
        await queue.fail_if_dead(uow, f"ocr:{scan_id}")
        if row.status == "PROCESSING":
            return ReceiptScanView(row.id, row.status, None, None, None)
        if row.status == "CONFIRMED":
            final = row.result["final"]
            return ReceiptScanView(row.id, row.status, _groups_from_json(final["groups"]), [], final["notices"])
        ingredient_by_id = (await catalog.snapshot(uow, store_id)).ingredient_by_id
        questions = [
            {
                "id": question["id"],
                "rawName": question["rawName"],
                "candidate": {"id": question["candidateId"], "name": question["candidateName"]},
            }
            for question in row.result["questions"]
        ]
        return ReceiptScanView(
            row.id, row.status, public_groups(row.result, ingredient_by_id), questions, row.result["notices"]
        )


def _groups_to_json(groups: list[dict]) -> list[dict]:
    return json.loads(json.dumps(groups, default=str))


def _groups_from_json(groups: list[dict]) -> list[dict]:
    for group in groups:
        for receipt in group["receipts"]:
            for line in receipt["lines"]:
                if line["quantity"]:
                    line["quantity"]["value"] = Decimal(line["quantity"]["value"])
    return groups


# ───────── 확정 ─────────


async def confirm_scan(deps: Container, store_id: str, scan_id: str, answers: list[tuple[str, str]]) -> None:
    if len({question_id for question_id, _ in answers}) != len(answers):
        raise InvalidRequest("questionId repeats")
    answer_by_question = dict(answers)
    async with deps.db.write() as uow:
        await stores.lock(uow, store_id)
        row = await _require_scan(uow, store_id, scan_id)
        await queue.fail_if_dead(uow, f"ocr:{scan_id}")
        if row.status == "PROCESSING":
            raise InvalidRequest("receipt scan is still processing")
        result = row.result
        if row.status == "CONFIRMED":
            saved = {item["questionId"]: item["answer"] for item in result["answers"] or []}
            if answers and answer_by_question != saved:
                raise InvalidRequest("receipt scan was confirmed with different answers")
            return
        if set(answer_by_question) != {question["id"] for question in result["questions"]}:
            raise InvalidRequest("every question needs exactly one answer")

        notices = list(result["notices"])
        applied = await _applied_fingerprints(uow, store_id, except_scan_id=scan_id)
        for photo in result["photos"]:
            if photo["status"] == "READ" and photo["fingerprint"] in applied:
                photo["status"] = "DUPLICATE"
                notices.append(
                    texts.duplicate_receipt_notice(date.fromisoformat(photo["purchasedOn"]), photo["supplierName"])
                )
        photos = [photo for photo in result["photos"] if photo["status"] == "READ"]

        supplier_by_name = await _resolve_suppliers(uow, store_id, photos)
        ingredient_by_line = await _resolve_ingredients(
            uow, store_id, photos, result["questions"], answer_by_question, deps
        )

        groups: dict[str, dict] = {}
        for photo in photos:
            target_supplier = supplier_by_name[photo["supplierName"]]
            purchased_on = date.fromisoformat(photo["purchasedOn"])
            group = groups.setdefault(
                target_supplier.name,
                {
                    "supplier": {
                        "id": target_supplier.id,
                        "name": target_supplier.name,
                        "isNew": photo["supplierId"] is None,
                    },
                    "receipts": {},
                },
            )
            receipt = group["receipts"].setdefault(
                photo["purchasedOn"], {"purchasedOn": photo["purchasedOn"], "lines": []}
            )
            skipped = 0
            for line_idx, line in enumerate(photo["lines"]):
                target = ingredient_by_line[(photo["number"], line_idx)]
                if line["unit"] != target.unit:
                    skipped += 1
                    continue
                amount = Decimal(line["amount"])
                await uow.execute(
                    insert(receipt_line).values(
                        store_id=store_id,
                        source="RECEIPT",
                        is_stock_reset=False,
                        supplier_id=target_supplier.id,
                        purchased_on=purchased_on,
                        ingredient_id=target.id,
                        product_name=line["rawProductName"],
                        quantity=amount,
                        unit_price=None
                        if line["linePrice"] is None
                        else (Decimal(line["linePrice"]) / amount).quantize(QTY_STEP, rounding=ROUND_HALF_UP),
                    )
                )
                receipt["lines"].append(
                    {
                        "ingredient": {
                            "id": target.id,
                            "name": target.name,
                            "isNew": line["ingredientId"] is None
                            and answer_by_question.get(line["questionId"]) != "SAME",
                        },
                        "quantity": {"value": line["amount"], "unit": line["unit"]},
                    }
                )
            if skipped:
                notices.append(texts.unreadable_lines_notice(purchased_on, target_supplier.name, skipped))
            photo["applied"] = True

        for target_supplier in {item.id: item for item in supplier_by_name.values()}.values():
            dates = [lot.purchased_on for lot in await lots(uow, store_id) if lot.supplier_id == target_supplier.id]
            await suppliers.estimate_order_days(uow, target_supplier.id, dates, deps.policy)

        result["answers"] = [{"questionId": question_id, "answer": answer} for question_id, answer in answers]
        result["final"] = {
            "groups": _groups_to_json(
                [
                    {
                        "supplier": group["supplier"],
                        "receipts": [group["receipts"][day] for day in sorted(group["receipts"])],
                    }
                    for _, group in sorted(groups.items())
                ]
            ),
            "notices": notices,
        }
        await uow.execute(
            update(receipt_scan).where(receipt_scan.c.id == scan_id).values(status="CONFIRMED", result=result)
        )


async def _resolve_suppliers(uow: UoW, store_id: str, photos: list[dict]) -> dict[str, suppliers.SupplierSnapshot]:
    existing_name_by_key = {supplier_key(item.name): item.name for item in await suppliers.list_all(uow, store_id)}
    drafts: dict[str, suppliers.SupplierDraft] = {}
    for photo in sorted(photos, key=lambda item: item["purchasedOn"]):
        source_key = photo["supplierName"]
        name = existing_name_by_key.get(supplier_key(source_key), source_key)
        previous = drafts.get(source_key)
        phone = photo.get("phone") or (previous.phone if previous else None)
        read_on = (
            date.fromisoformat(photo["purchasedOn"])
            if photo.get("phone")
            else (previous.phone_read_on if previous else None)
        )
        drafts[source_key] = suppliers.SupplierDraft(source_key, name, phone, read_on)
    return await suppliers.resolve(uow, store_id, list(drafts.values()))


async def _resolve_ingredients(
    uow: UoW,
    store_id: str,
    photos: list[dict],
    questions: list[dict],
    answer_by_question: dict[str, str],
    deps: Container,
) -> dict[tuple[int, int], catalog.IngredientSnapshot]:
    candidate_by_question = {question["id"]: question["candidateId"] for question in questions}
    resolutions = []
    for photo in photos:
        for line_idx, line in enumerate(photo["lines"]):
            existing_id = line["ingredientId"]
            if existing_id is None and answer_by_question.get(line["questionId"]) == "SAME":
                existing_id = candidate_by_question[line["questionId"]]
            resolutions.append(
                catalog.IngredientResolution(
                    source_key=f"{photo['number']}:{line_idx}",
                    existing_ingredient_id=existing_id,
                    new_name=None if existing_id else line["rawProductName"],
                    unit=line["unit"],
                )
            )
    resolved = await catalog.resolve_receipt_ingredients(uow, store_id, resolutions)
    return {
        (photo["number"], line_idx): resolved[f"{photo['number']}:{line_idx}"]
        for photo in photos
        for line_idx in range(len(photo["lines"]))
    }
