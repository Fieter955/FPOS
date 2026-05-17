from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import date, datetime

# ─── Auth ─────────────────────────────────────────────────────────────────────
class Token(BaseModel):
    access_token: str
    token_type: str
    user: dict

class RoleCreate(BaseModel):
    name: str

class RoleOut(RoleCreate):
    id: int
    model_config = {"from_attributes": True}

class UserCreate(BaseModel):
    username: str
    email: Optional[str] = None
    full_name: Optional[str] = None
    password: str
    role: str = "kasir"
    branch_id: Optional[int] = None

class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    role: Optional[str] = None
    branch_id: Optional[int] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None

class UserOut(BaseModel):
    id: int
    username: str
    email: Optional[str]
    full_name: Optional[str]
    role: str
    branch_id: Optional[int]
    active_branch_id: Optional[int] = None
    branch_status: Optional[str] = "Cabang"
    is_active: bool
    model_config = {"from_attributes": True}


# ─── Category ─────────────────────────────────────────────────────────────────
class CategoryCreate(BaseModel):
    name: str
    description: Optional[str] = None

class CategoryOut(CategoryCreate):
    id: int
    model_config = {"from_attributes": True}


# ─── Unit ─────────────────────────────────────────────────────────────────────
class UnitCreate(BaseModel):
    name: str
    abbreviation: Optional[str] = None

class UnitOut(UnitCreate):
    id: int
    model_config = {"from_attributes": True}

# ─── Branch / Cabang ──────────────────────────────────────────────────────────
class BranchCreate(BaseModel):
    code: str
    name: str
    address: str
    phone: Optional[str] = None
    status: Optional[str] = "Cabang"

