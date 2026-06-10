from datetime import datetime

import pytz
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Table, Text, Date
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .database import Base


# ─── Auth & Users ─────────────────────────────────────────────────────────────
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False)
    email = Column(String(100), unique=True, nullable=True)
    full_name = Column(String(100))
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(20), default="kasir")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Cabang asal karyawan
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=True)
    branch = relationship("Branch", back_populates="users")
    
    # 👇 TAMBAHKAN BARIS INI (Cabang aktif saat login) 👇
    active_branch_id = Column(Integer, nullable=True)
    active_branch = relationship("Branch", primaryjoin="User.active_branch_id == Branch.id", foreign_keys=[active_branch_id])

    @property
    def branch_status(self):
        if self.active_branch:
            return self.active_branch.status
        return "Cabang"


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    action = Column(String(20), nullable=False)   # CREATE | UPDATE | DELETE | LOGIN
    table_name = Column(String(50))
    record_id = Column(Integer, nullable=True)
    detail = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    user = relationship("User")


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), nullable=False)
    ip_address = Column(String(50))
    success = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class Role(Base):
    __tablename__ = "roles"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), unique=True, nullable=False)
    description = Column(Text)

    # ─── Data Cabang ─────────────────────────────────────────────────────────────
class Branch(Base):
    __tablename__ = "branches"
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(20), unique=True, nullable=False) # Misal: CBG-01
    name = Column(String(100), nullable=False)             # Misal: Eva Store - Pusat
    address = Column(Text)
    phone = Column(String(20))
    status = Column(String(20), default="Cabang") # Toko Utama | Cabang
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    is_setup_complete = Column(Boolean, default=False)
    
    # Relasi ke tabel lain
    users = relationship("User", back_populates="branch")
    warehouses = relationship("Warehouse", back_populates="branch")
    sales = relationship("Sale", back_populates="branch")
    purchases = relationship("Purchase", back_populates="branch", foreign_keys="[Purchase.branch_id]")
    shifts = relationship("Shift", back_populates="branch")


# ─── Shift / Tutup Kasir ──────────────────────────────────────────────────────
class Shift(Base):
    __tablename__ = "shifts"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    opening_cash = Column(Float, default=0)
    closing_cash = Column(Float, nullable=True)
    system_cash = Column(Float, nullable=True)   # calculated from sales
    difference = Column(Float, nullable=True)
    opened_at = Column(DateTime(timezone=True), server_default=func.now())
    closed_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(10), default="open")   # open | closed
    notes = Column(Text)
    user = relationship("User")
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=True)
    branch = relationship("Branch", back_populates="shifts")
    
    # 👇 TAMBAHAN UNTUK SETORAN KE PUSAT 👇
    is_deposited = Column(Boolean, default=False)
    deposit_id = Column(Integer, ForeignKey("branch_deposits.id"), nullable=True)
    deposit = relationship("BranchDeposit", back_populates="shifts")

class BranchDeposit(Base):
    __tablename__ = "branch_deposits"
    id = Column(Integer, primary_key=True, index=True)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    date = Column(Date, nullable=False)
    amount = Column(Float, nullable=False) # Total
    cash_amount = Column(Float, default=0)
    bank_amount = Column(Float, default=0)
    bank_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    journal_id = Column(Integer, ForeignKey("journals.id"), nullable=True)
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    branch = relationship("Branch")
    bank_account = relationship("Account")
    journal = relationship("Journal")
    shifts = relationship("Shift", back_populates="deposit")

# ─── Master Data ──────────────────────────────────────────────────────────────


# 👇 1. TAMBAHKAN MODEL PERANTARA item dan supplier untuk relasi many-to-many dengan data tambahan 👇
class ItemSupplier(Base):
    __tablename__ = "item_supplier"
    item_id = Column(Integer, ForeignKey("items.id"), primary_key=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), primary_key=True)
    buy_price = Column(Float, default=0)
    barcode = Column(String(100)) # Barcode khusus supplier ini

    item = relationship("Item", back_populates="supplier_details")
    supplier = relationship("Supplier", back_populates="item_details")

class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text)
    items = relationship("Item", back_populates="category")


class Brand(Base):
    __tablename__ = "brands"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text)
    items = relationship("Item", back_populates="brand")


class Unit(Base):
    __tablename__ = "units"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), nullable=False)
    abbreviation = Column(String(10))
    items = relationship("Item", back_populates="unit")


