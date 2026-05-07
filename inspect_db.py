from backend.app.database import SessionLocal
from backend.app import models

db = SessionLocal()
items = db.query(models.Item).limit(5).all()
for item in items:
    print(f"Item: {item.name} (ID: {item.id})")
    print(f"  Suppliers: {[s.name for s in item.suppliers]}")
    print(f"  Supplier Details: {[(sd.supplier_id, sd.buy_price) for sd in item.supplier_details]}")

db.close()
