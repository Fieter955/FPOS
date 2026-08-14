from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models
from .virtual_units import (
    get_effective_stock_from_source,
    get_stock_source_item,
    is_virtual_variant,
)


def get_low_stock_items(
    db: Session,
    branch_id: Optional[int],
) -> List[Dict[str, Any]]:
    """Return active items at/below minimum stock for the selected branch.

    Virtual unit variants use their parent's physical stock converted into the
    variant unit, matching the stock shown by the inventory module.
    """
    warehouse_ids = [
        warehouse_id
        for (warehouse_id,) in db.query(models.Warehouse.id).filter(
            models.Warehouse.branch_id == branch_id
        ).all()
    ]

    items = db.query(models.Item).filter(models.Item.is_active == True).all()
    item_map = {item.id: item for item in items}
    parent_ids = {
        item.parent_item_id
        for item in items
        if is_virtual_variant(item)
        and item.parent_item_id
        and item.parent_item_id not in item_map
    }
    if parent_ids:
        parents = db.query(models.Item).filter(models.Item.id.in_(parent_ids)).all()
        item_map.update({parent.id: parent for parent in parents})

    if warehouse_ids:
        stock_rows = (
            db.query(
                models.WarehouseStock.item_id,
                func.sum(models.WarehouseStock.stock),
            )
            .filter(models.WarehouseStock.warehouse_id.in_(warehouse_ids))
            .group_by(models.WarehouseStock.item_id)
            .all()
        )
        stock_map = {
            item_id: float(stock or 0) for item_id, stock in stock_rows
        }
    else:
        # Preserve the existing inventory behavior for installations that do
        # not yet have warehouse records.
        stock_map = {
            item_id: float(item.stock or 0)
            for item_id, item in item_map.items()
        }

    results: List[Dict[str, Any]] = []
    for item in items:
        source_item = get_stock_source_item(db, item, item_map=item_map)
        source_stock = stock_map.get(source_item.id, 0.0)
        local_stock = round(
            get_effective_stock_from_source(item, source_stock),
            4,
        )
        minimum_stock = float(item.min_stock or 0)

        if local_stock <= minimum_stock:
            results.append(
                {
                    "id": item.id,
                    "code": item.code,
                    "name": item.name,
                    "stock": local_stock,
                    "min_stock": minimum_stock,
                    "status": "out_of_stock" if local_stock <= 0 else "low",
                }
            )

    # Depleted/negative stock is most urgent. Within each status, show the
    # largest shortage first and use the name as a deterministic tie-breaker.
    results.sort(
        key=lambda item: (
            0 if item["stock"] <= 0 else 1,
            -(item["min_stock"] - item["stock"]),
            str(item["name"] or "").casefold(),
        )
    )
    return results
