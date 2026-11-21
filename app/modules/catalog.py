import logging
from dataclasses import dataclass
from decimal import Decimal
from functools import cached_property

from sqlalchemy import delete, insert, select, update

from app.bootstrap import Container
from app.infrastructure.db.tables import ingredient, menu, recipe_item
from app.infrastructure.db.uow import UoW
from app.infrastructure.jobs import queue
from app.infrastructure.providers.ports import ProviderInvalidPayload, RecipeDraft
from app.modules import stores
from app.shared.policy import Policy
from app.shared.types import InternalFailure, InvalidRequest, NotFound, new_id, normalize_name

logger = logging.getLogger(__name__)
RECIPE_BATCH_SIZE = 20
UNIT_SCALE = {
    ("kg", "g"): Decimal(1000),
    ("g", "kg"): Decimal("0.001"),
    ("l", "ml"): Decimal(1000),
    ("ml", "l"): Decimal("0.001"),
}


@dataclass(frozen=True)
class MenuSnapshot:
    id: str
    store_id: str
    name: str
    unit: str
    price: int | None


@dataclass(frozen=True)
class IngredientSnapshot:
    id: str
    store_id: str
    name: str
    status: str
    usage_unit: str | None
    purchase_unit: str | None
    content_quantity: Decimal | None
    shelf_life_days: int | None
    tags: tuple[str, ...]

    @property
    def convertible(self) -> bool:
        return bool(self.usage_unit and self.purchase_unit and self.content_quantity)


@dataclass(frozen=True)
class RecipeLine:
    menu_id: str
    ingredient_id: str
    quantity: Decimal


@dataclass(frozen=True)
class CatalogSnapshot:
    store_id: str
    menus: tuple[MenuSnapshot, ...]
    ingredients: tuple[IngredientSnapshot, ...]
    recipe_lines: tuple[RecipeLine, ...]

    @cached_property
    def menu_by_id(self) -> dict[str, MenuSnapshot]:
        return {item.id: item for item in self.menus}

    @cached_property
    def ingredient_by_id(self) -> dict[str, IngredientSnapshot]:
        return {item.id: item for item in self.ingredients}

    @cached_property
    def lines_by_menu(self) -> dict[str, tuple[RecipeLine, ...]]:
        grouped: dict[str, list[RecipeLine]] = {}
        for line in self.recipe_lines:
            grouped.setdefault(line.menu_id, []).append(line)
        return {menu_id: tuple(lines) for menu_id, lines in grouped.items()}

    @cached_property
    def menu_ids_by_ingredient(self) -> dict[str, tuple[str, ...]]:
        grouped: dict[str, list[str]] = {}
        for line in self.recipe_lines:
            grouped.setdefault(line.ingredient_id, []).append(line.menu_id)
        return {ingredient_id: tuple(sorted(menu_ids)) for ingredient_id, menu_ids in grouped.items()}

    def ingredients_by_usage(self) -> list[IngredientSnapshot]:
        """쓰이는 메뉴 수 내림차순, 동률은 ingredientId 순."""
        return sorted(self.ingredients, key=lambda item: (-len(self.menu_ids_by_ingredient.get(item.id, ())), item.id))


@dataclass(frozen=True)
class MenuImportDraft:
    name: str
    unit: str
    price: int | None


@dataclass(frozen=True)
class IngredientResolution:
    source_key: str
    existing_ingredient_id: str | None
    new_name: str | None
    usage_unit: str | None = None
    purchase_unit: str | None = None
    content_quantity: Decimal | None = None
    replaces_estimate: bool = False


@dataclass(frozen=True)
class RecipeReplacement:
    menu_id: str
    items: tuple[tuple[str, Decimal], ...]


@dataclass(frozen=True)
class CandidateList:
    generation_status: str
    catalog: CatalogSnapshot | None


def convert_unit(value: Decimal, from_unit: str, to_unit: str) -> Decimal | None:
    if from_unit == to_unit:
        return value
    scale = UNIT_SCALE.get((from_unit.lower(), to_unit.lower()))
    return None if scale is None else value * scale


def _menu(row) -> MenuSnapshot:
    return MenuSnapshot(row.id, row.store_id, row.name, row.unit, row.price)


def _ingredient(row) -> IngredientSnapshot:
    return IngredientSnapshot(
        id=row.id,
        store_id=row.store_id,
        name=row.name,
        status=row.status,
        usage_unit=row.usage_unit,
        purchase_unit=row.purchase_unit,
        content_quantity=row.content_quantity,
        shelf_life_days=row.shelf_life_days,
        tags=tuple(row.tags),
    )


