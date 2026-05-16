# FPOS Project Instructions & Standards

Foundational mandates for Gemini CLI interaction within this workspace.

## 🏗️ Architectural Overview

- **Frontend**: Vanilla JavaScript + HTML5.
- **Styling**: Vanilla CSS using CSS Variables (defined in `css/style.css`).
- **Backend**: FastAPI (Python) with SQLAlchemy ORM.
- **Database**: SQLite (`ipos.db`).

## 🧩 Shared UI Components (Mandatory)

Always reuse existing components instead of re-implementing them. Files: `frontend/js/components.js`, `frontend/css/components.css`.

### 1. Searchable Dropdowns (Combobox)

- **Function**: `createPremiumCombo(container, data, config)`.
- **Rule**: Mandatory for all selection tasks. Features fixed positioning and real-time filtering.
- **Features**: Creates a premium searchable dropdown with fixed positioning and high performance.

### 2. Unified Purchase Grid

- **Function**: `createPurchaseGrid(container, config)`.
- **Modes**:
  - `isFulfillment: false` (PO/Order): 1 column Qty (Pesan).
  - `isFulfillment: true` (Purchase): 2 columns Qty (Pesan vs Terima).
- **Naming Standard**: Always use `qty_ordered` and `qty_received` to match Pydantic schemas.
- **Visual Standards**:b
  - Qty & Margin: Compact width (60px).
  - Mismatch Feedback: `qty_received` input changes border/bg to orange if it differs from `qty_ordered`.
- **Layout Order**: **[Barang] [Pesan] [Terima*] [Harga Beli] [Margin %] [Harga Jual] [Diskon %] [Total]**. -**Features**: Membuat tabel pembelian yang bisa menyesuaikan apakah perlu kolom barang diterima apa tidak tergantung mode yang dipakai

### 3. Unified Transaction Manager (OrderManager)

- **Function**: `createOrderManager(containerId, config)`.
- **Scope**: Mandatory for all "Catat Pembelian" and "Catat Penjualan" workflows.
- **Features**: Automatically manages Supplier/Customer selection, Date inputs, Reference numbers, the unified Item Grid, and the Grand Total summary box.
- **Calculation Rule**: In `purchase` mode, Total is ALWAYS calculated based on `qty_received`.

### 4. Filter Bar

- **Function**: `createFilterBar(container, config)`.
- **Features**: Includes Date Range, Status Select, and a Premium Searchable Entity dropdown.

### 5. Payment Modal

- **Function**: `createPaymentModal(config)`.
- **Features**: Handles split payments (Cash/Bank), saldo verification, and automated API submission.

## 🎨 Design System

- **Colors**: Use variables like `--primary`, `--bg-color`, `--card-bg`, etc.
- **Spacing**: Maintain consistent padding (12px-24px) and border-radius (8px-12px).

## 🚀 Workflow Standards

- **Modularization**: Move reusable logic to `components.js` segera jika dipakai di 2+ tempat.
- **Modal CRUD Implementation**:
  - Every modal-based form (e.g., CoA, Kas, Items) MUST have three explicit functions: `open[Name]Modal()` (to reset fields), `edit[Name](data)` (to populate fields), and `save[Name]()` (to handle POST/PUT).
  - Ensure `edit` functions properly handle ID state (e.g., `editCoaId = a.id`).
- **Draft Editing (Admin Only)**:
  - Only `role: admin` can process/edit draft invoices in `catat-pembelian.html`.
  - **Supplier Locking**: Supplier MUST be locked (`.disable()`) when editing an existing draft to maintain data integrity.
  - **Change Tracking**: System MUST compare current items against original draft items and show a `showConfirm` warning if items are added, removed, or quantities are modified.
  - **Finalization**: Processing a draft MUST result in a status transition (e.g., to `unpaid` for AP).
- **Discrepancy Handling**: Purchase fulfillment MUST validate mismatches. If `qty_received < qty_ordered`, offer the "Reorder Missing" option to create a new draft for the balance.

## 🏢 Multi-Branch Standards

- **Branch Address**: Mandatory. Every branch MUST have a full address.
- **Auto-Generation**: Branch codes are auto-generated as `CBG-XXXX`.
- **Warehouse Linking**: Every branch has a default warehouse `WH-CBG-XXXX`.

### 📦 Inter-Branch PO Workflow (Mandatory Logic)

- **Request Routing**: Non-Main branches route POs to Main Store (ID 1) with `is_branch_request=true` and `status='pending'`.
- **Accounting (Reciprocal Transfer)**:
  - **Main Store (Branch 1)**: Debit `3-2200 Kirim Barang ke Cabang`, Credit `1-1100 Kas` / `2-1100 Hutang`.
  - **Requesting Branch**: Debit `1-1400 Persediaan`, Credit `3-2100 Transfer dari Pusat`.
  - **Implementation**: Both journals MUST be created simultaneously in `journal_service.py` during fulfillment.
- **Stock Isolation**:
  - **Pusat Stock**: `item.stock` MUST NOT increase when Pusat fulfills a request for another branch.
  - **Branch Stock**: Increments MUST be directed to the target branch's warehouse (`target_branch_id`).
- **Data Integrity**:
  - **Draft Preservation**: `target_branch_id` MUST be preserved when updating/finalizing a draft PO fulfillment, even if the frontend payload is incomplete.
  - **Visibility Filter**: Fulfillment purchases (where `branch_id == 1` and `target_branch_id > 1`) MUST be excluded from the branch's main purchase list to prevent duplicate views, accessible only via `po.html`.
- **Audit**: `StockMovement` records must use `get_total_branch_stock` for accurate branch-level snapshots.

## 🧠 AI Optimization & Intelligence

### 1. Schema Consistency

- **Standard**: Field names MUST match between Pydantic `schemas.py` and Frontend logic (e.g., `qty_received`, not `qty_diterima`).
- **Field Coverage**: Pydantic `Update` schemas (e.g., `AccountUpdate`) MUST include all fields that are editable in the frontend UI (e.g., `code`, `type`) to prevent silent save failures.

### 2. State Awareness

- **Active Branch**: Filter data using `current_user.active_branch_id`.

### 3. Documentation Hooks

- Use `GEMINI.md` for mandatory architectural patterns and shared UI components.
- When finishing a task, recap the "Why" and "How" in the topic summary.
