with open('frontend/purchases.html', encoding='utf-8') as f:
    content = f.read()

content = content.replace('let msg = "Deteksi perubahan harga beli pada barang berikut:\n";', r'let msg = "Deteksi perubahan harga beli pada barang berikut:\n";')
content = content.replace('priceChanges.forEach((c) => { msg += `- ${c.name}: ${fmtRp(c.old)} ➜ ${fmtRp(c.new)}\n`; });', r'priceChanges.forEach((c) => { msg += `- ${c.name}: ${fmtRp(c.old)} ➜ ${fmtRp(c.new)}\n`; });')
content = content.replace('msg += "\nSimpan dengan harga baru ini?";', r'msg += "\nSimpan dengan harga baru ini?";')
content = content.replace('if (await showConfirm("Ada barang yang kurang dari pesanan (Qty Diterima < Qty Dipesan).\n\nApakah Anda ingin membuat DRAFT PESANAN BARU otomatis untuk sisa barang yang kurang?")) {', r'if (await showConfirm("Ada barang yang kurang dari pesanan (Qty Diterima < Qty Dipesan).\n\nApakah Anda ingin membuat DRAFT PESANAN BARU otomatis untuk sisa barang yang kurang?")) {')

with open('frontend/purchases.html', 'w', encoding='utf-8') as f:
    f.write(content)

import re
with open('frontend/purchases.html', encoding='utf-8') as f:
    content = f.read()
match = re.search(r'<script>(.*?)</script>', content, re.DOTALL)
if match:
    with open('temp3.js', 'w', encoding='utf-8') as f:
        f.write(match.group(1))