async def snapshot(uow: UoW, store_id: str) -> CatalogSnapshot:
    menus = (await uow.execute(select(menu).where(menu.c.store_id == store_id).order_by(menu.c.id))).all()
    ingredients = (
        await uow.execute(select(ingredient).where(ingredient.c.store_id == store_id).order_by(ingredient.c.id))
    ).all()
    lines = (
        await uow.execute(
            select(recipe_item)
            .join(menu, menu.c.id == recipe_item.c.menu_id)
            .where(menu.c.store_id == store_id)
            .order_by(recipe_item.c.menu_id, recipe_item.c.ingredient_id)
        )
    ).all()
    return CatalogSnapshot(
        store_id=store_id,
        menus=tuple(_menu(row) for row in menus),
        ingredients=tuple(_ingredient(row) for row in ingredients),
        recipe_lines=tuple(RecipeLine(row.menu_id, row.ingredient_id, row.quantity) for row in lines),
    )


async def require_menu(uow: UoW, store_id: str, menu_id: str) -> MenuSnapshot:
    row = (await uow.execute(select(menu).where(menu.c.id == menu_id, menu.c.store_id == store_id))).first()
    if row is None:
        raise NotFound("menu")
    return _menu(row)


async def upsert_imported_menus(uow: UoW, store_id: str, drafts: list[MenuImportDraft]) -> dict[str, MenuSnapshot]:
    """이름으로 upsert한다. 기존 메뉴의 단위·레시피는 그대로 두고 판매가만 새 값으로 바꾼다."""
    existing = {item.name: item for item in (await snapshot(uow, store_id)).menus}
    for draft in drafts:
        current = existing.get(draft.name)
        if current is None:
            await uow.execute(
                insert(menu).values(
                    id=new_id("mn"), store_id=store_id, name=draft.name, unit=draft.unit, price=draft.price
                )
            )
        elif draft.price is not None and draft.price != current.price:
            await uow.execute(update(menu).where(menu.c.id == current.id).values(price=draft.price))
    return {item.name: item for item in (await snapshot(uow, store_id)).menus}


async def menus_without_recipe(uow: UoW, store_id: str) -> list[MenuSnapshot]:
    catalog = await snapshot(uow, store_id)
    return [item for item in catalog.menus if item.id not in catalog.lines_by_menu]


async def resolve_receipt_ingredients(
    uow: UoW, store_id: str, resolutions: list[IngredientResolution], policy: Policy
) -> dict[str, IngredientSnapshot]:
    """영수증 확정에서만 호출한다. 새 재료는 LATER로 만들고, 비어 있는 단위·환산 비율만 채운다."""
    resolved: dict[str, str] = {}
    for resolution in resolutions:
        catalog = await snapshot(uow, store_id)
        by_name = {item.name: item for item in catalog.ingredients}
        target = (
            catalog.ingredient_by_id.get(resolution.existing_ingredient_id)
            if resolution.existing_ingredient_id
            else by_name.get(resolution.new_name)
        )
        if target is None and resolution.existing_ingredient_id:
            raise NotFound("ingredient")
        if target is None:
            shelf_life_days = policy.shelf_life_for(resolution.new_name)
            target_id = new_id("ing")
            await uow.execute(
                insert(ingredient).values(
                    id=target_id,
                    store_id=store_id,
                    name=resolution.new_name,
                    status="LATER",
                    usage_unit=resolution.usage_unit if resolution.content_quantity else None,
                    purchase_unit=resolution.purchase_unit,
                    content_quantity=resolution.content_quantity if resolution.usage_unit else None,
                    shelf_life_days=shelf_life_days,
                    tags=policy.tags_for(resolution.new_name, shelf_life_days, 0),
                )
            )
        else:
            target_id = target.id
            missing = _missing_unit_values(target, resolution)
            if missing:
                await uow.execute(update(ingredient).where(ingredient.c.id == target_id).values(**missing))
        resolved[resolution.source_key] = target_id
    catalog = await snapshot(uow, store_id)
    return {source_key: catalog.ingredient_by_id[target_id] for source_key, target_id in resolved.items()}


def _missing_unit_values(target: IngredientSnapshot, resolution: IngredientResolution) -> dict:
    stated = resolution.purchase_unit and resolution.content_quantity and resolution.usage_unit
    if resolution.replaces_estimate and stated and target.usage_unit:
        # 확정 입고가 없는 재료의 단위는 레시피 초안의 추정값이라, 영수증에 적힌 포장량이 대신한다.
        content = convert_unit(resolution.content_quantity, resolution.usage_unit, target.usage_unit)
        if content is not None:
            return {"purchase_unit": resolution.purchase_unit, "content_quantity": content}
    values: dict = {}
    if target.purchase_unit is None and resolution.purchase_unit:
        values["purchase_unit"] = resolution.purchase_unit
    if target.content_quantity is None and resolution.content_quantity and resolution.usage_unit:
        usage_unit = target.usage_unit or resolution.usage_unit
        content = convert_unit(resolution.content_quantity, resolution.usage_unit, usage_unit)
        if content is not None:
            values["content_quantity"] = content
            values["usage_unit"] = usage_unit
    return values


