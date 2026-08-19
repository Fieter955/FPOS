"""
iPos 5.0 — AI Engine v3
Architecture: Executor + Evaluator (Self-Refinement Loop)

Provider Order (Executor & Evaluator sama):
  1. Gemini 2.0 Flash   — primary (smart + free tier)
  2. Groq llama-3.3-70b — fallback kalau Gemini rate limit
  3. OpenRouter free >70B— last resort, auto-detect model gratis >70B param

Rate Limit Handling:
  - Tiap provider punya cooldown counter sendiri setelah kena 429
  - Auto-switch transparan, user tidak tahu
  - Cooldown: Gemini 60s, Groq 60s, OpenRouter 120s
"""

import time, httpx, re
from typing import Optional
from openai import OpenAI, RateLimitError, APIError
from .config import settings

# ══════════════════════════════════════════════════════════════════════════════
# PROVIDER CONFIG
# ══════════════════════════════════════════════════════════════════════════════

PROVIDERS = {
    "gemini": {
        "base_url":     "https://generativelanguage.googleapis.com/v1beta/openai/",
        "api_key_attr": "GEMINI_API_KEY",
        "executor_model":  "gemini-2.0-flash",
        "evaluator_model": "gemini-2.0-flash",
        "max_tokens":   2048,
        "cooldown":     60,
    },
    "groq": {
        "base_url":     "https://api.groq.com/openai/v1",
        "api_key_attr": "GROQ_API_KEY",
        "executor_model":  "llama-3.3-70b-versatile",
        "evaluator_model": "llama-3.3-70b-versatile",
        "max_tokens":   2048,
        "cooldown":     60,
    },
    "openrouter": {
        "base_url":     "https://openrouter.ai/api/v1",
        "api_key_attr": "OPENROUTER_API_KEY",
        "executor_model":  None,   # auto-detect
        "evaluator_model": None,   # auto-detect
        "max_tokens":   2048,
        "cooldown":     120,
    },
}

# Urutan yang sama untuk executor dan evaluator
PROVIDER_ORDER = ["gemini", "groq", "openrouter"]

# ══════════════════════════════════════════════════════════════════════════════
# OPENROUTER: AUTO-DETECT FREE MODEL > 70B
# ══════════════════════════════════════════════════════════════════════════════

# Fallback kalau API OpenRouter tidak bisa diakses
FREE_70B_FALLBACK = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "meta-llama/llama-3.1-70b-instruct:free",
    "qwen/qwen-2.5-72b-instruct:free",
    "deepseek/deepseek-r1:free",
    "nvidia/llama-3.1-nemotron-70b-instruct:free",
]

_or_cache: dict = {"model": None, "cached_at": 0}
_OR_CACHE_TTL = 6 * 3600  # 6 jam


def _extract_param_count(model_id: str) -> int:
    """
    Coba ekstrak jumlah parameter dari model_id.
    Contoh: 'meta-llama/llama-3.3-70b-instruct' → 70
    Returns 0 kalau tidak ditemukan.
    """
    match = re.search(r'(\d+)b', model_id.lower())
    return int(match.group(1)) if match else 0