class Item(Base):
    __tablename__ = "items"
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(50), unique=True, nullable=False)
    name = Column(String(200), unique=True, nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=True)
    unit_id = Column(Integer, ForeignKey("units.id"), nullable=True)
    buy_price = Column(Float, default=0)
    sell_price = Column(Float, default=0)
    profit_margin = Column(Float, default=0)
    stock = Column(Float, default=0)
    min_stock = Column(Float, default=0)
    description = Column(Text)
    barcode = Column(String(100))
    parent_item_id = Column(Integer, ForeignKey("items.id"), nullable=True)
    conversion_factor_to_parent = Column(Float, default=1)
    is_virtual_variant = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    is_discountable = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    category = relationship("Category", back_populates="items")
    brand = relationship("Brand", back_populates="items")
    unit = relationship("Unit", back_populates="items")
    prices = relationship("ItemPrice", back_populates="item", cascade="all, delete-orphan")
    sale_items = relationship("SaleItem", back_populates="item")
    purchase_items = relationship("PurchaseItem", back_populates="item")
    stock_movements = relationship("StockMovement", back_populates="item")

    # Relasi detail supplier (dengan harga khusus)
    supplier_details = relationship("ItemSupplier", back_populates="item", cascade="all, delete-orphan")
    # Tetap sediakan relasi simpel untuk list supplier
    suppliers = relationship("Supplier", secondary="item_supplier", back_populates="items", viewonly=True)

class ItemPrice(Base):
    __tablename__ = "item_prices"
    id = Column(Integer, primary_key=True, index=True)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False)
    name = Column(String(50), nullable=False)
    price = Column(Float, nullable=False)
    min_qty = Column(Float, default=1)
    item = relationship("Item", back_populates="prices")


class CustomerGroup(Base):
    __tablename__ = "customer_groups"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    discount_percent = Column(Float, default=0)
    customers = relationship("Customer", back_populates="group")


class Customer(Base):
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(50), unique=True, nullable=False)
    name = Column(String(200), nullable=False)
    address = Column(Text)
    phone = Column(String(20))
    email = Column(String(100))
    group_id = Column(Integer, ForeignKey("customer_groups.id"), nullable=True)
    points = Column(Float, default=0)
    credit_limit = Column(Float, default=0)
    deposit_balance = Column(Float, default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    group = relationship("CustomerGroup", back_populates="customers")
    sales = relationship("Sale", back_populates="customer")


class Supplier(Base):
    __tablename__ = "suppliers"
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(50), unique=True, nullable=False)
    name = Column(String(200), unique=True, nullable=False)
    address = Column(Text)
    phone = Column(String(20))
    email = Column(String(100))
    PpnSupplier = Column(Float, default=0)
    credit_limit = Column(Float, default=0)
    deposit_balance = Column(Float, default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    purchases = relationship("Purchase", back_populates="supplier")
    
    # Relasi detail item (dengan harga khusus dari supplier ini)
    item_details = relationship("ItemSupplier", back_populates="supplier", cascade="all, delete-orphan")
    items = relationship("Item", secondary="item_supplier", back_populates="suppliers", viewonly=True)


class SalesPerson(Base):
    __tablename__ = "salespersons"
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(50), unique=True, nullable=False)
    name = Column(String(200), nullable=False)
    commission_percent = Column(Float, default=0)
    phone = Column(String(20))
    is_active = Column(Boolean, default=True)



# ─── Pembelian ────────────────────────────────────────────────────────────────
class Purchase(Base):
    __tablename__ = "purchases"
    id = Column(Integer, primary_key=True, index=True)
    number = Column(String(50), unique=True, nullable=False)
    date = Column(Date, nullable=False)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=True)
    subtotal = Column(Float, default=0)
    discount = Column(Float, default=0)
    tax = Column(Float, default=0)
    tax_percent = Column(Float, default=0)
    is_tax_included = Column(Boolean, default=True)
    total = Column(Float, default=0)
    paid = Column(Float, default=0)
    status = Column(String(20), default="unpaid")
    notes = Column(Text)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    supplier = relationship("Supplier", back_populates="purchases")
    items = relationship("PurchaseItem", back_populates="purchase", cascade="all, delete-orphan")
    returns = relationship("PurchaseReturn", back_populates="purchase")
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    branch = relationship("Branch", back_populates="purchases", foreign_keys=[branch_id])
    
    # 👇 TAMBAHAN UNTUK REQUEST CABANG (PO) 👇
    is_branch_request = Column(Boolean, default=False)
    target_branch_id = Column(Integer, ForeignKey("branches.id"), nullable=True)
    target_branch = relationship("Branch", foreign_keys=[target_branch_id])
    from_po_id = Column(Integer, ForeignKey("purchases.id"), nullable=True)
    from_po = relationship("Purchase", remote_side=[id], back_populates="fulfillment_drafts")
    fulfillment_drafts = relationship("Purchase", back_populates="from_po")
    is_received_by_branch = Column(Boolean, default=False)