async def _fail_if_generation_dead(uow: UoW, store_id: str) -> None:
    latest = await queue.latest_for_store(uow, "GENERATE_RECIPES", store_id)
    if latest is not None and latest.state == "DEAD":
        raise InternalFailure("recipe generation is DEAD")


async def list_candidates(deps: Container, store_id: str) -> CandidateList:
    async with deps.db.read() as uow:
        store = await stores.require(uow, store_id)
        await _fail_if_generation_dead(uow, store_id)
        if store.generation_status != "READY":
            return CandidateList("GENERATING", None)
        return CandidateList("READY", await snapshot(uow, store_id))


async def set_core_ingredients(deps: Container, store_id: str, ingredient_ids: list[str]) -> None:
    if not ingredient_ids or len(set(ingredient_ids)) != len(ingredient_ids):
        raise InvalidRequest("ingredientIds must be non-empty and unique")
    async with deps.db.write() as uow:
        store = await stores.lock(uow, store_id)
        if store.generation_status != "READY":
            raise InvalidRequest("recipes are still generating")
        catalog = await snapshot(uow, store_id)
        if any(ingredient_id not in catalog.ingredient_by_id for ingredient_id in ingredient_ids):
            raise NotFound("ingredient")
        await uow.execute(update(ingredient).where(ingredient.c.store_id == store_id).values(status="LATER"))
        await uow.execute(
            update(ingredient)
            .where(ingredient.c.store_id == store_id, ingredient.c.id.in_(ingredient_ids))
            .values(status="CORE")
        )


async def list_recipes(deps: Container, store_id: str) -> CatalogSnapshot | None:
    """GENERATING이면 None. 응답 변환은 CORE 재료 항목만 내보낸다."""
    async with deps.db.read() as uow:
        store = await stores.require(uow, store_id)
        await _fail_if_generation_dead(uow, store_id)
        if store.generation_status != "READY":
            return None
        return await snapshot(uow, store_id)


async def save_recipes(deps: Container, store_id: str, replacements: list[RecipeReplacement]) -> None:
    menu_ids = [item.menu_id for item in replacements]
    if not replacements or len(set(menu_ids)) != len(menu_ids):
        raise InvalidRequest("items must be non-empty with unique menuId")
    for replacement in replacements:
        ingredient_ids = [ingredient_id for ingredient_id, _ in replacement.items]
        if len(set(ingredient_ids)) != len(ingredient_ids):
            raise InvalidRequest("ingredientId repeats inside a menu")
    async with deps.db.write() as uow:
        store = await stores.lock(uow, store_id)
        if store.generation_status != "READY":
            raise InvalidRequest("recipes are still generating")
        catalog = await snapshot(uow, store_id)
        for replacement in replacements:
            if replacement.menu_id not in catalog.menu_by_id:
                raise NotFound("menu")
            if any(ingredient_id not in catalog.ingredient_by_id for ingredient_id, _ in replacement.items):
                raise NotFound("ingredient")
        core_ids = [item.id for item in catalog.ingredients if item.status == "CORE"]
        for replacement in replacements:
            for ingredient_id, _ in replacement.items:
                target = catalog.ingredient_by_id[ingredient_id]
                if target.status != "CORE" or not target.usage_unit:
                    raise InvalidRequest("recipe items must be CORE ingredients with a usage unit")
        for replacement in replacements:
            await uow.execute(
                delete(recipe_item).where(
                    recipe_item.c.menu_id == replacement.menu_id, recipe_item.c.ingredient_id.in_(core_ids)
                )
            )
            if replacement.items:
                await uow.execute(
                    insert(recipe_item),
                    [
                        {"menu_id": replacement.menu_id, "ingredient_id": ingredient_id, "quantity": quantity}
                        for ingredient_id, quantity in replacement.items
                    ],
                )


def validated_recipe_draft(draft: RecipeDraft, sent_menu_names: set[str]) -> RecipeDraft:
    for draft_menu in draft.menus:
        if draft_menu.menu_name not in sent_menu_names:
            raise ProviderInvalidPayload("recipe draft names a menu that was not sent")
        if any(item.usage_per_menu < 0 or not item.usage_unit or not item.ingredient_name for item in draft_menu.items):
            raise ProviderInvalidPayload("recipe draft has an invalid item")
    if any(item.content_quantity is not None and item.content_quantity <= 0 for item in draft.ingredients):
        raise ProviderInvalidPayload("recipe draft has a non-positive content quantity")
    return draft