def get_openrouter_free_70b_model() -> str:
    """
    Ambil model gratis terbaik dengan parameter ≥ 70B dari OpenRouter.
    Cache 6 jam. Fallback ke hardcoded list kalau API gagal.
    """
    global _or_cache
    now = time.time()

    if _or_cache["model"] and (now - _or_cache["cached_at"]) < _OR_CACHE_TTL:
        return _or_cache["model"]

    api_key = getattr(settings, "OPENROUTER_API_KEY", "")
    headers = {}
    if api_key and not api_key.startswith("your_"):
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        with httpx.Client(timeout=8) as client:
            resp = client.get("https://openrouter.ai/api/v1/models", headers=headers)
            resp.raise_for_status()
            all_models = resp.json().get("data", [])

        candidates = []
        for m in all_models:
            mid = m.get("id", "")
            pricing = m.get("pricing", {})

            # Filter gratis
            try:
                prompt_cost = float(pricing.get("prompt", "1") or "1")
                completion_cost = float(pricing.get("completion", "1") or "1")
                is_free = (prompt_cost == 0 and completion_cost == 0)
            except (ValueError, TypeError):
                continue

            if not is_free:
                continue

            # Filter >70B
            params = _extract_param_count(mid)
            if params < 70:
                continue

            # Filter: cukup context window
            try:
                ctx = int(m.get("context_length", 0))
            except (ValueError, TypeError):
                ctx = 0
            if ctx < 8000:
                continue

            # Filter: bukan embedding/vision
            if any(x in mid.lower() for x in ["embed", "vision", "image", "ocr"]):
                continue

            candidates.append((mid, params))

        if candidates:
            # Sort: param terbanyak duluan, lalu fallback order
            def sort_key(item):
                mid, params = item
                fallback_idx = next(
                    (i for i, f in enumerate(FREE_70B_FALLBACK) if f.split(":")[0] in mid),
                    len(FREE_70B_FALLBACK)
                )
                return (-params, fallback_idx)

            candidates.sort(key=sort_key)
            selected = candidates[0][0]
            print(f"✓ OpenRouter auto-detected: {selected} ({candidates[0][1]}B params)")
            print(f"  Available free >70B: {[c[0] for c in candidates[:5]]}")
        else:
            selected = FREE_70B_FALLBACK[0]
            print(f"⚠ OpenRouter: no free >70B model found via API, using fallback: {selected}")

        _or_cache = {"model": selected, "cached_at": now}
        return selected

    except Exception as e:
        print(f"⚠ OpenRouter model fetch failed ({e}), using fallback: {FREE_70B_FALLBACK[0]}")
        if not _or_cache["model"]:
            _or_cache = {"model": FREE_70B_FALLBACK[0], "cached_at": now}
        return _or_cache["model"]


# ══════════════════════════════════════════════════════════════════════════════
# PROVIDER MANAGER
# ══════════════════════════════════════════════════════════════════════════════

class ProviderManager:

    def __init__(self):
        self._cooldowns: dict[str, float] = {}

    def is_available(self, provider: str) -> bool:
        return time.time() >= self._cooldowns.get(provider, 0)

    def mark_rate_limited(self, provider: str):
        cd = PROVIDERS[provider]["cooldown"]
        self._cooldowns[provider] = time.time() + cd
        print(f"⚠ {provider} rate limited → cooldown {cd}s")

    def get_api_key(self, provider: str) -> Optional[str]:
        attr = PROVIDERS[provider]["api_key_attr"]
        key = getattr(settings, attr, "")
        if not key or key.startswith("your_"):
            return None
        return key

    def get_client(self, provider: str) -> Optional[OpenAI]:
        key = self.get_api_key(provider)
        if not key:
            return None
        return OpenAI(
            api_key=key,
            base_url=PROVIDERS[provider]["base_url"],
        )

    def get_model(self, provider: str, role: str) -> Optional[str]:
        cfg = PROVIDERS[provider]
        model = cfg[f"{role}_model"]
        if provider == "openrouter" and model is None:
            model = get_openrouter_free_70b_model()
        return model

    def call_one(self, provider: str, role: str,
                 messages: list, temperature: float) -> tuple[Optional[str], Optional[str]]:
        """
        Single provider call.
        Returns (text, None) on success, (None, error_msg) on failure.
        """
        if not self.is_available(provider):
            remaining = int(self._cooldowns.get(provider, 0) - time.time())
            return None, f"{provider}: cooldown {remaining}s remaining"

        key = self.get_api_key(provider)
        if not key:
            return None, f"{provider}: API key not configured"

        model = self.get_model(provider, role)
        if not model:
            return None, f"{provider}: no model available"

        client = self.get_client(provider)
        if not client:
            return None, f"{provider}: client init failed"

        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=PROVIDERS[provider]["max_tokens"],
            )
            text = resp.choices[0].message.content
            return text, None

        except RateLimitError:
            self.mark_rate_limited(provider)
            return None, f"{provider}: 429 rate limit"

        except APIError as e:
            s = str(e)
            if "429" in s or "rate" in s.lower() or "quota" in s.lower():
                self.mark_rate_limited(provider)
                return None, f"{provider}: rate limit (APIError)"
            return None, f"{provider}: APIError: {s[:120]}"

        except Exception as e:
            s = str(e)
            if any(x in s.lower() for x in ["429", "rate_limit", "quota", "limit exceeded"]):
                self.mark_rate_limited(provider)
                return None, f"{provider}: rate limit detected"
            return None, f"{provider}: {s[:120]}"

    def call(self, role: str, messages: list,
             temperature: float = 0.3) -> tuple[str, str]:
        """
        Try all providers in order for given role.
        Returns (response_text, provider_name).
        Raises RuntimeError if all fail.
        """
        errors = []
        for provider in PROVIDER_ORDER:
            text, err = self.call_one(provider, role, messages, temperature)
            if text is not None:
                if provider != "gemini":  # log non-primary usage
                    print(f"  → Using {provider} for {role} (primary unavailable)")
                return text, provider
            errors.append(err)
            print(f"  ✗ {err}")

        raise RuntimeError(
            f"Semua AI provider tidak tersedia.\n" +
            "\n".join(f"  - {e}" for e in errors)
        )

    def status(self) -> dict:
        now = time.time()
        result = {}
        for p in PROVIDER_ORDER:
            cd_until = self._cooldowns.get(p, 0)
            key_ok = bool(self.get_api_key(p))
            if not key_ok:
                result[p] = "not configured"
            elif now < cd_until:
                result[p] = f"cooldown {int(cd_until - now)}s"
            else:
                result[p] = "available"
        return result


