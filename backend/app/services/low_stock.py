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
    *,
    warehouse_id: Optional[int] = None,
    status: Optional[str] = None,
    category_id: Optional[int] = None,
    per_warehouse: bool = False,
) -> List[Dict[str, Any]]:
    """Return active items at/below minimum stock for the selected branch.

    Virtual unit variants use their parent's physical stock converted into the
    variant unit, matching the stock shown by the inventory module.
    """
    warehouses = db.query(models.Warehouse).filter(
            models.Warehouse.branch_id == branch_id
        ).order_by(models.Warehouse.is_default.desc(), models.Warehouse.name).all()
    if warehouse_id:
        warehouses = [row for row in warehouses if row.id == warehouse_id]
    warehouse_ids = [row.id for row in warehouses]

    item_query = db.query(models.Item).filter(models.Item.is_active == True)
    if category_id:
        item_query = item_query.filter(models.Item.category_id == category_id)
    items = item_query.all()
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

    if warehouse_ids and not per_warehouse:
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

    if per_warehouse:
        rows = db.query(models.WarehouseStock).filter(
            models.WarehouseStock.warehouse_id.in_(warehouse_ids)
        ).all() if warehouse_ids else []
        row_map = {(row.warehouse_id, row.item_id): row for row in rows}
    results: List[Dict[str, Any]] = []
    for item in items:
        source_item = get_stock_source_item(db, item, item_map=item_map)
        scopes = warehouses if per_warehouse else [None]
        for warehouse in scopes:
            stock_row = row_map.get((warehouse.id, source_item.id)) if warehouse else None
            source_stock = (float(stock_row.stock or 0) if stock_row else 0.0) if warehouse else stock_map.get(source_item.id, 0.0)
            local_stock = round(get_effective_stock_from_source(item, source_stock), 4)
            minimum_stock = float(
                stock_row.min_stock if stock_row and stock_row.min_stock is not None else item.min_stock or 0
            )
            item_status = "out_of_stock" if local_stock <= 0 else "low"
            if local_stock > minimum_stock or (status and status != item_status):
                continue
            results.append(
                {
                    "id": item.id,
                    "code": item.code,
                    "name": item.name,
                    "stock": local_stock,
                    "min_stock": minimum_stock,
                    "status": item_status,
                    "warehouse_id": warehouse.id if warehouse else None,
                    "warehouse_name": warehouse.name if warehouse else None,
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