class PurchaseItem(Base):
    __tablename__ = "purchase_items"
    id = Column(Integer, primary_key=True, index=True)
    purchase_id = Column(Integer, ForeignKey("purchases.id"), nullable=False)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False)
    qty = Column(Float, nullable=False) # 👇 Stok yang bertambah (sama dengan qty_received jika sudah diterima)
    qty_ordered = Column(Float, default=0) # 👇 Jumlah pesanan awal
    qty_received = Column(Float, default=0) # 👇 Jumlah yang benar-benar diterima
    buy_price = Column(Float, nullable=False)
    discount = Column(Float, default=0)
    disc1 = Column(Float, default=0)
    disc2 = Column(Float, default=0)
    total = Column(Float, nullable=False)
    purchase = relationship("Purchase", back_populates="items")
    item = relationship("Item", back_populates="purchase_items")


class PurchaseReturn(Base):
    __tablename__ = "purchase_returns"
    id = Column(Integer, primary_key=True, index=True)
    number = Column(String(50), unique=True, nullable=False)
    date = Column(Date, nullable=False)
    purchase_id = Column(Integer, ForeignKey("purchases.id"), nullable=False)
    tax_percent = Column(Float, default=0)
    is_tax_included = Column(Boolean, default=True)
    total = Column(Float, default=0)
    reason = Column(String(200))
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    purchase = relationship("Purchase", back_populates="returns")
    items = relationship("PurchaseReturnItem", back_populates="return_", cascade="all, delete-orphan")


class PurchaseReturnItem(Base):
    __tablename__ = "purchase_return_items"
    id = Column(Integer, primary_key=True, index=True)
    return_id = Column(Integer, ForeignKey("purchase_returns.id"), nullable=False)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False)
    qty = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    total = Column(Float, nullable=False)
    return_ = relationship("PurchaseReturn", back_populates="items")


# ─── Penjualan ────────────────────────────────────────────────────────────────
class Sale(Base):
    __tablename__ = "sales"
    id = Column(Integer, primary_key=True, index=True)
    number = Column(String(50), unique=True, nullable=False)
    date = Column(Date, nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    salesperson_id = Column(Integer, ForeignKey("salespersons.id"), nullable=True)
    shift_id = Column(Integer, ForeignKey("shifts.id"), nullable=True)
    subtotal = Column(Float, default=0)
    discount = Column(Float, default=0)
    tax = Column(Float, default=0)
    tax_percent = Column(Float, default=0)
    is_tax_included = Column(Boolean, default=True)
    total = Column(Float, default=0)
    paid = Column(Float, default=0)
    change = Column(Float, default=0)
    payment_method = Column(String(20), default="cash")
    status = Column(String(20), default="paid")
    notes = Column(Text)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    customer = relationship("Customer", back_populates="sales")
    items = relationship("SaleItem", back_populates="sale", cascade="all, delete-orphan")
    returns = relationship("SaleReturn", back_populates="sale")
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    branch = relationship("Branch", back_populates="sales")


class SaleItem(Base):
    __tablename__ = "sale_items"
    id = Column(Integer, primary_key=True, index=True)
    sale_id = Column(Integer, ForeignKey("sales.id"), nullable=False)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False)
    qty = Column(Float, nullable=False)
    buy_price = Column(Float, default=0)
    sell_price = Column(Float, nullable=False)
    discount = Column(Float, default=0)
    total = Column(Float, nullable=False)
    sale = relationship("Sale", back_populates="items")
    item = relationship("Item", back_populates="sale_items")


class SaleReturn(Base):
    __tablename__ = "sale_returns"
    id = Column(Integer, primary_key=True, index=True)
    number = Column(String(50), unique=True, nullable=False)
    date = Column(Date, nullable=False)
    sale_id = Column(Integer, ForeignKey("sales.id"), nullable=False)
    tax_percent = Column(Float, default=0)
    is_tax_included = Column(Boolean, default=True)
    total = Column(Float, default=0)
    reason = Column(String(200))
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    sale = relationship("Sale", back_populates="returns")
    items = relationship("SaleReturnItem", back_populates="return_", cascade="all, delete-orphan")