# Singleton
_pm: Optional[ProviderManager] = None

def get_provider_manager() -> ProviderManager:
    global _pm
    if _pm is None:
        _pm = ProviderManager()
    return _pm


# ══════════════════════════════════════════════════════════════════════════════
# SYSTEM PROMPTS
# ══════════════════════════════════════════════════════════════════════════════

EXECUTOR_BASE = """Kamu adalah AI Business Advisor untuk aplikasi POS (Point of Sale) iPos 5.0 Indonesia.
Kamu memiliki akses ke data bisnis toko secara real-time yang diberikan sebagai konteks.

Peranmu:
- Analisis data bisnis toko dengan tajam dan akurat
- Berikan insight yang actionable dan spesifik, bukan generik
- Gunakan angka nyata dari data yang diberikan
- Jawab dalam Bahasa Indonesia yang natural dan mudah dipahami pemilik toko
- Jika ada masalah, jelaskan penyebab spesifiknya berdasarkan data
- Jika ditanya rekomendasi, berikan langkah konkret yang bisa langsung dilakukan

PENTING:
- Jangan pernah mengarang angka yang tidak ada di data
- Jika data tidak cukup untuk menjawab, katakan secara jujur
- Fokus pada nilai bisnis nyata, bukan teori"""

EVALUATOR_BASE = """Kamu adalah AI Evaluator kritis untuk sistem AI Business Advisor.
Tugasmu: evaluasi apakah jawaban dari Executor AI sudah benar, akurat, dan berguna.

Kriteria:
1. AKURASI DATA — angka/fakta sesuai data yang diberikan?
2. LOGIKA — kesimpulan masuk akal secara bisnis?
3. KELENGKAPAN — pertanyaan user dijawab lengkap?
4. ACTIONABLE — rekomendasi konkret dan bisa dilakukan?
5. TIDAK MENYESATKAN — ada klaim yang bisa menyesatkan pemilik toko?

Format responmu HARUS salah satu dari:
  APPROVED
  REJECTED: [alasan spesifik + koreksi + data yang seharusnya digunakan]

Jangan reject perbedaan gaya, hanya kesalahan substansial."""


# ══════════════════════════════════════════════════════════════════════════════
# AI ENGINE
# ══════════════════════════════════════════════════════════════════════════════

class AIEngine:

    def __init__(self):
        self.pm = get_provider_manager()

    def _executor_call(self, system: str, messages: list) -> tuple[str, str]:
        full = [{"role": "system", "content": system}] + messages
        return self.pm.call("executor", full, temperature=0.4)

    def _evaluator_call(self, context: str, question: str,
                        answer: str) -> tuple[bool, str, str]:
        msgs = [
            {"role": "system", "content": EVALUATOR_BASE},
            {"role": "user", "content":
                f"DATA BISNIS:\n{context}\n\n"
                f"PERTANYAAN USER:\n{question}\n\n"
                f"JAWABAN EXECUTOR:\n{answer}\n\n"
                f"Evaluasi jawaban berdasarkan kriteria."}
        ]
        response, provider = self.pm.call("evaluator", msgs, temperature=0.1)
        approved = response.strip().upper().startswith("APPROVED")
        return approved, response.strip(), provider

    def run(self,
            user_question: str,
            context_data: str,
            conversation_history: list = None,
            executor_system_extra: str = "") -> dict:

        MAX_ITER = 3
        history = conversation_history or []

        system = EXECUTOR_BASE
        if executor_system_extra:
            system += f"\n\n{executor_system_extra}"
        system += f"\n\nDATA BISNIS TOKO (real-time):\n{context_data}"

        messages = history + [{"role": "user", "content": user_question}]

        best_answer = ""
        last_feedback = ""
        exec_provider = "unknown"
        eval_provider = "unknown"
        iterations = 0

        for i in range(MAX_ITER):
            iterations += 1

            # Executor
            answer, exec_provider = self._executor_call(system, messages)
            best_answer = answer

            # Evaluator
            approved, feedback, eval_provider = self._evaluator_call(
                context_data, user_question, answer
            )
            last_feedback = feedback

            if approved:
                break

            # Inject rejection critique
            critique = (
                f"[EVALUATOR MENOLAK JAWABAN]\n"
                f"{feedback.replace('REJECTED:', '').strip()}\n\n"
                f"Perbaiki berdasarkan koreksi di atas."
            )
            messages = messages + [
                {"role": "assistant", "content": answer},
                {"role": "user", "content": critique},
            ]

        return {
            "answer": best_answer,
            "iterations": iterations,
            "approved": last_feedback.upper().startswith("APPROVED"),
            "providers_used": {
                "executor": exec_provider,
                "evaluator": eval_provider,
            },
            "evaluator_notes": last_feedback,
        }


