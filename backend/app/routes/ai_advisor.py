"""
iPos 5.0 — AI Advisor Routes
Fitur:
  1. /chat          — Business Advisor Chat (tanya bisnis)
  2. /categorize    — Auto kategorisasi barang
  3. /report        — Laporan naratif otomatis
  4. /anomaly       — Deteksi anomali transaksi
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional
from pydantic import BaseModel
from ..database import get_db
from ..auth import get_current_user
from ..permissions import has_permission
from .. import models
from ..ai_engine import get_engine, build_business_context, build_anomaly_context

router = APIRouter()


# ─── Schemas ──────────────────────────────────────────────────────────────────
class ChatMessage(BaseModel):
    role: str   # "user" | "assistant"
    content: str

class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []


class CategorizeRequest(BaseModel):
    item_name: str
    barcode: Optional[str] = None
    current_buy_price: Optional[float] = None


# ══════════════════════════════════════════════════════════════════════════════
# 1. BUSINESS ADVISOR CHAT
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/chat")
def chat(
    req: ChatRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """
    AI Business Advisor — tanya apa saja tentang bisnis toko.
    Menggunakan Executor + Evaluator loop untuk memastikan jawaban akurat.
    """
    try:
        engine = get_engine()
        context = build_business_context(db)

        # Convert history ke format yang dibutuhkan engine
        history = [{"role": m.role, "content": m.content} for m in req.history]

        result = engine.run(
            user_question=req.message,
            context_data=context,
            conversation_history=history,
            executor_system_extra=f"""
Kamu sedang berbicara dengan {current_user.full_name or current_user.username} 
yang memiliki role: {current_user.role}.
Sesuaikan bahasa dan kedalaman analisis dengan role tersebut.
Jika role kasir, fokus pada hal operasional. 
Jika role admin/owner, berikan analisis bisnis yang lebih dalam.
"""
        )

        return {
            "answer": result["answer"],
            "iterations": result["iterations"],
            "approved": result["approved"],
            "debug_evaluator": result["evaluator_notes"] if not result["approved"] else None
        }

    except Exception as e:
        error_msg = str(e)
        if "api_key" in error_msg.lower() or "authentication" in error_msg.lower():
            raise HTTPException(400, "Groq API key belum dikonfigurasi. Set GROQ_API_KEY di file .env")
        raise HTTPException(500, f"AI Error: {error_msg}")


# ══════════════════════════════════════════════════════════════════════════════
# 4. AUTO KATEGORISASI BARANG
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/categorize")
def categorize_item(
    req: CategorizeRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """
    Auto kategorisasi & saran untuk barang baru.
    Input: nama barang → Output: kategori, satuan, deskripsi, estimasi harga jual
    """
    try:
        engine = get_engine()

        # Ambil kategori & satuan yang sudah ada di database
        existing_categories = db.query(models.Category).all()
        existing_units = db.query(models.Unit).all()

        cat_list = "\n".join([f"- ID {c.id}: {c.name}" for c in existing_categories]) or "Belum ada kategori"
        unit_list = "\n".join([f"- ID {u.id}: {u.name} ({u.abbreviation or '-'})" for u in existing_units]) or "Belum ada satuan"

        context = f"""
Kategori yang sudah ada di database:
{cat_list}

Satuan yang sudah ada di database:
{unit_list}

Harga beli yang diinput user: Rp {req.current_buy_price:,.0f if req.current_buy_price else 'belum diisi'}
"""
        question = f"""
Barang baru yang ingin diinput: "{req.item_name}"
{f'Barcode: {req.barcode}' if req.barcode else ''}

Berikan rekomendasi dalam format JSON yang valid (tidak ada teks di luar JSON):
{{
  "category_suggestion": "nama kategori yang paling cocok dari daftar yang ada, atau nama kategori baru jika tidak ada yang cocok",
  "category_id": null_atau_id_jika_cocok_dengan_yang_ada,
  "unit_suggestion": "nama satuan yang paling cocok",
  "unit_id": null_atau_id_jika_cocok,
  "description": "deskripsi singkat 1-2 kalimat tentang produk ini",
  "sell_price_suggestion": estimasi_harga_jual_dalam_rupiah_angka_saja,
  "sell_price_reasoning": "alasan estimasi harga jual ini",
  "margin_suggestion": estimasi_margin_persen_angka_saja,
  "tags": ["tag1", "tag2", "tag3"]
}}
"""
        result = engine.run(
            user_question=question,
            context_data=context,
            executor_system_extra="""
