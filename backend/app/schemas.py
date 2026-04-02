from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import date, datetime


# ─── Auth ─────────────────────────────────────────────────────────────────────
class Token(BaseModel):
    access_token: str
    token_type: str
    user: dict

class UserCreate(BaseModel):
    username: str
    email: Optional[str] = None
    full_name: Optional[str] = None
    password: str
    role: str = "kasir"

class UserOut(BaseModel):
    id: int
    username: str
    email: Optional[str]
    full_name: Optional[str]
    role: str
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


# ─── ItemPrice ────────────────────────────────────────────────────────────────
class ItemPriceCreate(BaseModel):
    name: str
    price: float
    min_qty: float = 1.0

class ItemPriceOut(ItemPriceCreate):
    id: int
    model_config = {"from_attributes": True}


# ─── Item ─────────────────────────────────────────────────────────────────────
class ItemCreate(BaseModel):
    code: str
    name: str
    category_id: Optional[int] = None
    unit_id: Optional[int] = None
    buy_price: float = 0
    sell_price: float = 0
    stock: float = 0
    min_stock: float = 0
    description: Optional[str] = None
    barcode: Optional[str] = None
    prices: Optional[List[ItemPriceCreate]] = []

class ItemUpdate(BaseModel):
    name: Optional[str] = None
    category_id: Optional[int] = None
    unit_id: Optional[int] = None
    buy_price: Optional[float] = None
    sell_price: Optional[float] = None
    min_stock: Optional[float] = None
    description: Optional[str] = None
    barcode: Optional[str] = None
    is_active: Optional[bool] = None
    prices: Optional[List[ItemPriceCreate]] = None

class ItemOut(BaseModel):
    id: int
    code: str
    name: str
    category_id: Optional[int]
    unit_id: Optional[int]
    buy_price: float
    sell_price: float
    stock: float
    min_stock: float
    description: Optional[str]
    barcode: Optional[str]
    is_active: bool
    category: Optional[CategoryOut] = None
    unit: Optional[UnitOut] = None
    prices: List[ItemPriceOut] = []
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
    code: str
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


# ─── Supplier ─────────────────────────────────────────────────────────────────
class SupplierCreate(BaseModel):
    code: str
    name: str
    address: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    contact_person: Optional[str] = None
    credit_limit: float = 0

class SupplierUpdate(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    contact_person: Optional[str] = None
    credit_limit: Optional[float] = None
    is_active: Optional[bool] = None

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
    buy_price: float
    discount: float = 0

class PurchaseCreate(BaseModel):
    number: Optional[str] = None
    date: date
    supplier_id: int
    discount: float = 0
    tax: float = 0
    notes: Optional[str] = None
    items: List[PurchaseItemCreate]

class PurchaseItemOut(BaseModel):
    id: int
    item_id: int
    qty: float
    buy_price: float
    discount: float
    total: float
    item: Optional[ItemOut] = None
    model_config = {"from_attributes": True}

class PurchaseOut(BaseModel):
    id: int
    number: str
    date: date
    supplier_id: int
    subtotal: float
    discount: float
    tax: float
    total: float
    paid: float
    status: str
    notes: Optional[str]
    supplier: Optional[SupplierOut] = None
    items: List[PurchaseItemOut] = []
    model_config = {"from_attributes": True}

class PurchasePayment(BaseModel):
    amount: float
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
    sell_price: float
    discount: float
    total: float
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


# ─── Stock Opname ─────────────────────────────────────────────────────────────
class StockOpnameItemCreate(BaseModel):
    item_id: int
    actual_qty: float

class StockOpnameCreate(BaseModel):
    number: Optional[str] = None
    date: date
    notes: Optional[str] = None
    items: List[StockOpnameItemCreate]

class StockOpnameItemOut(BaseModel):
    id: int
    item_id: int
    system_qty: float
    actual_qty: float
    difference: float
    model_config = {"from_attributes": True}

class StockOpnameOut(BaseModel):
    id: int
    number: str
    date: date
    status: str
    notes: Optional[str]
    items: List[StockOpnameItemOut] = []
    model_config = {"from_attributes": True}


# ─── Cash Transaction ─────────────────────────────────────────────────────────
class CashTransactionCreate(BaseModel):
    number: Optional[str] = None
    date: date
    type: str
    amount: float
    description: Optional[str] = None
    reference: Optional[str] = None

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