# ══════════════════════════════════════════════════════════════════════════════
# DATA CONTEXT BUILDERS
# ══════════════════════════════════════════════════════════════════════════════

def build_business_context(db) -> str:
    from . import models
    from sqlalchemy import func
    from datetime import date, timedelta

    today = date.today()
    last_30 = today - timedelta(days=30)
    last_7  = today - timedelta(days=7)
    lines = []

    sales_today = db.query(models.Sale).filter(models.Sale.date == today).all()
    sales_7d    = db.query(models.Sale).filter(models.Sale.date >= last_7).all()
    sales_30d   = db.query(models.Sale).filter(models.Sale.date >= last_30).all()

    lines.append("=== PENJUALAN ===")
    lines.append(f"Hari ini: {len(sales_today)} transaksi, total Rp {sum(s.total for s in sales_today):,.0f}")
    lines.append(f"7 hari  : {len(sales_7d)} transaksi, total Rp {sum(s.total for s in sales_7d):,.0f}")
    lines.append(f"30 hari : {len(sales_30d)} transaksi, total Rp {sum(s.total for s in sales_30d):,.0f}")
    if sales_30d:
        avg = sum(s.total for s in sales_30d) / len(sales_30d)
        cash = sum(s.total for s in sales_30d if s.payment_method == "cash")
        transfer = sum(s.total for s in sales_30d if s.payment_method == "transfer")
        lines.append(f"Rata-rata transaksi: Rp {avg:,.0f}")
        lines.append(f"Cash: Rp {cash:,.0f} | Transfer: Rp {transfer:,.0f}")

    lines.append("\n=== TOP 10 BARANG TERJUAL (30 hari) ===")
    top = db.query(
        models.Item.name, models.Item.sell_price, models.Item.buy_price,
        func.sum(models.SaleItem.qty).label("qty"),
        func.sum(models.SaleItem.total).label("revenue")
    ).join(models.SaleItem).join(models.Sale).filter(
        models.Sale.date >= last_30
    ).group_by(models.Item.id).order_by(func.sum(models.SaleItem.qty).desc()).limit(10).all()
    for i, t in enumerate(top, 1):
        margin = ((t.sell_price - t.buy_price) / t.sell_price * 100) if t.sell_price > 0 else 0
        lines.append(f"{i}. {t.name} — {t.qty:.0f} unit, Rp {t.revenue:,.0f}, margin {margin:.1f}%")

    lines.append("\n=== STOK KRITIS ===")
    low = db.query(models.Item).filter(
        models.Item.is_active == True,
        models.Item.stock <= models.Item.min_stock
    ).all()
    if low:
        for i in low[:10]:
            lines.append(f"- {i.name}: stok {i.stock}, min {i.min_stock}")
    else:
        lines.append("Semua stok aman")

    items_all = db.query(models.Item).filter(models.Item.is_active == True).all()
    lines.append(f"\n=== INVENTARIS ===")
    lines.append(f"Total item aktif: {len(items_all)}")
    lines.append(f"Nilai stok: Rp {sum(i.stock * i.buy_price for i in items_all):,.0f}")

    payables = db.query(models.Purchase).filter(
        models.Purchase.status.in_(["unpaid", "partial"])
    ).all()
    receivables = db.query(models.Sale).filter(
        models.Sale.status.in_(["unpaid", "partial"])
    ).all()
    lines.append(f"\n=== KEUANGAN ===")
    lines.append(f"Hutang ke supplier: Rp {sum(p.total - p.paid for p in payables):,.0f} ({len(payables)} PO)")
    lines.append(f"Piutang dari pelanggan: Rp {sum(s.total - s.paid for s in receivables):,.0f} ({len(receivables)} faktur)")

    lines.append("\n=== TREN 14 HARI ===")
    for i in range(13, -1, -1):
        d = today - timedelta(days=i)
        day_sales = db.query(models.Sale).filter(models.Sale.date == d).all()
        lines.append(f"{d.strftime('%d/%m')}: {len(day_sales)} tx, Rp {sum(s.total for s in day_sales):,.0f}")

    customers = db.query(models.Customer).filter(models.Customer.is_active == True).count()
    active_custs = db.query(models.Sale.customer_id).filter(
        models.Sale.date >= last_30, models.Sale.customer_id != None
    ).distinct().count()
    lines.append(f"\n=== PELANGGAN ===")
    lines.append(f"Total terdaftar: {customers} | Aktif 30 hari: {active_custs}")

    return "\n".join(lines)


