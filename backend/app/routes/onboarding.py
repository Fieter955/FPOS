"""
iPos 5.0 — Onboarding & Demo Mode
- Setup wizard step by step
- Demo data generation
- Progress tracking
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import date, timedelta
import random

from ..database import get_db
from ..auth import get_current_user, require_admin, get_password_hash
from .. import models

router = APIRouter()


@router.get("/status")
def get_onboarding_status(db: Session = Depends(get_db),
                          _=Depends(get_current_user)):
    """Cek progres onboarding"""
    has_category = db.query(models.Category).count() > 0
    has_item = db.query(models.Item).count() > 0
    has_customer = db.query(models.Customer).count() > 0
    has_supplier = db.query(models.Supplier).count() > 0
    has_sale = db.query(models.Sale).count() > 0
    has_store_setting = bool(db.query(models.User).filter(
        models.User.full_name != "Administrator",
        models.User.role == "admin"
    ).first() or db.query(models.Category).count() > 0)

    # 👇 FIX: Sesuaikan kata kunci dengan yang diminta oleh HTML (title, description, completed, url)
    steps = [
        {
            "id": "store",    
            "title": "Info Toko & Pengaturan",           
            "description": "Lengkapi nama toko, alamat, dan kontak untuk di struk.",
            "completed": has_store_setting,
            "url": "/settings"
        },
        {
            "id": "category", 
            "title": "Tambah Kategori",     
            "description": "Kelompokkan barang Anda (misal: Semen, Cat, Besi).",
            "completed": has_category,
            "url": "/items"
        },
        {
            "id": "item",     
            "title": "Tambah Data Barang",       
            "description": "Masukkan data stok awal, harga beli, dan harga jual.",
            "completed": has_item,
            "url": "/items"
        },
        {
            "id": "customer", 
            "title": "Tambah Pelanggan",    
            "description": "Catat data pelanggan setia untuk kemudahan piutang.",
            "completed": has_customer,
            "url": "/customers"
        },
        {
            "id": "sale",     
            "title": "Transaksi Pertama",   
            "description": "Selesaikan percobaan 1 transaksi penjualan di POS.",
            "completed": has_sale,
            "url": "/pos"
        },
    ]

    # FIX: Hitung berdasarkan kata kunci 'completed'
    done_count = sum(1 for s in steps if s["completed"])
    completed = done_count == len(steps)

    return {
        "steps": steps,
        "done_count": done_count,
        "total_steps": len(steps),
        "completed": completed,
        "percent": int(done_count / len(steps) * 100) if len(steps) > 0 else 0
    }


@router.post("/demo-data")
def load_demo_data(db: Session = Depends(get_db),
                   current_user: models.User = Depends(require_admin)):
    """
    Load demo data untuk testing / presentasi ke calon klien.
    """
    # Cek apakah sudah ada data
    if db.query(models.Item).count() > 5:
        raise HTTPException(400, "Sudah ada data cukup banyak. Demo data hanya untuk instalasi baru.")

    # ── Kategori ──────────────────────────────────────────────────────────────
    categories = {}
    for name, desc in [
        ("Minuman", "Minuman kemasan dan curah"),
        ("Makanan", "Makanan ringan dan berat"),
        ("Sembako", "Kebutuhan pokok sehari-hari"),
        ("Kebersihan", "Produk kebersihan rumah"),
        ("Rokok", "Rokok dan produk tembakau"),
    ]:
        cat = models.Category(name=name, description=desc)
        db.add(cat); db.flush()
        categories[name] = cat.id

    # ── Satuan ────────────────────────────────────────────────────────────────
    units = {}
    for name, abbr in [("Pcs", "pcs"), ("Karton", "ktn"), ("Kg", "kg"), ("Liter", "ltr"), ("Bungkus", "bks")]:
        u = models.Unit(name=name, abbreviation=abbr)
        db.add(u); db.flush()
        units[name] = u.id

    # ── Items ─────────────────────────────────────────────────────────────────
    demo_items = [
        ("MIN001", "Aqua 600ml", "Minuman", "Pcs", 2500, 3500, 100, 20),
        ("MIN002", "Teh Botol Sosro 350ml", "Minuman", "Pcs", 3500, 5000, 80, 15),
        ("MIN003", "Pocari Sweat 350ml", "Minuman", "Pcs", 5000, 7000, 60, 10),
        ("MIN004", "Coca Cola 330ml", "Minuman", "Pcs", 4000, 6000, 50, 10),
        ("MAK001", "Indomie Goreng", "Makanan", "Bungkus", 2800, 3500, 200, 50),
        ("MAK002", "Indomie Kuah", "Makanan", "Bungkus", 2800, 3500, 150, 50),
        ("MAK003", "Chitato Sapi Panggang", "Makanan", "Pcs", 7000, 10000, 40, 10),
        ("MAK004", "Oreo Original", "Makanan", "Pcs", 8000, 12000, 30, 5),
        ("SMB001", "Beras Premium 5kg", "Sembako", "Pcs", 65000, 75000, 30, 10),
        ("SMB002", "Minyak Goreng 1L", "Sembako", "Pcs", 15000, 18000, 40, 10),
        ("SMB003", "Gula Pasir 1kg", "Sembako", "Kg", 14000, 17000, 25, 5),
        ("KBR001", "Rinso 800gr", "Kebersihan", "Pcs", 18000, 25000, 20, 5),
        ("KBR002", "Sabun Lifebuoy", "Kebersihan", "Pcs", 4000, 6000, 50, 10),
        ("ROK001", "Gudang Garam Merah", "Rokok", "Pcs", 20000, 25000, 30, 10),
        ("ROK002", "Sampoerna Mild", "Rokok", "Pcs", 28000, 35000, 25, 10),
    ]

    item_map = {}
    for code, name, cat, unit, buy, sell, stock, min_stock in demo_items:
        item = models.Item(
            code=code, name=name,
            category_id=categories[cat],
            unit_id=units[unit],
            buy_price=buy, sell_price=sell,
            stock=stock, min_stock=min_stock,
            is_active=True
        )
        db.add(item); db.flush()
        item_map[code] = item

    # ── Customer Groups & Customers ───────────────────────────────────────────
    grp = models.CustomerGroup(name="Member", discount_percent=5)
    db.add(grp); db.flush()

    customers_data = [
        ("C001", "Budi Santoso", "08123456789", "budi@email.com", grp.id),
        ("C002", "Sari Dewi", "08987654321", "sari@email.com", None),
        ("C003", "Ahmad Fauzi", "08111222333", None, grp.id),
    ]
    cust_list = []
    for code, name, phone, email, gid in customers_data:
        c = models.Customer(code=code, name=name, phone=phone, email=email,
                            group_id=gid, is_active=True)
        db.add(c); db.flush()
        cust_list.append(c)

    # ── Supplier ──────────────────────────────────────────────────────────────
    suppliers_data = [
        ("SUP001", "PT. Indofood Sukses", "021-5555001", "Pak Hendra"),
        ("SUP002", "CV. Sinar Jaya", "021-5555002", "Bu Ratna"),
    ]
    sup_list = []
    for code, name, phone, contact in suppliers_data:
        s = models.Supplier(code=code, name=name, phone=phone,
                            contact_person=contact, is_active=True)
        db.add(s); db.flush()
        sup_list.append(s)

    # ── Demo Sales (7 hari terakhir) ──────────────────────────────────────────
    sale_items_pool = [
        ("MIN001", 5), ("MIN002", 3), ("MAK001", 10), ("MAK002", 8),
        ("SMB001", 2), ("KBR002", 3), ("ROK001", 5),
    ]

    for days_ago in range(7, 0, -1):
        sale_date = date.today() - timedelta(days=days_ago)
        num_sales = random.randint(3, 8)

        for i in range(num_sales):
            today_str = sale_date.strftime("%Y%m%d")
            prefix = f"INV{today_str}"
            last = db.query(models.Sale).filter(
                models.Sale.number.like(f"{prefix}%")
            ).order_by(models.Sale.id.desc()).first()
            seq = int(last.number[-4:]) + 1 if last else 1
            sale_number = f"{prefix}{seq:04d}"

            items_for_sale = random.sample(sale_items_pool,
                                           random.randint(1, 3))
            customer = random.choice([None] + cust_list)
            payment = random.choice(["cash", "cash", "transfer"])

            subtotal = 0.0
            sale_item_records = []

            for code, max_qty in items_for_sale:
                item = item_map.get(code)
                if not item or item.stock <= 0: continue
                qty = random.randint(1, min(max_qty, int(item.stock)))
                line_total = item.sell_price * qty
                subtotal += line_total
                sale_item_records.append((item, qty, line_total))

            if not sale_item_records: continue

            sale = models.Sale(
                number=sale_number, date=sale_date,
                customer_id=customer.id if customer else None,
                subtotal=subtotal, discount=0, tax=0,
                total=subtotal, paid=subtotal, change=0,
                payment_method=payment, status="paid"
            )
            db.add(sale); db.flush()

            for item, qty, line_total in sale_item_records:
                db.add(models.SaleItem(
                    sale_id=sale.id, item_id=item.id,
                    qty=qty, sell_price=item.sell_price,
                    discount=0, total=line_total
                ))
                item.stock -= qty

    db.commit()

    total_sales = db.query(models.Sale).count()
    return {
        "message": "Demo data berhasil dimuat!",
        "summary": {
            "categories": len(categories),
            "items": len(demo_items),
            "customers": len(customers_data),
            "suppliers": len(suppliers_data),
            "sales": total_sales
        }
    }


@router.delete("/demo-data")
def clear_demo_data(db: Session = Depends(get_db),
                    _=Depends(require_admin)):
    """Hapus semua demo data (kecuali user admin)"""
    # Urutan penting karena FK constraints
    db.query(models.SaleItem).delete()
    db.query(models.Sale).delete()
    db.query(models.PurchaseItem).delete()
    db.query(models.Purchase).delete()
    db.query(models.StockMovement).delete()
    db.query(models.ItemPrice).delete()
    db.query(models.Item).delete()
    db.query(models.Category).delete()
    db.query(models.Unit).delete()
    db.query(models.Customer).delete()
    db.query(models.CustomerGroup).delete()
    db.query(models.Supplier).delete()
    db.commit()
    return {"message": "Semua demo data dihapus. Aplikasi siap diisi data nyata."}