Kamu adalah expert produk retail Indonesia. 
Tugasmu: analisis nama barang dan berikan rekomendasi kategorisasi yang akurat.
Gunakan pengetahuanmu tentang produk-produk umum di toko retail Indonesia.
Respons HANYA dalam format JSON valid, tidak ada teks tambahan.
"""
        )

        # Parse JSON dari jawaban
        answer = result["answer"].strip()
        # Coba ekstrak JSON dari response
        json_match = None
        try:
            # Cari JSON block
            import re
            json_pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
            matches = re.findall(json_pattern, answer, re.DOTALL)
            if matches:
                json_match = json.loads(matches[0])
        except:
            pass

        import json as json_lib
        if not json_match:
            try:
                json_match = json_lib.loads(answer)
            except:
                # Fallback jika JSON tidak valid
                json_match = {
                    "category_suggestion": "Umum",
                    "category_id": None,
                    "unit_suggestion": "pcs",
                    "unit_id": None,
                    "description": f"Produk: {req.item_name}",
                    "sell_price_suggestion": req.current_buy_price * 1.3 if req.current_buy_price else 0,
                    "sell_price_reasoning": "Estimasi margin 30%",
                    "margin_suggestion": 30,
                    "tags": []
                }

        return {
            "suggestion": json_match,
            "iterations": result["iterations"],
            "approved": result["approved"]
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"AI Error: {str(e)}")


# ══════════════════════════════════════════════════════════════════════════════
# 6. LAPORAN NARATIF OTOMATIS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/report")
def generate_narrative_report(
    period: str = "today",   # today | week | month
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """
    Generate laporan bisnis dalam bahasa natural Indonesia.
    Bukan tabel angka — tapi narasi yang bisa langsung dipahami pemilik toko.
    """
    try:
        engine = get_engine()
        context = build_business_context(db)

        period_map = {
            "today": "hari ini",
            "week": "7 hari terakhir",
            "month": "30 hari terakhir"
        }
        period_label = period_map.get(period, "hari ini")

        question = f"""
Buatkan laporan bisnis naratif untuk periode: {period_label}

Format laporan yang diinginkan:

**RINGKASAN EKSEKUTIF**
[2-3 kalimat ringkasan performa bisnis periode ini]

**PENCAPAIAN**
[Apa yang berjalan baik? Minimal 2 poin dengan angka spesifik]

**PERHATIAN**
[Apa yang perlu diperhatikan atau diperbaiki? Minimal 2 poin dengan angka spesifik]

**REKOMENDASI AKSI**
[3 langkah konkret yang bisa dilakukan dalam 7 hari ke depan]

**PREDIKSI**
[Berdasarkan tren, prediksi apa yang akan terjadi minggu depan jika tidak ada perubahan?]

Gunakan angka nyata dari data. Buat laporan yang terasa personal, bukan seperti template.
Bayangkan kamu adalah konsultan bisnis yang berbicara langsung ke pemilik toko.
"""

        result = engine.run(
            user_question=question,
            context_data=context,
            executor_system_extra="""
Kamu adalah konsultan bisnis retail berpengalaman 10 tahun di Indonesia.
Gaya bahasa: profesional tapi hangat, seperti teman bisnis yang pintar.
Selalu gunakan angka spesifik dari data. Hindari kalimat generik.
Laporan harus terasa seperti ditulis oleh manusia, bukan robot.
"""
        )

        return {
            "report": result["answer"],
            "period": period_label,
            "iterations": result["iterations"],
            "approved": result["approved"]
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"AI Error: {str(e)}")


# ══════════════════════════════════════════════════════════════════════════════
# 7. DETEKSI ANOMALI TRANSAKSI
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/anomaly")
def detect_anomaly(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """
    Deteksi pola mencurigakan dalam transaksi.
    Menganalisis: diskon berlebihan, transaksi ganjil, pola kasir mencurigakan, dll.
    """
    if not has_permission(db, current_user, "report.financial", "view"):
        raise HTTPException(403, "Fitur ini hanya untuk admin/pemilik toko")

    try:
        engine = get_engine()
        anomaly_context = build_anomaly_context(db)
        business_context = build_business_context(db)

        full_context = business_context + "\n\n" + anomaly_context

        question = """
Analisis data berikut dan deteksi anomali atau pola mencurigakan yang perlu diperhatikan pemilik toko.

