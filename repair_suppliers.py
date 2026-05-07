from backend.app.database import SessionLocal
from backend.app import models
from sqlalchemy import func

def repair_suppliers():
    db = SessionLocal()
    try:
        # 1. Cari semua pasangan (item_id, supplier_id) dari history pembelian
        links = db.query(
            models.PurchaseItem.item_id,
            models.Purchase.supplier_id,
            func.max(models.PurchaseItem.buy_price).label('last_price')
        ).join(
            models.Purchase, models.PurchaseItem.purchase_id == models.Purchase.id
        ).group_by(
            models.PurchaseItem.item_id,
            models.Purchase.supplier_id
        ).all()

        added_count = 0
        for item_id, supplier_id, last_price in links:
            # Cek apakah sudah ada di item_supplier
            exists = db.query(models.ItemSupplier).filter_by(
                item_id=item_id,
                supplier_id=supplier_id
            ).first()
            
            if not exists:
                # Ambil barcode item sebagai default
                item = db.query(models.Item).get(item_id)
                if item:
                    db.add(models.ItemSupplier(
                        item_id=item_id,
                        supplier_id=supplier_id,
                        buy_price=last_price or item.buy_price,
                        barcode=item.barcode
                    ))
                    added_count += 1
        
        db.commit()
        print(f"Berhasil merestorasi {added_count} hubungan supplier-barang dari history pembelian.")
    except Exception as e:
        db.rollback()
        print(f"Error saat restorasi: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    repair_suppliers()
