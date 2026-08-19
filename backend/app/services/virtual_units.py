from __future__ import annotations

from typing import Mapping, Optional

from sqlalchemy.orm import Session

from .. import models


def get_conversion_factor(item: models.Item) -> float:
    try:
        return float(getattr(item, "conversion_factor_to_parent", 0) or 0)
    except Exception:
        return 0.0


def is_virtual_variant(item: models.Item) -> bool:
    return bool(
        getattr(item, "is_virtual_variant", False)
        and getattr(item, "parent_item_id", None)
        and get_conversion_factor(item) > 0
    )


def get_parent_item(
    db: Session,
    item: models.Item,
    item_map: Optional[Mapping[int, models.Item]] = None,
) -> Optional[models.Item]:
    parent_id = getattr(item, "parent_item_id", None)
    if not parent_id:
        return None

    if item_map and parent_id in item_map:
        return item_map[parent_id]

    return db.query(models.Item).get(parent_id)


def get_stock_source_item(
    db: Session,
    item: models.Item,
    item_map: Optional[Mapping[int, models.Item]] = None,
) -> models.Item:
    if not is_virtual_variant(item):
        return item

    parent = get_parent_item(db, item, item_map=item_map)
    return parent or item


def get_required_stock_qty(item: models.Item, qty: float) -> float:
    qty = float(qty or 0)
    if not is_virtual_variant(item):
        return qty

    return qty * get_conversion_factor(item)


def get_effective_buy_price(
    db: Session,
    item: models.Item,
    item_map: Optional[Mapping[int, models.Item]] = None,
) -> float:
    if not is_virtual_variant(item):
        return float(item.buy_price or 0)

    parent = get_parent_item(db, item, item_map=item_map)
    factor = get_conversion_factor(item)
    if not parent or factor <= 0:
        return float(item.buy_price or 0)

    return float(parent.buy_price or 0) * factor


def get_effective_stock_from_source(item: models.Item, source_stock: float) -> float:
    source_stock = float(source_stock or 0)
    if not is_virtual_variant(item):
        return source_stock

    factor = get_conversion_factor(item)
    if factor <= 0:
        return 0.0

    return source_stock / factor
