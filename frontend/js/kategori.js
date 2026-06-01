async function loadKat() {
  const cats = await api("GET", "/items/categories");
  document.getElementById("tblKat").innerHTML =
    cats
      .map(
        (c) => `<tr>
      <td><b>${c.name}</b></td><td style="color:var(--text-muted)">${
        c.description || "-"
      }</td>
      <td style="white-space:nowrap"><button class="bsm be" onclick='editKat(${JSON.stringify(
        c,
      ).replace(/"/g, "&quot;")})'>Edit</button> <button class="bsm bd" onclick="delKat(${
          c.id
        },'${c.name}')">Hapus</button></td>
    </tr>`,
      )
      .join("") ||
    '<tr><td colspan="3" style="text-align:center;padding:20px;color:var(--text-muted)">Belum ada kategori</td></tr>';
}

function openKatModal(item = null) {
  editKatId = null;
  document.getElementById("mKatTitle").textContent = "Tambah Kategori";
  document.getElementById("kNama").value = "";
  document.getElementById("kDesk").value = "";
  if (item) {
    editKatId = item.id;
    document.getElementById("mKatTitle").textContent = "Edit Kategori";
    document.getElementById("kNama").value = item.name;
    document.getElementById("kDesk").value = item.description || "";
  }
  openModal("mKat");
}

function editKat(c) {
  openKatModal(c);
}

async function saveKat() {
  const name = document.getElementById("kNama").value.trim();
  if (!name) {
    showToast("Nama wajib diisi", "error");
    return;
  }
  try {
    let saved;
    if (editKatId)
      saved = await api("PUT", `/items/categories/${editKatId}`, {
        name,
        description: document.getElementById("kDesk").value || null,
      });
    else
      saved = await api("POST", "/items/categories", {
        name,
        description: document.getElementById("kDesk").value || null,
      });
    showToast("Kategori disimpan ✓");
    await refreshSelects();
    if (fromItemModal) {
      document.getElementById("fKat").value = saved.id;
      openModal("mBarang");
    }
    closeModal("mKat");
    fromItemModal = false;
    loadKat();
  } catch (ex) {
    showToast(ex.message, "error");
  }
}

async function delKat(id, name) {
  if (!(await showConfirm(`Hapus kategori "${name}"?`))) return;
  try {
    await api("DELETE", `/items/categories/${id}`);
    showToast("Dihapus");
    loadKat();
    refreshSelects();
  } catch (ex) {
    showToast(ex.message, "error");
  }
}

function quickAddKat() {
  fromItemModal = true;
  closeModal("mBarang");
  openKatModal();
}
