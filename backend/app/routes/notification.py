"""
iPos 5.0 — Notifikasi WA & Telegram
Channels:
  - WhatsApp via Fonnte (fonnte.com) — gratis 100 pesan/bulan
  - Telegram via Bot API (100% gratis)

Events:
  - low_stock: stok item di bawah minimum
  - large_sale: penjualan di atas threshold
  - suspicious_transaction: anomali terdeteksi
  - daily_report: laporan harian otomatis (jam 21:00)
  - shift_close: ringkasan saat shift ditutup
"""
import httpx, json
from datetime import datetime, date, timedelta
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel

from ..database import get_db, SessionLocal
from ..auth import get_current_user, require_admin
from .. import models

router = APIRouter()


# ──────────────────────────────────────────────────────────────────────────────
# CORE SEND FUNCTIONS
# ──────────────────────────────────────────────────────────────────────────────

async def send_telegram(bot_token: str, chat_id: str, message: str) -> dict:
    """Kirim pesan via Telegram Bot API"""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML"
            })
        data = resp.json()
        if data.get("ok"):
            return {"success": True, "message": "Terkirim ke Telegram"}
        return {"success": False, "message": data.get("description", "Error")}
    except Exception as e:
        return {"success": False, "message": str(e)}


async def send_whatsapp_fonnte(api_key: str, target: str, message: str) -> dict:
    """Kirim pesan via Fonnte WhatsApp Gateway"""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.fonnte.com/send",
                headers={"Authorization": api_key},
                data={"target": target, "message": message, "typing": "true"}
            )
        data = resp.json()
        if data.get("status"):
            return {"success": True, "message": "Terkirim ke WhatsApp"}
        return {"success": False, "message": data.get("reason", "Error Fonnte")}
    except Exception as e:
        return {"success": False, "message": str(e)}


async def dispatch_notification(db: Session, event: str, message: str):
    """Kirim ke semua channel yang subscribe event ini"""
    configs = db.query(models.NotificationConfig).filter(
        models.NotificationConfig.is_active == True
    ).all()

    for cfg in configs:
        try:
            events = json.loads(cfg.events) if cfg.events else []
        except Exception:
            events = []

        if event not in events and "all" not in events:
            continue

        success = False
        error = None
        try:
            if cfg.channel == "telegram":
                result = await send_telegram(cfg.api_key, cfg.target, message)
            elif cfg.channel == "whatsapp":
                result = await send_whatsapp_fonnte(cfg.api_key, cfg.target, message)
            else:
                continue
            success = result.get("success", False)
            error = result.get("message") if not success else None
        except Exception as e:
            error = str(e)

        # Log
        db.add(models.NotificationLog(
            channel=cfg.channel, event=event,
            message=message[:500],
            status="sent" if success else "failed",
            error=error
        ))
    db.commit()


# ──────────────────────────────────────────────────────────────────────────────
# CONFIG ROUTES
# ──────────────────────────────────────────────────────────────────────────────

VALID_EVENTS = [
    "low_stock", "large_sale", "suspicious_transaction",
    "daily_report", "shift_close", "new_sale"
]

class NotifConfigCreate(BaseModel):
    channel: str           # telegram | whatsapp
    target: str            # chat_id atau nomor HP
    api_key: str           # bot token atau Fonnte API key
    events: List[str] = ["low_stock", "daily_report"]
    is_active: bool = True


@router.get("/config")
def get_configs(db: Session = Depends(get_db), _=Depends(require_admin)):
    configs = db.query(models.NotificationConfig).all()
    return [{
        "id": c.id, "channel": c.channel, "target": c.target,
        "api_key": c.api_key[:8] + "****" if c.api_key else "",
        "events": json.loads(c.events) if c.events else [],
        "is_active": c.is_active
    } for c in configs]


@router.post("/config")
def create_config(data: NotifConfigCreate, db: Session = Depends(get_db),
                  _=Depends(require_admin)):
    if data.channel not in ["telegram", "whatsapp"]:
        raise HTTPException(400, "Channel harus: telegram atau whatsapp")
    invalid_events = [e for e in data.events if e not in VALID_EVENTS]
    if invalid_events:
        raise HTTPException(400, f"Event tidak valid: {invalid_events}. Valid: {VALID_EVENTS}")

    cfg = models.NotificationConfig(
        channel=data.channel, target=data.target,
        api_key=data.api_key,
        events=json.dumps(data.events),
        is_active=data.is_active
    )
    db.add(cfg); db.commit(); db.refresh(cfg)
    return {"id": cfg.id, "message": "Konfigurasi notifikasi disimpan"}


@router.put("/config/{cfg_id}")
def update_config(cfg_id: int, data: dict, db: Session = Depends(get_db),
                  _=Depends(require_admin)):
    cfg = db.query(models.NotificationConfig).get(cfg_id)
    if not cfg: raise HTTPException(404, "Config tidak ditemukan")
    if "events" in data: data["events"] = json.dumps(data["events"])
    for k, v in data.items():
        if hasattr(cfg, k): setattr(cfg, k, v)
    db.commit()
    return {"message": "Config diperbarui"}