class SaleReturnItem(Base):
    __tablename__ = "sale_return_items"
    id = Column(Integer, primary_key=True, index=True)
    return_id = Column(Integer, ForeignKey("sale_returns.id"), nullable=False)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False)
    qty = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    total = Column(Float, nullable=False)
    return_ = relationship("SaleReturn", back_populates="items")


# ─── Persediaan ───────────────────────────────────────────────────────────────
class StockMovement(Base):
    __tablename__ = "stock_movements"
    id = Column(Integer, primary_key=True, index=True)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    date = Column(Date, nullable=False)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False)
    type = Column(String(20), nullable=False)
    qty = Column(Float, nullable=False)
    qty_before = Column(Float, default=0)
    qty_after = Column(Float, default=0)
    reference = Column(String(100))
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    item = relationship("Item", back_populates="stock_movements")

class CashTransaction(Base):
    __tablename__ = "cash_transactions"
    id = Column(Integer, primary_key=True, index=True)
    number = Column(String(50), unique=True, nullable=False)
    date = Column(Date, nullable=False)
    type = Column(String(10), nullable=False)
    amount = Column(Float, nullable=False)
    description = Column(Text)
    reference = Column(String(100))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False) 
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False) # Tambahkan ini di model Kas
    account = relationship("Account")


# ─── Konsinyasi ───────────────────────────────────────────────────────────────
class ConsignmentIn(Base):
    __tablename__ = "consignment_ins"
    id = Column(Integer, primary_key=True, index=True)
    number = Column(String(50), unique=True, nullable=False)
    date = Column(Date, nullable=False)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False)
    status = Column(String(20), default="active")
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    items = relationship("ConsignmentInItem", back_populates="consignment", cascade="all, delete-orphan")
    bills = relationship("ConsignmentInBill", back_populates="consignment")


class ConsignmentInItem(Base):
    __tablename__ = "consignment_in_items"
    id = Column(Integer, primary_key=True, index=True)
    consignment_id = Column(Integer, ForeignKey("consignment_ins.id"), nullable=False)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False)
    qty_received = Column(Float, nullable=False)
    qty_sold = Column(Float, default=0)
    qty_returned = Column(Float, default=0)
    sell_price = Column(Float, nullable=False)
    consign_price = Column(Float, nullable=False)
    consignment = relationship("ConsignmentIn", back_populates="items")


class ConsignmentInBill(Base):
    __tablename__ = "consignment_in_bills"
    id = Column(Integer, primary_key=True, index=True)
    number = Column(String(50), unique=True, nullable=False)
    date = Column(Date, nullable=False)
    consignment_id = Column(Integer, ForeignKey("consignment_ins.id"), nullable=False)
    amount = Column(Float, nullable=False)
    paid = Column(Float, default=0)
    status = Column(String(20), default="unpaid")
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    consignment = relationship("ConsignmentIn", back_populates="bills")


class ConsignmentOut(Base):
    __tablename__ = "consignment_outs"
    id = Column(Integer, primary_key=True, index=True)
    number = Column(String(50), unique=True, nullable=False)
    date = Column(Date, nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    status = Column(String(20), default="active")
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    items = relationship("ConsignmentOutItem", back_populates="consignment", cascade="all, delete-orphan")
    bills = relationship("ConsignmentOutBill", back_populates="consignment")


class ConsignmentOutItem(Base):
    __tablename__ = "consignment_out_items"
    id = Column(Integer, primary_key=True, index=True)
    consignment_id = Column(Integer, ForeignKey("consignment_outs.id"), nullable=False)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False)
    qty_sent = Column(Float, nullable=False)
    qty_sold = Column(Float, default=0)
    qty_returned = Column(Float, default=0)
    sell_price = Column(Float, nullable=False)
    consignment = relationship("ConsignmentOut", back_populates="items")


class ConsignmentOutBill(Base):
    __tablename__ = "consignment_out_bills"
    id = Column(Integer, primary_key=True, index=True)
    number = Column(String(50), unique=True, nullable=False)
    date = Column(Date, nullable=False)
    consignment_id = Column(Integer, ForeignKey("consignment_outs.id"), nullable=False)
    amount = Column(Float, nullable=False)
    paid = Column(Float, default=0)
    status = Column(String(20), default="unpaid")
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    consignment = relationship("ConsignmentOut", back_populates="bills")

# ─── Akuntansi Lengkap ────────────────────────────────────────────────────────