async def generate_recipes(deps: Container, job: queue.Job) -> None:
    store_id = job.payload["storeId"]
    async with deps.db.read() as uow:
        try:
            store = await stores.require(uow, store_id)
        except NotFound as error:
            raise queue.PermanentJobFailure("store not found") from error
        catalog = await snapshot(uow, store_id)
    pending = [item for item in catalog.menus if item.id not in catalog.lines_by_menu]
    drafts: list[RecipeDraft] = []
    known_names = [item.name for item in catalog.ingredients]
    for start in range(0, len(pending), RECIPE_BATCH_SIZE):
        batch = pending[start : start + RECIPE_BATCH_SIZE]
        draft = await deps.providers.recipes.draft_recipes(
            store.category, [(item.name, item.unit) for item in batch], known_names
        )
        drafts.append(validated_recipe_draft(draft, {item.name for item in batch}))
        known_names = sorted({*known_names, *(normalize_name(item.name) for item in draft.ingredients)})

    async with deps.db.write() as uow:
        await stores.lock(uow, store_id)
        saved_lines = 0
        for draft in drafts:
            saved_lines += await _save_recipe_draft(uow, store_id, draft, deps.policy)
        catalog = await snapshot(uow, store_id)
        if pending and saved_lines == 0 and not catalog.recipe_lines:
            raise ProviderInvalidPayload("recipe draft produced no usable recipe item")
        for item in catalog.ingredients:
            tags = deps.policy.tags_for(
                item.name, item.shelf_life_days, len(catalog.menu_ids_by_ingredient.get(item.id, ()))
            )
            await uow.execute(update(ingredient).where(ingredient.c.id == item.id).values(tags=tags))
        await stores.set_generation_status(uow, store_id, "READY")
        await queue.succeed(uow, job)


async def _save_recipe_draft(uow: UoW, store_id: str, draft: RecipeDraft, policy: Policy) -> int:
    catalog = await snapshot(uow, store_id)
    menu_by_name = {item.name: item for item in catalog.menus}
    ingredient_by_name = {item.name: item for item in catalog.ingredients}
    purchase_by_name = {normalize_name(item.name): item for item in draft.ingredients}
    usage_unit_by_name = {name: item.usage_unit for name, item in ingredient_by_name.items() if item.usage_unit}
    ingredient_id_by_name = {name: item.id for name, item in ingredient_by_name.items()}
    saved_lines = 0
    for draft_menu in draft.menus:
        target_menu = menu_by_name[draft_menu.menu_name]
        if target_menu.id in catalog.lines_by_menu:
            continue
        rows: dict[str, Decimal] = {}
        for item in draft_menu.items:
            name = normalize_name(item.ingredient_name)
            usage_unit = usage_unit_by_name.setdefault(name, item.usage_unit)
            usage = convert_unit(item.usage_per_menu, item.usage_unit, usage_unit)
            if usage is None:
                continue
            if name not in ingredient_id_by_name:
                ingredient_id_by_name[name] = await _insert_drafted_ingredient(
                    uow, store_id, name, usage_unit, purchase_by_name.get(name), policy
                )
            elif name in ingredient_by_name:
                await _fill_drafted_units(uow, ingredient_by_name[name], usage_unit, purchase_by_name.get(name))
            rows[ingredient_id_by_name[name]] = usage
        if rows:
            await uow.execute(
                insert(recipe_item),
                [
                    {"menu_id": target_menu.id, "ingredient_id": ingredient_id, "quantity": quantity}
                    for ingredient_id, quantity in rows.items()
                ],
            )
            saved_lines += len(rows)
    return saved_lines


async def _insert_drafted_ingredient(
    uow: UoW, store_id: str, name: str, usage_unit: str, purchase, policy: Policy
) -> str:
    ingredient_id = new_id("ing")
    await uow.execute(
        insert(ingredient).values(
            id=ingredient_id,
            store_id=store_id,
            name=name,
            status="LATER",
            usage_unit=usage_unit,
            purchase_unit=purchase.purchase_unit if purchase else None,
            content_quantity=purchase.content_quantity if purchase and purchase.purchase_unit else None,
            shelf_life_days=policy.shelf_life_for(name),
            tags=[],
        )
    )
    return ingredient_id


async def _fill_drafted_units(uow: UoW, target: IngredientSnapshot, usage_unit: str, purchase) -> None:
    values: dict = {}
    if target.usage_unit is None:
        values["usage_unit"] = usage_unit
    if purchase and purchase.purchase_unit and target.purchase_unit is None:
        values["purchase_unit"] = purchase.purchase_unit
        if target.content_quantity is None and purchase.content_quantity:
            values["content_quantity"] = purchase.content_quantity
    if values:
        await uow.execute(update(ingredient).where(ingredient.c.id == target.id).values(**values))