def build_anomaly_context(db) -> str:
    from . import models
    from sqlalchemy import func
    from datetime import date, timedelta

    today = date.today()
    last_30 = today - timedelta(days=30)
    last_7  = today - timedelta(days=7)
    lines = ["=== DATA ANOMALI ==="]

    lines.append("\n--- Per Kasir (30 hari) ---")
    users = db.query(models.User).filter(models.User.is_active == True).all()
    for u in users:
        sales = db.query(models.Sale).filter(
            models.Sale.created_by == u.id,
            models.Sale.date >= last_30
        ).all()
        if not sales:
            continue
        rev = sum(s.total for s in sales)
        disc = sum(s.discount for s in sales)
        zero = [s for s in sales if s.total == 0]
        lines.append(
            f"[{u.username}] {len(sales)} tx, "
            f"Rp {rev:,.0f}, diskon Rp {disc:,.0f}, tx Rp0: {len(zero)}"
        )

    lines.append("\n--- Diskon > 30% (30 hari) ---")
    suspicious = [
        s for s in db.query(models.Sale).filter(models.Sale.date >= last_30).all()
        if s.subtotal > 0 and (s.discount / s.subtotal) > 0.30
    ]
    if suspicious:
        for s in suspicious[:10]:
            pct = s.discount / s.subtotal * 100
            u = db.query(models.User).get(s.created_by)
            lines.append(f"  {s.number} | {s.date} | diskon {pct:.1f}% | user: {u.username if u else '?'}")
    else:
        lines.append("  Tidak ada")

    lines.append("\n--- Transaksi di Luar Jam (< 07:00 atau > 22:00, 7 hari) ---")
    odd = [(s, s.created_at.hour) for s in db.query(models.Sale).filter(
        models.Sale.date >= last_7
    ).all() if s.created_at and (s.created_at.hour < 7 or s.created_at.hour >= 22)]
    if odd:
        for s, h in odd[:10]:
            lines.append(f"  {s.number} | {h:02d}:xx | Rp {s.total:,.0f}")
    else:
        lines.append("  Tidak ada")

    retur = db.query(models.SaleReturn).filter(models.SaleReturn.date >= last_30).all()
    total_retur = sum(r.total for r in retur)
    total_sales_val = sum(s.total for s in db.query(models.Sale).filter(
        models.Sale.date >= last_30).all())
    retur_rate = (total_retur / total_sales_val * 100) if total_sales_val else 0
    lines.append(f"\n--- Retur Penjualan (30 hari) ---")
    lines.append(f"  {len(retur)} retur, Rp {total_retur:,.0f}, rate {retur_rate:.2f}%")

    lines.append("\n--- Audit Log (7 hari) ---")
    audit = db.query(models.AuditLog).filter(
        models.AuditLog.action.in_(["UPDATE", "DELETE"]),
        models.AuditLog.created_at >= (date.today() - timedelta(days=7))
    ).order_by(models.AuditLog.created_at.desc()).limit(15).all()
    if audit:
        for a in audit:
            u = db.query(models.User).get(a.user_id)
            lines.append(f"  [{a.action}] {a.table_name} | {u.username if u else '?'} | {a.detail or '-'}")
    else:
        lines.append("  Tidak ada perubahan data")

    return "\n".join(lines)


# Singleton engine
_engine: Optional[AIEngine] = None

def get_engine() -> AIEngine:
    global _engine
    if _engine is None:
        _engine = AIEngine()
    return _engine