class Account(Base):
    """Chart of Accounts / Daftar Akun"""
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(20), unique=True, nullable=False)
    name = Column(String(150), nullable=False)
    # Tipe: asset | liability | equity | revenue | expense
    type = Column(String(20), nullable=False)
    # Sub-tipe untuk pengelompokan neraca
    # asset: current_asset | fixed_asset
    # liability: current_liability | long_term_liability
    # equity: capital | retained_earnings
    # revenue: operating | non_operating
    # expense: cogs | operating | non_operating
    subtype = Column(String(30), nullable=True)
    normal_balance = Column(String(10), default="debit")  # debit | credit
    is_active = Column(Boolean, default=True)
    opening_balance = Column(Float, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    journal_debits = relationship("JournalEntryLine",
                                  foreign_keys="JournalEntryLine.debit_account_id",
                                  back_populates="debit_account")
    journal_credits = relationship("JournalEntryLine",
                                   foreign_keys="JournalEntryLine.credit_account_id",
                                   back_populates="credit_account")


class Journal(Base):
    """Jurnal Umum — header transaksi akuntansi"""
    __tablename__ = "journals"
    id = Column(Integer, primary_key=True, index=True)
    number = Column(String(50), unique=True, nullable=False)
    date = Column(Date, nullable=False)
    description = Column(Text, nullable=False)
    branch_id = Column(Integer, nullable=True)
    reference = Column(String(100))   # nomor faktur/PO yang terkait
    source = Column(String(20), default="manual")  # manual | sale | purchase | cash
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    lines = relationship("JournalEntryLine", back_populates="journal",
                         cascade="all, delete-orphan")
    creator = relationship("User")


class JournalEntryLine(Base):
    """Baris jurnal — double entry (debit & credit)"""
    __tablename__ = "journal_entry_lines"
    id = Column(Integer, primary_key=True, index=True)
    journal_id = Column(Integer, ForeignKey("journals.id"), nullable=False)
    debit_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    credit_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    amount = Column(Float, nullable=False)
    description = Column(Text)

    journal = relationship("Journal", back_populates="lines")
    debit_account = relationship("Account",
                                  foreign_keys=[debit_account_id],
                                  back_populates="journal_debits")
    credit_account = relationship("Account",
                                   foreign_keys=[credit_account_id],
                                   back_populates="journal_credits")

# ─── Sistem Lisensi ────────────────────────────────────────────────────────────

# ─── Sistem Lisensi & Penagihan (KILL SWITCH) ──────────────────────────────────

class BookClose(Base):
    __tablename__ = "book_closes"
    id = Column(Integer, primary_key=True, index=True)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=True)
    close_type = Column(String(10), default="month")  # month | year
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    notes = Column(Text)
    is_active = Column(Boolean, default=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    reopened_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    reopened_at = Column(DateTime(timezone=True), nullable=True)

    branch = relationship("Branch")
    creator = relationship("User", foreign_keys=[created_by])
    reopener = relationship("User", foreign_keys=[reopened_by])

class License(Base):
    __tablename__ = "licenses"
    id = Column(Integer, primary_key=True, index=True)
    license_key = Column(String(64), unique=True, nullable=False)
    hardware_id = Column(String(128))           
    owner_name = Column(String(200))
    owner_email = Column(String(200))
    plan = Column(String(20), default="trial")  
    status = Column(String(20), default="active") 
    
    # 👇 TAMBAHAN UNTUK KILL SWITCH 👇
    billing_status = Column(String(20), default="ok") # ok | warning | blocked
    billing_message = Column(Text, default="Aplikasi berjalan normal.")
    
    activated_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=True)
    max_users = Column(Integer, default=3)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

# 👇 TABEL BARU UNTUK BUKTI TRANSFER 👇
class LicensePayment(Base):
    __tablename__ = "license_payments"
    id = Column(Integer, primary_key=True, index=True)
    license_id = Column(Integer, ForeignKey("licenses.id"), nullable=False)
    upload_date = Column(DateTime(timezone=True), server_default=func.now())
    proof_image_path = Column(String(255), nullable=False) # Path foto struk
    status = Column(String(20), default="pending") # pending | verified
    notes = Column(Text)


# ─── Diskon Bertingkat ─────────────────────────────────────────────────────────

class DiscountTier(Base):
    """Diskon berdasarkan qty atau total belanja"""
    __tablename__ = "discount_tiers"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=True)  # null = semua item
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    min_qty = Column(Float, nullable=True)
    min_amount = Column(Float, nullable=True)
    discount_percent = Column(Float, nullable=False)
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ─── Multi Gudang ──────────────────────────────────────────────────────────────