Fokus pada:
1. Pola transaksi per kasir yang tidak normal dibanding kasir lain
2. Diskon berlebihan yang tidak wajar
3. Transaksi di luar jam operasional normal
4. Retur yang mencurigakan
5. Perubahan data yang tidak wajar
6. Perbandingan performa antar shift/kasir

Format responmu:

**TINGKAT RISIKO KESELURUHAN**: [RENDAH / SEDANG / TINGGI]
[1 kalimat alasan]

**ANOMALI TERDETEKSI**
[Untuk setiap anomali yang ditemukan, format:]
🔴 KRITIS / 🟡 PERLU PERHATIAN / 🟢 NORMAL
[Nama anomali]: [Penjelasan spesifik dengan angka]
→ Rekomendasi: [Langkah konkret]

**KESIMPULAN**
[Ringkasan 2-3 kalimat: apakah ada indikasi kecurangan atau hanya ketidakefisienan operasional?]

Jika tidak ada anomali berarti, katakan dengan jelas bahwa bisnis berjalan normal.
Jangan membuat klaim serius tanpa bukti data yang kuat.
"""

        result = engine.run(
            user_question=question,
            context_data=full_context,
            executor_system_extra="""
Kamu adalah fraud analyst dan internal auditor untuk bisnis retail.
Tugasmu: deteksi anomali berdasarkan data, BUKAN menuduh tanpa bukti.
Bedakan antara: anomali operasional biasa VS indikasi kecurangan.
Selalu sertakan data spesifik sebagai dasar setiap klaim.
Bersikap objektif — jika data normal, katakan normal. Jangan mencari masalah yang tidak ada.
"""
        )

        return {
            "analysis": result["answer"],
            "iterations": result["iterations"],
            "approved": result["approved"]
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"AI Error: {str(e)}")


@router.get("/status")
def ai_status(current_user: models.User = Depends(get_current_user)):
    """Cek apakah AI sudah dikonfigurasi (Gemini / Groq / OpenRouter)"""
    from ..config import settings

    gemini_ok     = bool(settings.GEMINI_API_KEY and settings.GEMINI_API_KEY != "your_gemini_api_key_here")
    groq_ok       = bool(settings.GROQ_API_KEY and settings.GROQ_API_KEY != "your_groq_api_key_here")
    openrouter_ok = bool(settings.OPENROUTER_API_KEY and settings.OPENROUTER_API_KEY != "your_openrouter_api_key_here")
    configured    = gemini_ok or groq_ok or openrouter_ok

    active = "gemini" if gemini_ok else "groq" if groq_ok else "openrouter" if openrouter_ok else "none"
    model_map = {
        "gemini": "gemini-2.0-flash",
        "groq": "llama-3.3-70b-versatile",
        "openrouter": "auto-detect free >70B",
        "none": "-"
    }
    return {
        "configured": configured,
        "active_provider": active,
        "model": model_map[active],
        "providers": {"gemini": gemini_ok, "groq": groq_ok, "openrouter": openrouter_ok},
        "message": (
            f"AI aktif via {active} ({model_map[active]})" if configured
            else "Set GEMINI_API_KEY atau GROQ_API_KEY di file .env"
        )
    }


# ══════════════════════════════════════════════════════════════════════════════
# PROVIDER STATUS — bisa dipantau dari Settings
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/providers")
def get_provider_status(current_user: models.User = Depends(get_current_user)):
    """
    Status semua AI provider: available / cooldown Xs / not configured
    Tampil di Settings → AI Status
    """
    from ..ai_engine import get_provider_manager, get_openrouter_free_70b_model, PROVIDERS

    pm = get_provider_manager()
    status = pm.status()

    # Tambahkan info model yang dipakai
    result = {}
    for provider, state in status.items():
        executor_model = PROVIDERS[provider]["executor_model"]
        if provider == "openrouter" and executor_model is None:
            try:
                executor_model = get_openrouter_free_70b_model()
            except Exception:
                executor_model = "auto-detect (free >70B)"

        result[provider] = {
            "status": state,
            "executor_model": executor_model,
            "evaluator_model": PROVIDERS[provider].get("evaluator_model") or executor_model,
            "configured": state != "not configured",
        }

    return {
        "providers": result,
        "order": ["gemini → groq → openrouter (auto fallback)"],
        "strategy": "Gemini primary, Groq 70B fallback, OpenRouter free >70B last resort"
    }
