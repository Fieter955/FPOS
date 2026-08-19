"""
iPos 5.0 — AI Konkret untuk Toko Bangunan
Fitur:
  1. Kalkulator Material — estimasi kebutuhan material dari dimensi
  2. Proactive Daily Briefing — kirim ke Telegram setiap hari
  3. Prediksi Restock — kapan barang akan habis
  4. Alert Piutang Overdue — analisis risiko kredit
  5. Pricing Alert — margin drop detection
  6. Weekly Business Review — laporan naratif mingguan

Semua notifikasi via Telegram (bukan WhatsApp).
"""
import json
from datetime import date, timedelta, datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import func

from ..database import get_db, SessionLocal
from ..auth import get_current_user, require_admin
from .. import models
from ..ai_engine import get_engine, build_business_context

router = APIRouter()


# ══════════════════════════════════════════════════════════════════════════════
# MATERIAL CALCULATOR — no AI needed, pure math + AI narration
# ══════════════════════════════════════════════════════════════════════════════

MATERIAL_FORMULAS = {
    "keramik_lantai": {
        "desc": "Pemasangan Keramik Lantai",
        "materials": [
            {"name": "Keramik", "unit": "dus", "formula": "luas / 1.44 * 1.1",
             "note": "1 dus 40x40 isi 6 lembar = 1.44m², waste 10%"},
            {"name": "Semen Perekat", "unit": "sak", "formula": "luas * 0.5",
             "note": "0.5 sak per m²"},
            {"name": "Nat Keramik", "unit": "kg", "formula": "luas * 0.5",
             "note": "0.5 kg per m²"},
            {"name": "Semen Portland", "unit": "sak", "formula": "luas * 0.3",
             "note": "Untuk leveling jika perlu"},
        ]
    },
    "keramik_dinding": {
        "desc": "Pemasangan Keramik Dinding",
        "materials": [
            {"name": "Keramik Dinding", "unit": "dus", "formula": "luas / 1.0 * 1.1",
             "note": "1 dus 25x40 isi 10 lembar = 1m², waste 10%"},
            {"name": "Semen Perekat", "unit": "sak", "formula": "luas * 0.6"},
            {"name": "Nat Keramik", "unit": "kg", "formula": "luas * 0.7"},
        ]
    },
    "plester_dinding": {
        "desc": "Plesteran Dinding",
        "materials": [
            {"name": "Semen Portland", "unit": "sak", "formula": "luas * 0.16",
             "note": "0.16 sak per m² untuk tebal 1.5cm"},
            {"name": "Pasir Pasang", "unit": "m3", "formula": "luas * 0.02"},
        ]
    },
    "pengecatan": {
        "desc": "Pengecatan Dinding",
        "materials": [
            {"name": "Cat Tembok", "unit": "kg", "formula": "luas / 12 * 2",
             "note": "1 kg untuk 12m², 2 lapis"},
            {"name": "Plamir", "unit": "kg", "formula": "luas / 10",
             "note": "Untuk permukaan baru"},
            {"name": "Amplas", "unit": "lembar", "formula": "luas / 5"},
        ]
    },
    "pondasi_batu_kali": {
        "desc": "Pondasi Batu Kali",
        "materials": [
            {"name": "Batu Kali", "unit": "m3", "formula": "panjang * 0.6 * 0.7"},
            {"name": "Semen Portland", "unit": "sak", "formula": "panjang * 1.5"},
            {"name": "Pasir Pasang", "unit": "m3", "formula": "panjang * 0.3"},
        ]
    },
    "beton_cor": {
        "desc": "Beton Cor (K175)",
        "materials": [
            {"name": "Semen Portland", "unit": "sak", "formula": "volume * 7",
             "note": "7 sak per m3 untuk K175"},
            {"name": "Pasir Beton", "unit": "m3", "formula": "volume * 0.5"},
            {"name": "Kerikil/Split", "unit": "m3", "formula": "volume * 0.8"},
        ]
    },
    "rangka_atap_baja_ringan": {
        "desc": "Rangka Atap Baja Ringan",
        "materials": [
            {"name": "Baja Ringan C75", "unit": "batang", "formula": "luas_atap / 0.8",
             "note": "Jarak reng 80cm"},
            {"name": "Sekrup Roofing", "unit": "box", "formula": "luas_atap / 20"},
            {"name": "Genteng Metal", "unit": "lembar", "formula": "luas_atap / 0.7"},
        ]
    },
    "instalasi_listrik": {
        "desc": "Instalasi Listrik Rumah",
        "materials": [
            {"name": "Kabel NYM 2x1.5", "unit": "meter", "formula": "luas * 1.5"},
            {"name": "Pipa Konduit", "unit": "batang", "formula": "luas / 3"},
            {"name": "Stop Kontak", "unit": "buah", "formula": "luas / 6"},
            {"name": "Saklar", "unit": "buah", "formula": "luas / 9"},
        ]
    },
}