class Warehouse(Base):
    __tablename__ = "warehouses"
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(20), unique=True, nullable=False)
    name = Column(String(100), nullable=False)
    address = Column(Text)
    is_active = Column(Boolean, default=True)
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    stock_items = relationship("WarehouseStock", back_populates="warehouse",
                               cascade="all, delete-orphan")
    transfers_from = relationship("WarehouseTransfer",
                                  foreign_keys="WarehouseTransfer.from_warehouse_id",
                                  back_populates="from_warehouse")
    transfers_to = relationship("WarehouseTransfer",
                                foreign_keys="WarehouseTransfer.to_warehouse_id",
                                back_populates="to_warehouse")
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    branch = relationship("Branch", back_populates="warehouses")


class WarehouseStock(Base):
    """Stok per item per gudang"""
    __tablename__ = "warehouse_stocks"
    id = Column(Integer, primary_key=True, index=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False)
    stock = Column(Float, default=0)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    warehouse = relationship("Warehouse", back_populates="stock_items")
    item = relationship("Item")


class WarehouseTransfer(Base):
    """Transfer stok antar gudang"""
    __tablename__ = "warehouse_transfers"
    id = Column(Integer, primary_key=True, index=True)
    number = Column(String(50), unique=True, nullable=False)
    date = Column(Date, nullable=False)
    from_warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False)
    to_warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False)
    status = Column(String(20), default="pending")  # pending | confirmed
    notes = Column(Text)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    from_warehouse = relationship("Warehouse", foreign_keys=[from_warehouse_id],
                                   back_populates="transfers_from")
    to_warehouse = relationship("Warehouse", foreign_keys=[to_warehouse_id],
                                 back_populates="transfers_to")
    items = relationship("WarehouseTransferItem", back_populates="transfer",
                         cascade="all, delete-orphan")
    creator = relationship("User")


class WarehouseTransferItem(Base):
    __tablename__ = "warehouse_transfer_items"
    id = Column(Integer, primary_key=True, index=True)
    transfer_id = Column(Integer, ForeignKey("warehouse_transfers.id"), nullable=False)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False)
    qty = Column(Float, nullable=False)

    transfer = relationship("WarehouseTransfer", back_populates="items")
    item = relationship("Item")


# ─── Perakitan (Assembly) ──────────────────────────────────────────────────────

class BillOfMaterial(Base):
    """Bill of Materials — resep/formula produk jadi"""
    __tablename__ = "bill_of_materials"
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("items.id"), nullable=False)  # produk jadi
    qty_produced = Column(Float, default=1)  # berapa unit dihasilkan sekali rakit
    notes = Column(Text)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    product = relationship("Item", foreign_keys=[product_id])
    materials = relationship("BOMLine", back_populates="bom",
                             cascade="all, delete-orphan")
    assemblies = relationship("Assembly", back_populates="bom")


class BOMLine(Base):
    """Bahan baku dalam Bill of Materials"""
    __tablename__ = "bom_lines"
    id = Column(Integer, primary_key=True, index=True)
    bom_id = Column(Integer, ForeignKey("bill_of_materials.id"), nullable=False)
    material_id = Column(Integer, ForeignKey("items.id"), nullable=False)  # bahan baku
    qty_needed = Column(Float, nullable=False)

    bom = relationship("BillOfMaterial", back_populates="materials")
    material = relationship("Item", foreign_keys=[material_id])


class Assembly(Base):
    """Order/proses perakitan"""
    __tablename__ = "assemblies"
    id = Column(Integer, primary_key=True, index=True)
    number = Column(String(50), unique=True, nullable=False)
    date = Column(Date, nullable=False)
    bom_id = Column(Integer, ForeignKey("bill_of_materials.id"), nullable=False)
    qty_planned = Column(Float, nullable=False)
    qty_produced = Column(Float, default=0)
    status = Column(String(20), default="draft")  # draft | in_progress | done | cancelled
    notes = Column(Text)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    bom = relationship("BillOfMaterial", back_populates="assemblies")
    creator = relationship("User")


# ─── Notifikasi WA/Telegram ────────────────────────────────────────────────────

class NotificationConfig(Base):
    __tablename__ = "notification_configs"
    id = Column(Integer, primary_key=True, index=True)
    channel = Column(String(20), nullable=False)   # whatsapp | telegram
    # WhatsApp: nomor HP target, pakai Fonnte/WA Gateway
    # Telegram: chat_id target
    target = Column(String(100), nullable=False)
    api_key = Column(String(255))   # Fonnte API key atau Telegram Bot Token
    # Event triggers (JSON string of list)
    events = Column(Text, default="[]")
    # Events: low_stock, large_sale, suspicious_transaction, daily_report, shift_close
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class NotificationLog(Base):
    __tablename__ = "notification_logs"
    id = Column(Integer, primary_key=True, index=True)
    channel = Column(String(20))
    event = Column(String(50))
    message = Column(Text)
    status = Column(String(20), default="sent")   # sent | failed
    error = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