@router.delete("/config/{cfg_id}")
def delete_config(cfg_id: int, db: Session = Depends(get_db), _=Depends(require_admin)):
    cfg = db.query(models.NotificationConfig).get(cfg_id)
    if not cfg: raise HTTPException(404, "Config tidak ditemukan")
    db.delete(cfg); db.commit()
    return {"message": "Config dihapus"}


# ──────────────────────────────────────────────────────────────────────────────
# TEST & MANUAL SEND
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/test/{cfg_id}")
async def test_notification(cfg_id: int, db: Session = Depends(get_db),
                             _=Depends(require_admin)):
    cfg = db.query(models.NotificationConfig).get(cfg_id)
    if not cfg: raise HTTPException(404, "Config tidak ditemukan")

    msg = f"✅ <b>Test Notifikasi iPos 5.0</b>\n\nKonfigurasi berhasil!\nChannel: {cfg.channel}\nWaktu: {datetime.now().strftime('%d/%m/%Y %H:%M')}"

    if cfg.channel == "telegram":
        result = await send_telegram(cfg.api_key, cfg.target, msg)
    elif cfg.channel == "whatsapp":
        result = await send_whatsapp_fonnte(cfg.api_key, cfg.target, msg.replace("<b>","*").replace("</b>","*"))
    else:
        raise HTTPException(400, "Channel tidak dikenal")

    if not result["success"]:
        raise HTTPException(400, result["message"])
    return result


@router.post("/send-low-stock")
async def trigger_low_stock(background_tasks: BackgroundTasks,
                             db: Session = Depends(get_db),
                             _=Depends(get_current_user)):
    """Cek dan kirim notif stok menipis"""
    low_items = db.query(models.Item).filter(
        models.Item.is_active == True,
        models.Item.stock <= models.Item.min_stock,
        models.Item.min_stock > 0
    ).limit(10).all()

    if not low_items:
        return {"message": "Semua stok aman, tidak ada notifikasi dikirim"}

    lines = "\n".join([
        f"• {i.name}: stok {i.stock} (min {i.min_stock})"
        for i in low_items
    ])
    msg = f"⚠️ <b>Peringatan Stok Menipis</b>\n\n{lines}\n\nSegera lakukan restock!"
    background_tasks.add_task(dispatch_notification, db, "low_stock", msg)
    return {"message": f"Notifikasi stok menipis dikirim ({len(low_items)} item)"}


@router.post("/send-daily-report")
async def trigger_daily_report(background_tasks: BackgroundTasks,
                                db: Session = Depends(get_db),
                                _=Depends(require_admin)):
    """Kirim laporan harian manual"""
    today = date.today()
    sales = db.query(models.Sale).filter(models.Sale.date == today).all()
    total = sum(s.total for s in sales)
    tx_count = len(sales)
    cash = sum(s.total for s in sales if s.payment_method == "cash")

    low_stock = db.query(func.count(models.Item.id)).filter(
        models.Item.is_active == True,
        models.Item.stock <= models.Item.min_stock
    ).scalar() or 0

    msg = f"""📊 <b>Laporan Harian iPos 5.0</b>
📅 {today.strftime('%d %B %Y')}

💰 Total Penjualan: Rp {total:,.0f}
🧾 Jumlah Transaksi: {tx_count}
💵 Tunai: Rp {cash:,.0f}
⚠️ Stok Menipis: {low_stock} item

#iPos #LaporanHarian"""

    background_tasks.add_task(dispatch_notification, db, "daily_report", msg)
    return {"message": "Laporan harian dikirim", "total": total, "transactions": tx_count}


# ──────────────────────────────────────────────────────────────────────────────
# LOGS
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/logs")
def get_logs(limit: int = 50, db: Session = Depends(get_db),
             _=Depends(require_admin)):
    logs = db.query(models.NotificationLog).order_by(
        models.NotificationLog.id.desc()
    ).limit(limit).all()
    return [{
        "id": l.id, "channel": l.channel, "event": l.event,
        "message": l.message[:100] + "..." if l.message and len(l.message) > 100 else l.message,
        "status": l.status, "error": l.error,
        "created_at": l.created_at.isoformat() if l.created_at else None
    } for l in logs]


@router.get("/events")
def get_valid_events(_=Depends(get_current_user)):
    return {
        "events": [
            {"id": "low_stock", "label": "Stok Menipis", "description": "Kirim notif saat ada item stok ≤ minimum"},
            {"id": "large_sale", "label": "Penjualan Besar", "description": "Kirim notif saat ada transaksi > threshold"},
            {"id": "suspicious_transaction", "label": "Transaksi Mencurigakan", "description": "Kirim notif saat AI deteksi anomali"},
            {"id": "daily_report", "label": "Laporan Harian", "description": "Kirim ringkasan penjualan setiap hari"},
            {"id": "shift_close", "label": "Tutup Shift", "description": "Kirim ringkasan saat kasir tutup shift"},
            {"id": "new_sale", "label": "Setiap Penjualan", "description": "Kirim notif setiap ada transaksi baru"},
        ]
    }
