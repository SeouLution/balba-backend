import logging
from dataclasses import dataclass
from decimal import Decimal
from functools import cached_property

from sqlalchemy import delete, insert, select

from app.bootstrap import Container
from app.infrastructure.db.tables import ingredient, menu, recipe_item
from app.infrastructure.db.uow import UoW
from app.infrastructure.jobs import queue
from app.infrastructure.providers.ports import ProviderInvalidPayload, RecipeDraft
from app.modules import stores
from app.shared.types import (
    INGREDIENT_UNITS,
    Conflict,
    InternalFailure,
    InvalidRequest,
    NotFound,
    new_id,
    normalize_name,
)

logger = logging.getLogger(__name__)
RECIPE_BATCH_SIZE = 20


@dataclass(frozen=True)
class MenuSnapshot:
    id: str
    store_id: str
    name: str


@dataclass(frozen=True)
class IngredientSnapshot:
    id: str
    store_id: str
    name: str
    unit: str


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
class IngredientResolution:
    source_key: str
    existing_ingredient_id: str | None
    new_name: str | None
    unit: str


@dataclass(frozen=True)
class RecipeReplacement:
    menu_id: str
    items: tuple[tuple[str, Decimal], ...]


@dataclass(frozen=True)
class RecipeList:
    generation_status: str
    catalog: CatalogSnapshot | None


def _menu(row) -> MenuSnapshot:
    return MenuSnapshot(row.id, row.store_id, row.name)


def _ingredient(row) -> IngredientSnapshot:
    return IngredientSnapshot(row.id, row.store_id, row.name, row.unit)


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


async def upsert_imported_menus(uow: UoW, store_id: str, names: list[str]) -> dict[str, MenuSnapshot]:
    """이름으로 upsert한다. 기존 메뉴와 레시피는 그대로 둔다."""
    existing = {item.name for item in (await snapshot(uow, store_id)).menus}
    for name in names:
        if name not in existing:
            await uow.execute(insert(menu).values(id=new_id("mn"), store_id=store_id, name=name))
    return {item.name: item for item in (await snapshot(uow, store_id)).menus}


async def menus_without_recipe(uow: UoW, store_id: str) -> list[MenuSnapshot]:
    catalog = await snapshot(uow, store_id)
    return [item for item in catalog.menus if item.id not in catalog.lines_by_menu]


async def resolve_receipt_ingredients(
    uow: UoW, store_id: str, resolutions: list[IngredientResolution]
) -> dict[str, IngredientSnapshot]:
    """영수증 확정에서만 호출한다. 새 재료는 영수증 줄의 단위로 만든다."""
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
            target_id = new_id("ing")
            await uow.execute(
                insert(ingredient).values(
                    id=target_id, store_id=store_id, name=resolution.new_name, unit=resolution.unit
                )
            )
        else:
            target_id = target.id
        resolved[resolution.source_key] = target_id
    catalog = await snapshot(uow, store_id)
    return {source_key: catalog.ingredient_by_id[target_id] for source_key, target_id in resolved.items()}


async def _fail_if_generation_dead(uow: UoW, store_id: str) -> None:
    latest = await queue.latest_for_store(uow, "GENERATE_RECIPES", store_id)
    if latest is not None and latest.state == "DEAD":
        raise InternalFailure("recipe generation is DEAD")


async def list_recipes(deps: Container, store_id: str) -> RecipeList:
    async with deps.db.read() as uow:
        store = await stores.require(uow, store_id)
        await _fail_if_generation_dead(uow, store_id)
        if store.generation_status != "READY":
            return RecipeList("GENERATING", None)
        return RecipeList("READY", await snapshot(uow, store_id))


async def create_ingredient(deps: Container, store_id: str, name: str, unit: str) -> IngredientSnapshot:
    name = normalize_name(name)
    if not 1 <= len(name) <= 40:
        raise InvalidRequest("name must be 1-40 characters")
    if unit not in INGREDIENT_UNITS:
        raise InvalidRequest("unit must be 개 or g")
    async with deps.db.write() as uow:
        await stores.lock(uow, store_id)
        exists = (
            await uow.execute(
                select(ingredient.c.id).where(ingredient.c.store_id == store_id, ingredient.c.name == name)
            )
        ).first()
        if exists is not None:
            raise Conflict("ingredient name already exists")
        created = IngredientSnapshot(new_id("ing"), store_id, name, unit)
        await uow.execute(insert(ingredient).values(id=created.id, store_id=store_id, name=name, unit=unit))
        return created


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
        for replacement in replacements:
            await uow.execute(delete(recipe_item).where(recipe_item.c.menu_id == replacement.menu_id))
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
        if any(item.usage_per_menu < 0 or not item.ingredient_name for item in draft_menu.items):
            raise ProviderInvalidPayload("recipe draft has an invalid item")
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
    known_units = {item.name: item.unit for item in catalog.ingredients}
    for start in range(0, len(pending), RECIPE_BATCH_SIZE):
        batch = pending[start : start + RECIPE_BATCH_SIZE]
        draft = await deps.providers.recipes.draft_recipes(store.category, [item.name for item in batch], known_units)
        drafts.append(validated_recipe_draft(draft, {item.name for item in batch}))
        for draft_menu in draft.menus:
            for item in draft_menu.items:
                if item.unit in INGREDIENT_UNITS:
                    known_units.setdefault(normalize_name(item.ingredient_name), item.unit)

    async with deps.db.write() as uow:
        await stores.lock(uow, store_id)
        saved_lines = 0
        for draft in drafts:
            saved_lines += await _save_recipe_draft(uow, store_id, draft)
        catalog = await snapshot(uow, store_id)
        if pending and saved_lines == 0 and not catalog.recipe_lines:
            raise ProviderInvalidPayload("recipe draft produced no usable recipe item")
        await stores.set_generation_status(uow, store_id, "READY")
        await queue.succeed(uow, job)


async def _save_recipe_draft(uow: UoW, store_id: str, draft: RecipeDraft) -> int:
    catalog = await snapshot(uow, store_id)
    menu_by_name = {item.name: item for item in catalog.menus}
    unit_by_name = {item.name: item.unit for item in catalog.ingredients}
    ingredient_id_by_name = {item.name: item.id for item in catalog.ingredients}
    saved_lines = 0
    for draft_menu in draft.menus:
        target_menu = menu_by_name[draft_menu.menu_name]
        if target_menu.id in catalog.lines_by_menu:
            continue
        rows: dict[str, Decimal] = {}
        for item in draft_menu.items:
            name = normalize_name(item.ingredient_name)
            if item.unit not in INGREDIENT_UNITS or unit_by_name.setdefault(name, item.unit) != item.unit:
                continue
            if name not in ingredient_id_by_name:
                ingredient_id_by_name[name] = new_id("ing")
                await uow.execute(
                    insert(ingredient).values(
                        id=ingredient_id_by_name[name], store_id=store_id, name=name, unit=item.unit
                    )
                )
            rows[ingredient_id_by_name[name]] = item.usage_per_menu
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
