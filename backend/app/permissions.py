"""Resolusi role, seed kompatibilitas, dan pemetaan request API ke hak akses."""

from typing import Iterable

from fastapi import HTTPException, Request
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from . import models
from .config import settings
from .permission_catalog import AVAILABLE_GRANTS, PERMISSION_INDEX


def role_names(user_or_value) -> list[str]:
    value = getattr(user_or_value, "role", user_or_value) or ""
    return [part.strip().lower() for part in value.split(",") if part.strip()]


def has_role(user_or_value, role_name: str) -> bool:
    return role_name.lower() in role_names(user_or_value)


def effective_grants(db: Session, user: models.User) -> set[tuple[str, str]]:
    if has_role(user, "admin"):
        return set(AVAILABLE_GRANTS)
    names = role_names(user)
    if not names:
        return set()
    rows = (
        db.query(models.RolePermission.permission_key, models.RolePermission.action)
        .join(models.Role, models.Role.id == models.RolePermission.role_id)
        .filter(models.Role.name.in_(names))
        .all()
    )
    return {(row.permission_key, row.action) for row in rows}


def has_permission(db: Session, user: models.User, permission_key: str, action: str) -> bool:
    return has_role(user, "admin") or (permission_key, action) in effective_grants(db, user)


def grant_payload(grants: Iterable[tuple[str, str]]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for key, action in sorted(grants):
        result.setdefault(key, []).append(action)
    return result


def user_from_bearer(request: Request, db: Session) -> models.User | None:
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    token = header.split(" ", 1)[1].strip()
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        username = payload.get("sub")
    except JWTError:
        return None
    if not username:
        return None
    return (
        db.query(models.User)
        .filter(models.User.username == username, models.User.is_active == True)
        .first()
    )


def seed_roles_and_permissions(db: Session) -> None:
    """Seed idempoten sambil mempertahankan akses efektif instalasi lama."""
    known_names = {"admin", "kasir"}
    known_names.update(role.name.strip().lower() for role in db.query(models.Role).all())
    for (value,) in db.query(models.User.role).all():
        known_names.update(role_names(value))

    roles: dict[str, models.Role] = {}
    for name in sorted(n for n in known_names if n):
        role = db.query(models.Role).filter(models.Role.name == name).first()
        if not role:
            role = models.Role(name=name, description="Role hasil migrasi FPOS")
            db.add(role)
            db.flush()
        roles[name] = role

    # Marker di deskripsi membedakan role lama yang perlu baseline satu kali dari
    # role baru/kosong yang sengaja belum diberi izin.
    for name, role in roles.items():
        if "[permissions-seeded]" in (role.description or ""):
            continue
        grants = AVAILABLE_GRANTS
        if name != "admin":
            grants = {
                (key, action)
                for key, action in AVAILABLE_GRANTS
                if not PERMISSION_INDEX[key].get("admin_only")
                and action not in PERMISSION_INDEX[key].get("admin_only_actions", [])
            }
        db.add_all(
            models.RolePermission(role_id=role.id, permission_key=key, action=action)
            for key, action in sorted(grants)
        )
        role.description = f"[permissions-seeded] {role.description or 'Role hasil migrasi FPOS'}"

    # Kompatibilitas modul opname lama: sebelumnya tombol posting dilindungi oleh
    # Item Masuk, sedangkan katalog Opname hanya memiliki Buka/Kunci Tanggal.
    # Beri izin create satu kali kepada role lama yang memang memiliki KEDUANYA;
    # izin pembatalan tetap harus diberikan admin secara eksplisit.
    for role in roles.values():
        grants = {
            (row.permission_key, row.action)
            for row in db.query(models.RolePermission).filter(
                models.RolePermission.role_id == role.id
            ).all()
        }
        additions = []
        if ("inventory.item_in", "create") in grants and ("inventory.stock_opname", "view") in grants:
            additions.append(("inventory.stock_opname", "create"))
        if ("inventory.item_in", "create") in grants and ("inventory.opening_stock", "view") in grants:
            additions.append(("inventory.opening_stock", "create"))
        for key, action in additions:
            if (key, action) not in grants:
                db.add(models.RolePermission(role_id=role.id, permission_key=key, action=action))
    db.commit()


def request_permission(path: str, method: str) -> tuple[str, str] | None:
    """Petakan endpoint API ke izin referensi yang paling dekat."""
    if not path.startswith("/api/"):
        return None
    relative = path[5:].strip("/")
    if not relative:
        return None
    parts = relative.split("/")
    prefix = parts[0]
    tail = "/".join(parts[1:]).lower()

    if prefix == "auth":
        if tail in {"login", "me", "permissions/me"} or (
            tail.startswith("users/") and tail.endswith("/password")
        ):
            return None
        return ("settings.user_management", "access")
    if prefix == "license" and tail in {"status", "hardware-id", "activate", "upload-proof"}:
        return None
    if prefix == "license" and (
        tail == "generate-key" or tail.startswith("developer/")
    ):
        return ("__admin__", "access")
    # Agen printer Windows tidak memakai JWT browser, tetapi endpoint agent/*
    # memvalidasi X-Printer-Token sendiri. Endpoint print lainnya tetap RBAC.
    if prefix == "print":
        if tail.startswith("agent/"):
            return None
    # Dokumen persediaan menentukan izin dari tipe dokumen di payload/record.
    # Endpoint ini melakukan pemeriksaan granular di route/service agar user
    # Item Keluar tidak salah diwajibkan memiliki izin Item Masuk.
    if prefix == "inventory" and (
        tail.startswith("documents")
        or tail in {"item-snapshot", "document-accounts", "document-warehouses", "adjust"}
    ):
        return None

    action = {
        "GET": "view",
        "POST": "create",
        "PUT": "update",
        "PATCH": "update",
        "DELETE": "delete",
    }.get(method.upper())
    if not action:
        return None

    # Operasi berbasis POST yang secara semantik merupakan ubah/hapus/baca.
    if any(word in tail for word in ("cancel", "delete", "bersihkan")):
        action = "delete"
    elif any(
        word in tail
        for word in (
            "pay",
            "close",
            "reopen",
            "reverse",
            "receive",
            "complete",
            "return",
            "transfer-balance",
            "apply-ppn",
            "reconcile",
            "status",
            "prepare",
            "reset",
            "report-sold",
            "split-fulfill",
        )
    ):
        action = "update"
    elif any(word in tail for word in ("print", "generate", "calculate", "simulate", "export", "test")):
        action = "view"

    key = {
        "items": "master.item",
        "customers": "master.customer",
        "suppliers": "master.supplier",
        "purchases": "purchase.transaction",
        "sales": "sales.transaction",
        "inventory": "inventory.item_in",
        "reports": "report.sales",
        "accounting": "accounting.journal",
        "consignment": "inventory.item_in",
        "shifts": "sales.cashier",
        "backup": "settings.backup",
        "ai": "report.sales",
        "email-backup": "settings.backup",
        "updater": "settings.general",
        "license": "settings.general",
        "warehouses": "inventory.transfer",
        "assembly": "assembly.transaction",
        "notification": "settings.general",
        "discounts": "master.discount_period",
        "onboarding": "settings.company",
        "unit-conversion": "master.unit",
        "barcode": "master.barcode",
        "delivery": "sales.transaction",
        "trade-in": "sales.trade_in",
        "ai-bangunan": "report.sales",
        "branches": "master.warehouse",
        "employees": "settings.user_management",
        "sticker": "master.barcode",
        "stiker": "master.barcode",
        "po": "purchase.order",
        "print": "settings.general",
    }.get(prefix)

    if prefix == "items":
        if tail.startswith("categories"):
            key = "master.type"
        elif tail.startswith("brands"):
            key = "master.brand"
        elif tail.startswith("units"):
            key = "master.unit"
        elif "import" in tail:
            key, action = "settings.import", "access"
    elif prefix == "customers" and tail.startswith("groups"):
        key = "master.customer_group"
    elif prefix == "customers" and "transfer-balance" in tail:
        key, action = "sales.receivable", "update"
    elif prefix == "suppliers" and "salesperson" in tail:
        key = "master.salesperson"
    elif prefix == "purchases":
        if "item-history" in tail:
            key, action = "purchase.price_history", "view"
        elif tail.endswith("/pay"):
            key, action = "purchase.payable", "update"
        elif tail.endswith("/cancel"):
            key, action = "purchase.transaction", "delete"
    elif prefix == "sales":
        if "item-history" in tail:
            key, action = "sales.sale_price_history", "view"
        elif tail.startswith("print"):
            key, action = "sales.continue_print", "view"
        elif tail.endswith("/cancel"):
            key, action = "sales.cancel_detail", "view"
        elif method.upper() == "POST" and not tail:
            key = "sales.cashier"
    elif prefix == "print" and not tail:
        key = "master.barcode"
    elif prefix == "returns":
        key = "purchase.return" if "purchase" in tail else "sales.return"
        if "swap" in tail:
            key, action = "sales.return", "update"
    elif prefix == "inventory":
        if "movements" in tail:
            key, action = "master.stock_card", "view"
        elif "adjust" in tail:
            key = "inventory.item_in"
    elif prefix == "reports":
        if any(word in tail for word in ("profit", "payable", "receivable", "deposit")):
            key = "report.financial"
        elif "purchase" in tail:
            key = "report.purchase"
        elif "inventory" in tail or "top-items" in tail:
            key = "report.inventory"
    elif prefix == "ai" and tail in {"anomaly", "report"}:
        key, action = "report.financial", "view"
    elif prefix == "accounting":
        if "seed-default" in tail or (
            "pkp-status" in tail and method.upper() != "GET"
        ):
            key, action = "settings.company", "access"
        elif tail.startswith("accounts"):
            key = "accounting.accounts"
        elif tail.startswith("cash-transactions"):
            key = "accounting.cash_in"
        elif tail.startswith("ledger"):
            key = "accounting.ledger"
        elif tail.startswith("cash-flow"):
            key = "accounting.cash_flow_category"
        elif any(word in tail for word in ("trial-balance", "income-statement", "balance-sheet", "ppn-report")):
            key, action = "report.financial", "view"
        elif "book-close" in tail:
            key = "accounting.annual_process"
        elif "reconcile-inventory-value" in tail:
            key, action = "inventory.repair_balance", "view"
    elif prefix == "warehouses" and "transfers" not in tail:
        key = "master.warehouse"
    elif prefix == "assembly":
        if tail.startswith("orders"):
            key = "assembly.order"
        elif tail.startswith("results"):
            key = "assembly.finished_goods"
        else:
            key = "assembly.transaction"
    elif prefix == "backup":
        if "import" in tail:
            key, action = "settings.restore", "access"
        elif "auto" in tail:
            key, action = "settings.auto_backup", "access"
        else:
            key, action = "settings.backup", "access"
    elif prefix == "email-backup":
        key, action = "settings.auto_backup", "access"

    if not key:
        return None

    permission = PERMISSION_INDEX.get(key)
    if not permission or not permission["available"]:
        return None
    if action not in permission["actions"]:
        # Izin satu-kolom (Akses/Tampil) melindungi seluruh operasi modul.
        action = permission["actions"][0]
    return key, action


def require_catalog_permission(
    db: Session,
    user: models.User,
    permission_key: str,
    action: str,
) -> None:
    if not has_permission(db, user, permission_key, action):
        label = PERMISSION_INDEX.get(permission_key, {}).get("label", permission_key)
        raise HTTPException(
            status_code=403,
            detail=f"Akses ditolak: tidak memiliki izin {action} untuk {label}",
        )