import ast as _ast
import operator as _op

# Evaluator aritmetika aman (pengganti eval). Hanya izinkan angka, nama parameter,
# dan operator + - * / % ** serta tanda unary — TANPA akses atribut/builtins/fungsi,
# sehingga tidak bisa dipakai untuk code injection.
_ALLOWED_BINOPS = {
    _ast.Add: _op.add, _ast.Sub: _op.sub, _ast.Mult: _op.mul,
    _ast.Div: _op.truediv, _ast.Mod: _op.mod, _ast.Pow: _op.pow,
}
_ALLOWED_UNARY = {_ast.UAdd: _op.pos, _ast.USub: _op.neg}


def _safe_eval_node(node, params):
    if isinstance(node, _ast.Expression):
        return _safe_eval_node(node.body, params)
    if isinstance(node, _ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, _ast.Name):
        if node.id in params:
            return float(params[node.id])
        raise ValueError(f"variabel tidak dikenal: {node.id}")
    if isinstance(node, _ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
        return _ALLOWED_BINOPS[type(node.op)](
            _safe_eval_node(node.left, params), _safe_eval_node(node.right, params)
        )
    if isinstance(node, _ast.UnaryOp) and type(node.op) in _ALLOWED_UNARY:
        return _ALLOWED_UNARY[type(node.op)](_safe_eval_node(node.operand, params))
    raise ValueError("ekspresi tidak diizinkan")


def evaluate_formula(formula: str, params: dict) -> float:
    """Evaluasi formula material dengan parameter dimensi (aman, tanpa eval)."""
    safe_params = {k: float(v) for k, v in params.items() if v}
    try:
        tree = _ast.parse(formula, mode="eval")
        return float(_safe_eval_node(tree, safe_params))
    except Exception:
        return 0.0


def get_item_price(db: Session, keyword: str) -> Optional[float]:
    """Cari harga item di database berdasarkan keyword"""
    item = db.query(models.Item).filter(
        models.Item.name.ilike(f"%{keyword}%"),
        models.Item.is_active == True
    ).first()
    return item.sell_price if item else None


@router.get("/formulas")
def get_formulas(_=Depends(get_current_user)):
    """Daftar semua formula kalkulator material yang tersedia"""
    return [{
        "id": k,
        "description": v["desc"],
        "material_count": len(v["materials"])
    } for k, v in MATERIAL_FORMULAS.items()]


@router.post("/calculate-material")
def calculate_material(
    data: dict,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    """
    Hitung kebutuhan material berdasarkan pekerjaan dan dimensi.
    Input: {formula_id, dimensions: {luas, panjang, volume, dll}, with_price: bool}
    """
    formula_id = data.get("formula_id")
    dimensions = data.get("dimensions", {})
    with_price = data.get("with_price", True)
    include_waste = data.get("include_waste", True)

    if formula_id not in MATERIAL_FORMULAS:
        raise HTTPException(400, f"Formula tidak ditemukan. Tersedia: {list(MATERIAL_FORMULAS.keys())}")

    formula = MATERIAL_FORMULAS[formula_id]
    results = []
    total_estimate = 0.0

    for mat in formula["materials"]:
        qty_raw = evaluate_formula(mat["formula"], dimensions)
        qty = max(0, round(qty_raw + 0.499))  # round up

        price = None
        subtotal = None
        if with_price:
            price = get_item_price(db, mat["name"].split(" ")[0])
            if price:
                subtotal = qty * price
                total_estimate += subtotal

        results.append({
            "material": mat["name"],
            "unit": mat["unit"],
            "qty_calculated": round(qty_raw, 2),
            "qty_recommended": qty,
            "note": mat.get("note", ""),
            "price_per_unit": price,
            "subtotal": subtotal,
        })

    return {
        "formula_id": formula_id,
        "description": formula["desc"],
        "dimensions": dimensions,
        "materials": results,
        "total_estimate": total_estimate if total_estimate > 0 else None,
        "disclaimer": "Estimasi kasar. Sesuaikan dengan kondisi lapangan dan spesifikasi proyek."
    }


# ══════════════════════════════════════════════════════════════════════════════
# PREDIKSI RESTOCK — math based, no AI token
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/restock-prediction")
def restock_prediction(
    days_ahead: int = 14,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    """
    Prediksi kapan setiap item akan habis berdasarkan velocity penjualan.
    Pure math — tidak perlu AI token.
    """
    today = date.today()
    last_30 = today - timedelta(days=30)

    items = db.query(models.Item).filter(models.Item.is_active == True).all()
    predictions = []

    for item in items:
        # Hitung velocity: rata-rata penjualan per hari dalam 30 hari terakhir
        total_sold = db.query(func.sum(models.SaleItem.qty)).join(
            models.Sale
        ).filter(
            models.SaleItem.item_id == item.id,
            models.Sale.date >= last_30
        ).scalar() or 0

        daily_velocity = total_sold / 30.0

        if daily_velocity <= 0:
            continue  # tidak ada penjualan, skip

        days_until_empty = item.stock / daily_velocity if daily_velocity > 0 else 999
        predicted_empty_date = today + timedelta(days=int(days_until_empty))

        # Hitung qty yang perlu dibeli untuk 30 hari ke depan
        qty_needed_30d = daily_velocity * 30
        qty_to_order = max(0, qty_needed_30d - item.stock)

        status = "critical" if days_until_empty <= 3 else \
                 "warning" if days_until_empty <= 7 else \
                 "watch" if days_until_empty <= days_ahead else "ok"

        if status in ["critical", "warning", "watch"]:
            predictions.append({
                "item_id": item.id,
                "item_code": item.code,
                "item_name": item.name,
                "current_stock": item.stock,
                "daily_velocity": round(daily_velocity, 2),
                "days_until_empty": round(days_until_empty, 1),
                "predicted_empty_date": str(predicted_empty_date),
                "qty_to_order_30d": round(qty_to_order, 0),
                "status": status,
                "unit": item.unit.abbreviation if item.unit else "pcs",
                "estimated_order_value": round(qty_to_order * item.buy_price, 0),
            })

    predictions.sort(key=lambda x: x["days_until_empty"])

    total_order_value = sum(p["estimated_order_value"] for p in predictions)

    return {
        "as_of": str(today),
        "days_ahead_analyzed": days_ahead,
        "predictions": predictions,
        "summary": {
            "critical_count": sum(1 for p in predictions if p["status"] == "critical"),
            "warning_count": sum(1 for p in predictions if p["status"] == "warning"),
            "watch_count": sum(1 for p in predictions if p["status"] == "watch"),
            "total_order_value_estimate": total_order_value,
        }
    }


# ══════════════════════════════════════════════════════════════════════════════
# CREDIT RISK — analisis piutang overdue
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/credit-risk")
def credit_risk_analysis(
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    """Analisis risiko kredit pelanggan berdasarkan riwayat pembayaran"""
    today = date.today()

    # Ambil semua penjualan yang belum lunas
    unpaid = db.query(models.Sale).filter(
        models.Sale.status.in_(["unpaid", "partial"])
    ).all()

    customer_risk = {}
    for sale in unpaid:
        cid = sale.customer_id or 0
        cname = sale.customer.name if sale.customer else "Umum"

        outstanding = sale.total - sale.paid
        days_overdue = (today - sale.date).days if sale.date else 0

        if cid not in customer_risk:
            customer_risk[cid] = {
                "customer_id": cid,
                "customer_name": cname,
                "total_outstanding": 0,
                "transaction_count": 0,
                "max_days_overdue": 0,
                "transactions": [],
            }

        customer_risk[cid]["total_outstanding"] += outstanding
        customer_risk[cid]["transaction_count"] += 1
        customer_risk[cid]["max_days_overdue"] = max(
            customer_risk[cid]["max_days_overdue"], days_overdue
        )
        customer_risk[cid]["transactions"].append({
            "number": sale.number,
            "date": str(sale.date),
            "outstanding": outstanding,
            "days": days_overdue,
        })

    # Assign risk level
    for cid, risk in customer_risk.items():
        days = risk["max_days_overdue"]
        amount = risk["total_outstanding"]
        if days > 60 or amount > 10_000_000:
            risk["risk_level"] = "high"
        elif days > 30 or amount > 5_000_000:
            risk["risk_level"] = "medium"
        else:
            risk["risk_level"] = "low"

    result = sorted(customer_risk.values(),
                    key=lambda x: x["total_outstanding"], reverse=True)

    return {
        "as_of": str(today),
        "customers": result,
        "summary": {
            "high_risk_count": sum(1 for r in result if r["risk_level"] == "high"),
            "medium_risk_count": sum(1 for r in result if r["risk_level"] == "medium"),
            "total_outstanding": sum(r["total_outstanding"] for r in result),
        }
    }


# ══════════════════════════════════════════════════════════════════════════════
# PROACTIVE DAILY BRIEFING — 1 AI call per hari, kirim ke Telegram
# ══════════════════════════════════════════════════════════════════════════════

async def _send_telegram(bot_token: str, chat_id: str, message: str) -> bool:
    """Kirim pesan ke Telegram"""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
            )
        return resp.json().get("ok", False)
    except Exception as e:
        print(f"Telegram error: {e}")
        return False


def _build_daily_briefing_data(db: Session) -> dict:
    """Kumpulkan data untuk briefing harian — pure math, no AI"""
    today = date.today()
    yesterday = today - timedelta(days=1)
    last_7 = today - timedelta(days=7)
    last_30 = today - timedelta(days=30)

    # Penjualan hari ini
    sales_today = db.query(models.Sale).filter(models.Sale.date == today).all()
    sales_yesterday = db.query(models.Sale).filter(models.Sale.date == yesterday).all()

    total_today = sum(s.total for s in sales_today)
    total_yesterday = sum(s.total for s in sales_yesterday)
    change_pct = ((total_today - total_yesterday) / total_yesterday * 100) if total_yesterday > 0 else 0

    # Stok kritis
    critical_stock = db.query(models.Item).filter(
        models.Item.is_active == True,
        models.Item.stock <= models.Item.min_stock
    ).all()

    # Piutang overdue > 30 hari
    overdue_sales = db.query(models.Sale).filter(
        models.Sale.status.in_(["unpaid", "partial"]),
    ).all()
    overdue_30 = [(today - s.date).days for s in overdue_sales if s.date and (today - s.date).days > 30]
    total_overdue_amount = sum(s.total - s.paid for s in overdue_sales
                               if s.date and (today - s.date).days > 30)

    # Surat jalan pending
    pending_delivery = db.query(models.DeliveryNote).filter(
        models.DeliveryNote.status == "pending"
    ).count()

    # Top item hari ini
    top_today = db.query(
        models.Item.name,
        func.sum(models.SaleItem.qty).label("qty"),
        func.sum(models.SaleItem.total).label("rev")
    ).join(models.SaleItem).join(models.Sale).filter(
        models.Sale.date == today
    ).group_by(models.Item.id).order_by(func.sum(models.SaleItem.total).desc()).limit(3).all()

    # Prediksi restock 7 hari
    restock_urgent = []
    items = db.query(models.Item).filter(models.Item.is_active == True).all()
    for item in items:
        total_sold = db.query(func.sum(models.SaleItem.qty)).join(
            models.Sale
        ).filter(
            models.SaleItem.item_id == item.id,
            models.Sale.date >= last_30
        ).scalar() or 0
        velocity = total_sold / 30.0
        if velocity > 0:
            days_left = item.stock / velocity
            if 0 < days_left <= 7:
                restock_urgent.append({
                    "name": item.name,
                    "days_left": round(days_left, 1),
                    "qty_order": round(velocity * 30 - item.stock)
                })

    restock_urgent.sort(key=lambda x: x["days_left"])

    return {
        "date": str(today),
        "sales_today": len(sales_today),
        "revenue_today": total_today,
        "revenue_yesterday": total_yesterday,
        "change_pct": change_pct,
        "critical_stock_count": len(critical_stock),
        "critical_stock_items": [i.name for i in critical_stock[:3]],
        "overdue_count": len(overdue_30),
        "overdue_amount": total_overdue_amount,
        "pending_delivery_count": pending_delivery,
        "top_items_today": [{"name": t.name, "qty": t.qty, "rev": t.rev} for t in top_today],
        "restock_urgent": restock_urgent[:5],
    }


@router.post("/daily-briefing")
async def send_daily_briefing(
    background_tasks: BackgroundTasks,
    use_ai: bool = True,
    db: Session = Depends(get_db),
    _=Depends(require_admin)
):
    """
    Generate dan kirim briefing harian ke Telegram.
    use_ai=True → AI tulis narasi (1 call)
    use_ai=False → Format template sederhana (0 token)
    """
    # Ambil Telegram config
    configs = db.query(models.NotificationConfig).filter(
        models.NotificationConfig.channel == "telegram",
        models.NotificationConfig.is_active == True
    ).all()

    if not configs:
        raise HTTPException(400, "Telegram belum dikonfigurasi di Settings → Notifikasi")

    briefing_data = _build_daily_briefing_data(db)
    rp = lambda n: f"Rp {int(n):,}".replace(",", ".")

    if use_ai:
        # 1 AI call untuk narasi
        context = json.dumps(briefing_data, ensure_ascii=False)
        question = f"""
Buatkan briefing harian singkat untuk pemilik toko bangunan.
Data hari ini: {context}

Format output (HANYA ini, tidak ada lain):
📊 <b>Briefing {briefing_data['date']}</b>

[2-3 kalimat ringkasan kondisi toko hari ini dengan angka spesifik]

⚡ <b>Perlu Tindakan:</b>
[bullet point 3-5 hal yang perlu dilakukan hari ini, kongkret dengan angka]

💡 <b>Peluang:</b>
[1-2 hal yang bisa dilakukan untuk meningkatkan penjualan hari ini]

Gunakan angka nyata dari data. Bahasa Indonesia santai tapi profesional.
Maksimal 250 kata total.
"""
        try:
            engine = get_engine()
            result = engine.run(
                user_question=question,
                context_data=context,
                executor_system_extra="Kamu adalah asisten bisnis toko bangunan. Jawab singkat dan langsung ke poin."
            )
            message = result["answer"]
        except Exception as e:
            # Fallback ke template kalau AI gagal
            use_ai = False

    if not use_ai:
        # Template tanpa AI
        change_arrow = "📈" if briefing_data["change_pct"] >= 0 else "📉"
        message = f"""📊 <b>Briefing Toko — {briefing_data['date']}</b>

💰 Penjualan hari ini: <b>{rp(briefing_data['revenue_today'])}</b> ({len(briefing_data['sales_today'])} transaksi)
{change_arrow} vs kemarin: {briefing_data['change_pct']:+.1f}%

⚠️ Perlu Tindakan:
"""
        if briefing_data["critical_stock_count"] > 0:
            items_str = ", ".join(briefing_data["critical_stock_items"])
            message += f"• Stok kritis {briefing_data['critical_stock_count']} item: {items_str}\n"
        if briefing_data["overdue_count"] > 0:
            message += f"• Piutang overdue >30hr: {briefing_data['overdue_count']} pelanggan ({rp(briefing_data['overdue_amount'])})\n"
        if briefing_data["pending_delivery_count"] > 0:
            message += f"• Surat jalan pending: {briefing_data['pending_delivery_count']}\n"
        if briefing_data["restock_urgent"]:
            r = briefing_data["restock_urgent"][0]
            message += f"• Restock urgent: {r['name']} ({r['days_left']} hari lagi habis)\n"
        if not any([briefing_data["critical_stock_count"], briefing_data["overdue_count"],
                    briefing_data["pending_delivery_count"], briefing_data["restock_urgent"]]):
            message += "• Semua kondisi normal ✅\n"

        if briefing_data["top_items_today"]:
            top = briefing_data["top_items_today"][0]
            message += f"\n🏆 Terlaris: {top['name']} ({top['qty']:.0f} unit)"

    # Kirim ke semua Telegram yang subscribe daily_report
    sent_count = 0
    import json as json_mod
    for cfg in configs:
        try:
            events = json_mod.loads(cfg.events) if cfg.events else []
        except Exception:
            events = []
        if "daily_report" not in events and "all" not in events:
            continue
        ok = await _send_telegram(cfg.api_key, cfg.target, message)
        if ok:
            sent_count += 1
            db.add(models.NotificationLog(
                channel="telegram", event="daily_report",
                message=message[:500], status="sent"
            ))
        else:
            db.add(models.NotificationLog(
                channel="telegram", event="daily_report",
                message=message[:500], status="failed"
            ))

    db.commit()

    return {
        "message": f"Briefing dikirim ke {sent_count} channel Telegram",
        "used_ai": use_ai,
        "preview": message[:300] + "..." if len(message) > 300 else message,
    }


# ══════════════════════════════════════════════════════════════════════════════
# WEEKLY BUSINESS REVIEW — 1 AI call per minggu
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/weekly-review")
async def send_weekly_review(
    db: Session = Depends(get_db),
    _=Depends(require_admin)
):
    """
    Laporan review mingguan — AI tulis seperti konsultan.
    Kirim ke Telegram.
    """
    configs = db.query(models.NotificationConfig).filter(
        models.NotificationConfig.channel == "telegram",
        models.NotificationConfig.is_active == True
    ).all()

    if not configs:
        raise HTTPException(400, "Telegram belum dikonfigurasi")

    # Build context mingguan
    context = build_business_context(db)

    question = """
Buatkan laporan review mingguan toko bangunan dalam format:

<b>📋 Review Mingguan Toko</b>

<b>🎯 Kondisi Bisnis:</b>
[Evaluasi singkat performa minggu ini — apa yang baik, apa yang kurang]

<b>📦 Manajemen Stok:</b>
[Item yang perlu diperhatikan, rekomendasi pembelian spesifik]

<b>💰 Keuangan:</b>
[Piutang yang perlu ditagih, margin yang perlu diperbaiki]

<b>🚀 3 Langkah Minggu Depan:</b>
[Tiga tindakan konkret dan terukur yang bisa dilakukan]

Gunakan angka nyata. Bahasa Indonesia. Maksimal 300 kata.
Buat terasa seperti konsultan bisnis berpengalaman yang bicara langsung.
"""

    try:
        engine = get_engine()
        result = engine.run(
            user_question=question,
            context_data=context,
            executor_system_extra="Kamu konsultan bisnis toko bangunan berpengalaman. Spesifik, data-driven, tidak generik."
        )
        message = result["answer"]
    except Exception as e:
        raise HTTPException(500, f"AI error: {str(e)}")

    # Kirim ke Telegram
    import json as json_mod
    sent = 0
    for cfg in configs:
        try:
            events = json_mod.loads(cfg.events) if cfg.events else []
        except Exception:
            events = []
        if "daily_report" not in events and "all" not in events:
            continue
        ok = await _send_telegram(cfg.api_key, cfg.target, message)
        if ok:
            sent += 1

    return {"message": f"Weekly review dikirim ke {sent} channel", "preview": message[:400]}


# ══════════════════════════════════════════════════════════════════════════════
# CONSIGNMENT DUE DATE ALERT — kirim ke Telegram
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/consignment-due-alert")
async def send_consignment_due_alert(
    days_ahead: int = 7,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    """
    Alert tagihan konsinyasi yang akan jatuh tempo dalam X hari.
    Kirim otomatis ke Telegram.
    """
    today = date.today()
    due_date = today + timedelta(days=days_ahead)

    # Tagihan konsinyasi masuk yang belum lunas
    bills_in = db.query(models.ConsignmentInBill).filter(
        models.ConsignmentInBill.status.in_(["unpaid", "partial"])
    ).all()

    # Tagihan konsinyasi keluar yang belum lunas
    bills_out = db.query(models.ConsignmentOutBill).filter(
        models.ConsignmentOutBill.status.in_(["unpaid", "partial"])
    ).all()

    alerts = []

    for bill in bills_in:
        remaining = bill.amount - bill.paid
        alerts.append({
            "type": "Konsinyasi Masuk (Hutang ke Supplier)",
            "number": bill.number,
            "date": str(bill.date),
            "remaining": remaining,
            "status": bill.status,
        })

    for bill in bills_out:
        remaining = bill.amount - bill.paid
        alerts.append({
            "type": "Konsinyasi Keluar (Piutang dari Toko Lain)",
            "number": bill.number,
            "date": str(bill.date),
            "remaining": remaining,
            "status": bill.status,
        })

    if not alerts:
        return {"message": "Tidak ada tagihan konsinyasi yang perlu diingatkan"}

    rp = lambda n: f"Rp {int(n):,}".replace(",", ".")
    total = sum(a["remaining"] for a in alerts)

    message = f"""🔔 <b>Alert Tagihan Konsinyasi</b>
📅 {today}

Ada <b>{len(alerts)} tagihan</b> yang belum diselesaikan:
Total: <b>{rp(total)}</b>

"""
    for a in alerts[:5]:
        message += f"• {a['type']}\n  No: {a['number']} | {rp(a['remaining'])}\n"

    if len(alerts) > 5:
        message += f"\n...dan {len(alerts) - 5} tagihan lainnya."

    message += "\n\nSegera selesaikan untuk menjaga kepercayaan mitra bisnis."

    # Kirim ke Telegram
    configs = db.query(models.NotificationConfig).filter(
        models.NotificationConfig.channel == "telegram",
        models.NotificationConfig.is_active == True
    ).all()

    import json as json_mod
    sent = 0
    for cfg in configs:
        try:
            events = json_mod.loads(cfg.events) if cfg.events else []
        except Exception:
            events = []
        if any(e in events for e in ["low_stock", "daily_report", "all"]):
            ok = await _send_telegram(cfg.api_key, cfg.target, message)
            if ok:
                sent += 1

    return {
        "alerts_count": len(alerts),
        "total_amount": total,
        "sent_to": sent,
        "message": f"Alert dikirim ke {sent} channel Telegram"
    }


# ══════════════════════════════════════════════════════════════════════════════
# PRICING ALERT
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/pricing-alert")
def pricing_alert(
    min_margin_pct: float = 10.0,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    """
    Alert item yang margin-nya di bawah threshold.
    Berguna ketika harga beli naik tapi harga jual belum disesuaikan.
    """
    items = db.query(models.Item).filter(
        models.Item.is_active == True,
        models.Item.sell_price > 0,
        models.Item.buy_price > 0,
    ).all()

    low_margin = []
    negative_margin = []

    for item in items:
        margin_pct = (item.sell_price - item.buy_price) / item.sell_price * 100
        if margin_pct < 0:
            negative_margin.append({
                "id": item.id, "code": item.code, "name": item.name,
                "buy_price": item.buy_price, "sell_price": item.sell_price,
                "margin_pct": round(margin_pct, 1),
                "recommended_sell_price": round(item.buy_price * 1.15, 0),
            })
        elif margin_pct < min_margin_pct:
            low_margin.append({
                "id": item.id, "code": item.code, "name": item.name,
                "buy_price": item.buy_price, "sell_price": item.sell_price,
                "margin_pct": round(margin_pct, 1),
                "recommended_sell_price": round(item.buy_price / (1 - min_margin_pct/100), 0),
            })

    return {
        "threshold_pct": min_margin_pct,
        "negative_margin": sorted(negative_margin, key=lambda x: x["margin_pct"]),
        "low_margin": sorted(low_margin, key=lambda x: x["margin_pct"]),
        "summary": {
            "negative_count": len(negative_margin),
            "low_margin_count": len(low_margin),
            "total_items_checked": len(items),
        }
    }
