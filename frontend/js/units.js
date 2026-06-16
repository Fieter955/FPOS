async function loadSat() {
  const units = await api("GET", "/items/units");
  document.getElementById("tblSat").innerHTML =
    units
      .map(
        (u) => `<tr>
      <td><b>${u.name}</b></td><td style="color:var(--text-muted)">${
        u.abbreviation || "-"
      }</td>
      <td style="white-space:nowrap"><button class="bsm be" onclick='editSat(${JSON.stringify(
        u,
      ).replace(/"/g, "&quot;")})'>Edit</button> <button class="bsm bd" onclick="delSat(${
          u.id
        },'${u.name}')">Hapus</button></td>
    </tr>`,
      )
      .join("") ||
    '<tr><td colspan="3" style="text-align:center;padding:20px;color:var(--text-muted)">Belum ada satuan</td></tr>';
}

function openSatModal(item = null) {
  editSatId = null;
  document.getElementById("mSatTitle").textContent = "Tambah Satuan";
  document.getElementById("sNama").value = "";
  document.getElementById("sAbr").value = "";
  if (item) {
    editSatId = item.id;
    document.getElementById("mSatTitle").textContent = "Edit Satuan";
    document.getElementById("sNama").value = item.name;
    document.getElementById("sAbr").value = item.abbreviation || "";
  }
  openModal("mSat");
}

function editSat(u) {
  openSatModal(u);
}

async function saveSat() {
  const name = document.getElementById("sNama").value.trim();
  if (!name) {
    showToast("Nama wajib diisi", "error");
    return;
  }
  try {
    let saved;
    if (editSatId)
      saved = await api("PUT", `/items/units/${editSatId}`, {
        name,
        abbreviation: document.getElementById("sAbr").value || null,
      });
    else
      saved = await api("POST", "/items/units", {
        name,
        abbreviation: document.getElementById("sAbr").value || null,
      });
    showToast("Satuan disimpan ✓");
    invalidateCache("/items/units");
    await refreshSelects();
    if (fromItemModal) {
      document.getElementById("fSat").value = saved.id;
      openModal("mBarang");
    }
    closeModal("mSat");
    fromItemModal = false;
    loadSat();
  } catch (ex) {
    showToast(ex.message, "error");
  }
}

async function delSat(id, name) {
  if (!(await showConfirm(`Hapus satuan "${name}"?`))) return;
  try {
    await api("DELETE", `/items/units/${id}`);
    showToast("Dihapus");
    invalidateCache("/items/units");
    loadSat();
    refreshSelects();
  } catch (ex) {
    showToast(ex.message, "error");
  }
}

function quickAddSat() {
  fromItemModal = true;
  closeModal("mBarang");
  openSatModal();
}