# ─── Multi Satuan Konversi ─────────────────────────────────────────────────────

class UnitConversion(Base):
    """
    Konversi antar satuan untuk 1 item.
    Contoh: 1 Lusin Engsel = 12 Pcs Engsel
    Saat beli per lusin Rp 120.000 → harga per pcs otomatis Rp 10.000
    """
    __tablename__ = "unit_conversions"
    id = Column(Integer, primary_key=True, index=True)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False)
    child_item_id = Column(Integer, ForeignKey("items.id"), nullable=True)
    unit_id = Column(Integer, ForeignKey("units.id"), nullable=False)     # satuan besar (Lusin)
    base_unit_id = Column(Integer, ForeignKey("units.id"), nullable=False) # satuan kecil (Pcs)
    conversion_factor = Column(Float, nullable=False)  # 1 lusin = 12 pcs
    buy_price = Column(Float, default=0)   # harga beli per satuan besar
    sell_price = Column(Float, default=0)  # harga jual per satuan besar
    # harga per satuan kecil = sell_price / conversion_factor
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    item = relationship("Item", foreign_keys=[item_id])
    unit = relationship("Unit", foreign_keys=[unit_id])
    base_unit = relationship("Unit", foreign_keys=[base_unit_id])


# ─── Barcode Internal ──────────────────────────────────────────────────────────

class BarcodeLabel(Base):
    """Label barcode yang digenerate untuk barang tanpa barcode supplier"""
    __tablename__ = "barcode_labels"
    id = Column(Integer, primary_key=True, index=True)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False)
    barcode_value = Column(String(50), unique=True, nullable=False)
    barcode_type = Column(String(20), default="CODE128")  # CODE128 | QR | EAN13
    label_text = Column(String(200))   # teks di bawah barcode
    printed_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    item = relationship("Item")


# ─── Surat Jalan ───────────────────────────────────────────────────────────────

class DeliveryNote(Base):
    """Surat Jalan — dokumen pengiriman barang ke lokasi proyek"""
    __tablename__ = "delivery_notes"
    id = Column(Integer, primary_key=True, index=True)
    number = Column(String(50), unique=True, nullable=False)
    date = Column(Date, nullable=False)
    sale_id = Column(Integer, ForeignKey("sales.id"), nullable=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    delivery_address = Column(Text, nullable=False)
    recipient_name = Column(String(200))
    driver_name = Column(String(200))
    vehicle_no = Column(String(50))      # nomor kendaraan
    notes = Column(Text)
    status = Column(String(20), default="pending")  # pending | delivered | signed
    signed_at = Column(DateTime(timezone=True))
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    items = relationship("DeliveryNoteItem", back_populates="delivery",
                         cascade="all, delete-orphan")
    customer = relationship("Customer")
    creator = relationship("User")


class DeliveryNoteItem(Base):
    __tablename__ = "delivery_note_items"
    id = Column(Integer, primary_key=True, index=True)
    delivery_id = Column(Integer, ForeignKey("delivery_notes.id"), nullable=False)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False)
    qty = Column(Float, nullable=False)
    unit_name = Column(String(50))
    notes = Column(Text)

    delivery = relationship("DeliveryNote", back_populates="items")
    item = relationship("Item")


# ─── Tukar Tambah ─────────────────────────────────────────────────────────────

class TradeIn(Base):
    """
    Tukar Tambah — pelanggan kembalikan barang + bayar selisih untuk barang baru.
    Umum di toko bangunan: tukar pipa ukuran salah, tukar keramik pecah, dll.
    """
    __tablename__ = "trade_ins"
    id = Column(Integer, primary_key=True, index=True)
    number = Column(String(50), unique=True, nullable=False)
    date = Column(Date, nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    notes = Column(Text)
    # Barang yang dikembalikan pelanggan
    return_subtotal = Column(Float, default=0)
    # Barang baru yang diambil
    new_subtotal = Column(Float, default=0)
    # Selisih yang dibayar/dikembalikan ke pelanggan
    difference = Column(Float, default=0)  # positif = pelanggan bayar, negatif = toko kembalikan uang
    payment_method = Column(String(20), default="cash")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    return_items = relationship("TradeInReturnItem", back_populates="trade_in",
                                cascade="all, delete-orphan")
    new_items = relationship("TradeInNewItem", back_populates="trade_in",
                             cascade="all, delete-orphan")
    customer = relationship("Customer")
    creator = relationship("User")


class TradeInReturnItem(Base):
    """Barang yang dikembalikan oleh pelanggan dalam transaksi tukar tambah"""
    __tablename__ = "trade_in_return_items"
    id = Column(Integer, primary_key=True, index=True)
    trade_in_id = Column(Integer, ForeignKey("trade_ins.id"), nullable=False)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False)
    qty = Column(Float, nullable=False)
    returned_qty = Column(Float, default=0) # Jumlah yang sudah diretur ke supplier
    return_price = Column(Float, nullable=False)  # harga yang diterima toko dari pelanggan
    condition = Column(String(50), default="good")  # good | damaged | partial
    total = Column(Float, nullable=False)

    trade_in = relationship("TradeIn", back_populates="return_items")
    item = relationship("Item")


