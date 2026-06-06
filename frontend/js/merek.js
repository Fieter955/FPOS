async function loadMerek() {
  const brands = await api("GET", "/items/brands");
  document.getElementById("tblMerek").innerHTML =
    brands
      .map(
        (b) => `<tr>
      <td><b>${b.name}</b></td><td style="color:var(--text-muted)">${
        b.description || "-"
      }</td>
      <td style="white-space:nowrap"><button class="bsm be" onclick='editMerek(${JSON.stringify(
        b,
      ).replace(/"/g, "&quot;")})'>Edit</button> <button class="bsm bd" onclick="delMerek(${
          b.id
        },'${b.name}')">Hapus</button></td>
    </tr>`,
      )
      .join("") ||
    '<tr><td colspan="3" style="text-align:center;padding:20px;color:var(--text-muted)">Belum ada merek</td></tr>';
}

function openMerekModal(item = null) {
  editMerekId = null;
  document.getElementById("mMerekTitle").textContent = "Tambah Merek";
  document.getElementById("merekNama").value = "";
  document.getElementById("merekDesk").value = "";
  if (item) {
    editMerekId = item.id;
    document.getElementById("mMerekTitle").textContent = "Edit Merek";
    document.getElementById("merekNama").value = item.name;
    document.getElementById("merekDesk").value = item.description || "";
  }
  openModal("mMerek");
}

function editMerek(b) {
  openMerekModal(b);
}

async function saveMerek() {
  const name = document.getElementById("merekNama").value.trim();
  if (!name) {
    showToast("Nama wajib diisi", "error");
    return;
  }
  try {
    let saved;
    if (editMerekId)
      saved = await api("PUT", `/items/brands/${editMerekId}`, {
        name,
        description: document.getElementById("merekDesk").value || null,
      });
    else
      saved = await api("POST", "/items/brands", {
        name,
        description: document.getElementById("merekDesk").value || null,
      });
    showToast("Merek disimpan ✓");
    await refreshSelects();
    if (fromItemModal) {
      document.getElementById("fMerek").value = saved.id;
      openModal("mBarang");
    }
    closeModal("mMerek");
    fromItemModal = false;
    loadMerek();
  } catch (ex) {
    showToast(ex.message, "error");
  }
}

async function delMerek(id, name) {
  if (!(await showConfirm(`Hapus merek "${name}"?`))) return;
  try {
    await api("DELETE", `/items/brands/${id}`);
    showToast("Dihapus");
    loadMerek();
    refreshSelects();
  } catch (ex) {
    showToast(ex.message, "error");
  }
}

function quickAddMerek() {
  fromItemModal = true;
  closeModal("mBarang");
  openMerekModal();
}