class BranchUpdate(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    status: Optional[str] = None
    is_active: Optional[bool] = None

class BranchOut(BaseModel):
    id: int
    code: str
    name: str
    address: Optional[str]
    phone: Optional[str]
    status: str
    is_active: bool
    model_config = {"from_attributes": True}


# ─── ItemPrice ────────────────────────────────────────────────────────────────
class ItemPriceCreate(BaseModel):
    name: str
    price: float
    min_qty: float = 1.0

class ItemPriceOut(ItemPriceCreate):
    id: int
    model_config = {"from_attributes": True}

class SupplierSimpleOut(BaseModel):
    id: int
    name: str
    code: Optional[str] = None
    model_config = {"from_attributes": True}

# ─── Item Supplier Settings ────────────────────────────────────────────────────
class ItemSupplierCreate(BaseModel):
    supplier_id: int
    buy_price: float = 0
    barcode: Optional[str] = None

class ItemSupplierOut(ItemSupplierCreate):
    model_config = {"from_attributes": True}

# ─── Item ─────────────────────────────────────────────────────────────────────
class ItemCreate(BaseModel):
    code: str
    name: str
    category_id: Optional[int] = None
    unit_id: Optional[int] = None
    buy_price: float = 0
    sell_price: float = 0
    profit_margin: float = 0
    stock: float = 0
    min_stock: float = 0
    description: Optional[str] = None
    barcode: Optional[str] = None
    is_discountable: bool = False
    supplier_ids: Optional[List[int]] = None
    supplier_settings: Optional[List[ItemSupplierCreate]] = []
    prices: Optional[List[ItemPriceCreate]] = []

class ItemUpdate(BaseModel):
    name: Optional[str] = None
    category_id: Optional[int] = None
    unit_id: Optional[int] = None
    buy_price: Optional[float] = None
    sell_price: Optional[float] = None
    profit_margin: Optional[float] = None
    min_stock: Optional[float] = None
    description: Optional[str] = None
    barcode: Optional[str] = None
    is_active: Optional[bool] = None
    is_discountable: Optional[bool] = None
    supplier_ids: Optional[List[int]] = None
    supplier_settings: Optional[List[ItemSupplierCreate]] = None
    prices: Optional[List[ItemPriceCreate]] = None

class ItemOut(BaseModel):
    id: int
    code: str
    name: str
    category_id: Optional[int]
    unit_id: Optional[int]
    parent_item_id: Optional[int] = None
    conversion_factor_to_parent: float = 1
    is_virtual_variant: bool = False
    buy_price: float
    sell_price: float
    profit_margin: float
    stock: float
    min_stock: float
    description: Optional[str]
    barcode: Optional[str]
    is_discountable: bool
    is_active: bool
    category: Optional[CategoryOut] = None
    unit: Optional[UnitOut] = None
    prices: List[ItemPriceOut] = []
    suppliers: List[SupplierSimpleOut] = []
    supplier_details: List[ItemSupplierOut] = []
    model_config = {"from_attributes": True}


# ─── CustomerGroup ────────────────────────────────────────────────────────────
class CustomerGroupCreate(BaseModel):
    name: str
    discount_percent: float = 0

class CustomerGroupOut(CustomerGroupCreate):
    id: int
    model_config = {"from_attributes": True}


# ─── Customer ─────────────────────────────────────────────────────────────────
class CustomerCreate(BaseModel):
    code: Optional[str] = None
    name: str
    address: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    group_id: Optional[int] = None
    credit_limit: float = 0

class CustomerUpdate(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    group_id: Optional[int] = None
    credit_limit: Optional[float] = None
    is_active: Optional[bool] = None

class CustomerOut(BaseModel):
    id: int
    code: str
    name: str
    address: Optional[str]
    phone: Optional[str]
    email: Optional[str]
    group_id: Optional[int]
    points: float
    credit_limit: float
    is_active: bool
    group: Optional[CustomerGroupOut] = None
    model_config = {"from_attributes": True}


# ── Supplier ─────────────────────────────────────────────────────────────────
class SupplierCreate(BaseModel):
    code: Optional[str] = None  # 👈 DIUBAH: Tidak wajib diisi user
    name: str
    address: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    contact_person: Optional[str] = None
    credit_limit: float = 0
    item_ids: Optional[List[int]] = None
    model_config = {"from_attributes": True}

class SupplierUpdate(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    contact_person: Optional[str] = None
    credit_limit: Optional[float] = None
    is_active: Optional[bool] = None
    item_ids: Optional[List[int]] = None
    model_config = {"from_attributes": True}

class SupplierOut(BaseModel):
    id: int
    code: str
    name: str
    address: Optional[str]
    phone: Optional[str]
    email: Optional[str]
    contact_person: Optional[str]
    credit_limit: float
    is_active: bool
    items: List[ItemOut] = []
    model_config = {"from_attributes": True}

# ─── SalesPerson ──────────────────────────────────────────────────────────────
class SalesPersonCreate(BaseModel):
    code: str
    name: str
    commission_percent: float = 0
    phone: Optional[str] = None

class SalesPersonOut(SalesPersonCreate):
    id: int
    is_active: bool
    model_config = {"from_attributes": True}


# ─── Purchase ─────────────────────────────────────────────────────────────────
class PurchaseItemCreate(BaseModel):
    item_id: int
    qty: float
    qty_ordered: float = 0
    qty_received: float = 0
    buy_price: float
    disc1: float = 0
    disc2: float = 0
    sell_price: float = 0
    profit_margin: float = 0
    discount: float = 0

class PurchaseCreate(BaseModel):
    number: Optional[str] = None
    date: date
    supplier_id: Optional[int] = None
    discount: float = 0
    tax: float = 0
    notes: Optional[str] = None
    items: List[PurchaseItemCreate]
    paid: float = 0
    status: Optional[str] = None
    is_branch_request: Optional[bool] = False
    target_branch_id: Optional[int] = None
    from_po_id: Optional[int] = None

class PurchaseItemOut(BaseModel):
    id: int
    item_id: int
    qty: float
    qty_ordered: float = 0
    qty_received: float = 0
    buy_price: float
    discount: float
    disc1: float = 0
    disc2: float = 0
    total: float
    item: Optional[ItemOut] = None
    model_config = {"from_attributes": True}

class PurchaseOut(BaseModel):
    id: int
    number: str
    date: date
    branch_id: Optional[int] = None
    supplier_id: Optional[int] = None
    from_po_id: Optional[int] = None
    subtotal: float
    discount: float
    tax: float
    total: float
    paid: float
    status: str
    notes: Optional[str]
    is_branch_request: Optional[bool] = False
    target_branch_id: Optional[int] = None
    is_received_by_branch: bool = False
    supplier: Optional[SupplierOut] = None
    branch: Optional[BranchOut] = None
    target_branch: Optional[BranchOut] = None
    items: List[PurchaseItemOut] = []
    fulfillment_drafts: List["PurchaseOut"] = []
    model_config = {"from_attributes": True}

class PurchasePayment(BaseModel):
    amount: float = 0
    cash_amount: float = 0
    bank_amount: float = 0
    notes: Optional[str] = None

class SplitFulfillItem(BaseModel):
    item_id: int
    qty: float
    supplier_id: int
    buy_price: float = 0

class SplitFulfillRequest(BaseModel):
    items: List[SplitFulfillItem]
    notes: Optional[str] = None

class DraftReceiveItem(BaseModel):
    purchase_item_id: int
    qty_received: float
    buy_price: float
    new_item_name: Optional[str] = None

class DraftReceiveRequest(BaseModel):
    items: List[DraftReceiveItem]
    notes: Optional[str] = None


# ─── Sale ─────────────────────────────────────────────────────────────────────
class SaleItemCreate(BaseModel):
    item_id: int
    qty: float
    sell_price: float
    discount: float = 0

class SaleCreate(BaseModel):
    number: Optional[str] = None
    date: date
    customer_id: Optional[int] = None
    salesperson_id: Optional[int] = None
    discount: float = 0
    tax: float = 0
    paid: float = 0
    payment_method: str = "cash"
    notes: Optional[str] = None
    items: List[SaleItemCreate]

class SaleItemOut(BaseModel):
    id: int
    item_id: int
    qty: float
    buy_price: float = 0
    sell_price: float
    discount: float
    total: float
    margin_amount: float = 0
    margin_percent: float = 0
    item: Optional[ItemOut] = None
    model_config = {"from_attributes": True}

class SaleOut(BaseModel):
    id: int
    number: str
    date: date
    customer_id: Optional[int]
    subtotal: float
    discount: float
    tax: float
    total: float
    paid: float
    change: float
    payment_method: str
    status: str
    notes: Optional[str]
    customer: Optional[CustomerOut] = None
    items: List[SaleItemOut] = []
    model_config = {"from_attributes": True}


# ─── Branch Deposit ──────────────────────────────────────────────────────────
class BranchDepositCreate(BaseModel):
    amount: float # Total
    cash_amount: float = 0
    bank_amount: float = 0
    bank_account_id: Optional[int] = None
    notes: Optional[str] = None

class BranchDepositOut(BaseModel):
    id: int
    branch_id: int
    date: date
    amount: float
    cash_amount: float
    bank_amount: float
    bank_account_id: Optional[int]
    journal_id: Optional[int]
    notes: Optional[str]
    created_at: datetime
    model_config = {"from_attributes": True}


# ─── Stock Opname ─────────────────────────────────────────────────────────────
class AdjustmentCreate(BaseModel):
    item_id: int
    type: str          # 'in', 'out', 'adjust'
    qty: float
    description: str
    opname_mode: str = "running"  # 'opening' (setup awal) | 'running' (opname berjalan)


# ─── Cash Transaction ─────────────────────────────────────────────────────────
class CashTransactionCreate(BaseModel):
    number: Optional[str] = None
    date: date
    type: str
    amount: float
    description: Optional[str] = None
    reference: Optional[str] = None
    account_id: int
    

class CashTransactionOut(CashTransactionCreate):
    id: int
    model_config = {"from_attributes": True}



# ─── Dashboard Stats ──────────────────────────────────────────────────────────
class DashboardStats(BaseModel):
    total_sales_today: float
    total_purchases_today: float
    total_transactions_today: int
    low_stock_count: int
    top_items: list
    recent_sales: list


# ─── Accounting (Jurnal & COA) ────────────────────────────────────────────────
class AccountCreate(BaseModel):
    code: str
    name: str
    type: str

class AccountOut(BaseModel):
    id: int
    code: str
    name: str
    type: str
    is_active: bool
    model_config = {"from_attributes": True}

class JournalEntryLineOut(BaseModel):
    id: int
    account_id: int
    debit: float
    credit: float
    account: Optional[AccountOut] = None
    model_config = {"from_attributes": True}

class JournalOut(BaseModel):
    id: int
    number: str
    date: date
    description: str
    reference: Optional[str] = None
    # 👇👇 INI ADALAH KUNCI JAWABANNYA 👇👇
    entries: List[JournalEntryLineOut] = [] 
    model_config = {"from_attributes": True}

PurchaseOut.model_rebuild()