class TradeInNewItem(Base):
    """Barang baru yang diambil pelanggan dalam transaksi tukar tambah"""
    __tablename__ = "trade_in_new_items"
    id = Column(Integer, primary_key=True, index=True)
    trade_in_id = Column(Integer, ForeignKey("trade_ins.id"), nullable=False)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False)
    qty = Column(Float, nullable=False)
    sell_price = Column(Float, nullable=False)
    total = Column(Float, nullable=False)

    trade_in = relationship("TradeIn", back_populates="new_items")
    item = relationship("Item")

# ─── Building Materials AI ────────────────────────────────────────────────────

class BuildingProject(Base):
    """Project tracking pelanggan toko bangunan"""
    __tablename__ = "building_projects"
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    name = Column(String(200), nullable=False)
    location = Column(Text)
    type = Column(String(50))        # rumah_tinggal | ruko | gedung | renovasi | lainnya
    status = Column(String(20), default="active")   # active | completed | paused
    started_at = Column(Date, nullable=True)
    estimated_completion = Column(Date, nullable=True)
    budget_estimate = Column(Float, default=0)
    total_spent = Column(Float, default=0)
    notes = Column(Text)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    customer = relationship("Customer")
    creator = relationship("User")
    phases = relationship("BuildingProjectPhase", back_populates="project",
                          cascade="all, delete-orphan")


class BuildingProjectPhase(Base):
    """Fase/tahap pembangunan dalam satu project"""
    __tablename__ = "building_project_phases"
    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("building_projects.id"), nullable=False)
    name = Column(String(100), nullable=False)   # pondasi | struktur | dinding | atap | finishing
    sequence = Column(Integer, default=0)
    status = Column(String(20), default="pending")  # pending | active | done
    started_at = Column(Date, nullable=True)
    completed_at = Column(Date, nullable=True)
    budget = Column(Float, default=0)
    notes = Column(Text)

    project = relationship("BuildingProject", back_populates="phases")


class AIInsight(Base):
    """Simpan AI insights / rekomendasi yang sudah digenerate"""
    __tablename__ = "ai_insights"
    id = Column(Integer, primary_key=True, index=True)
    type = Column(String(50), nullable=False)
    # material_estimate | daily_brief | restock_alert | pricing_alert | project_reminder
    content = Column(Text, nullable=False)
    data_snapshot = Column(Text)   # JSON context yang dipakai
    sent_telegram = Column(Boolean, default=False)
    telegram_sent_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class MaterialEstimate(Base):
    """Simpan histori estimasi material yang pernah dihitung"""
    __tablename__ = "material_estimates"
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    project_id = Column(Integer, ForeignKey("building_projects.id"), nullable=True)
    type = Column(String(50))       # keramik | cat | pasangan_bata | plesteran | dll
    input_data = Column(Text)       # JSON: dimensi, spesifikasi
    result = Column(Text)           # JSON: list material + qty + estimasi harga
    ai_narration = Column(Text)     # Narasi AI
    converted_to_sale = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    customer = relationship("Customer")



## ─── Print Job Queue ───────────────────────────────────────────────────────────
from datetime import datetime
import pytz
from sqlalchemy import Column, Integer, String, Text, DateTime

def get_local_datetime():
    WITA = pytz.timezone("Asia/Makassar")
    return datetime.now(WITA)

class PrintJob(Base):
    __tablename__ = "print_jobs"

    id = Column(Integer, primary_key=True, index=True)
    branch_id = Column(Integer)
    content = Column(Text)
    status = Column(String, default="pending")  
    content_type = Column(String, default="raw")
    
    # 👇 PERBAIKAN: Hilangkan tanda () di get_local_datetime
    created_at = Column(DateTime, default=get_local_datetime)
