async function loadMerek() {
  try {
    const brands = await api("GET", "/items/brands");
    const tbl = document.getElementById("tblMerek");
    if (!tbl) return;
    tbl.innerHTML =
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
  } catch (e) {
    console.error("Gagal memuat merek:", e);
  }
}

function openMerekModal(item = null) {
  editMerekId = null;
  const title = document.getElementById("mMerekTitle");
  const nama = document.getElementById("merekNama");
  const desk = document.getElementById("merekDesk");

  if (title) title.textContent = "Tambah Merek";
  if (nama) nama.value = "";
  if (desk) desk.value = "";

  if (item) {
    editMerekId = item.id;
    if (title) title.textContent = "Edit Merek";
    if (nama) nama.value = item.name;
    if (desk) desk.value = item.description || "";
  }
  openModal("mMerek");

  // Fokus eksplisit ke Nama Merek. Jalankan lagi sesudah event klik/layout selesai
  // karena WebView dapat mengembalikan fokus ke tombol "+" setelah handler onclick.
  const focusNama = () => {
    if (
      nama &&
      document.getElementById("mMerek")?.style.display === "flex"
    ) {
      nama.focus({ preventScroll: true });
    }
  };
  focusNama();
  requestAnimationFrame(focusNama);
  setTimeout(focusNama, 0);
}

function editMerek(b) {
  openMerekModal(b);
}

function closeMerekModal() {
  const returnToItemModal = fromItemModal;
  closeModal("mMerek");

  // Jika dialog merek dibuka dari form barang, tampilkan kembali form yang sama.
  // Form tidak diinisialisasi ulang agar seluruh input barang tetap utuh.
  if (returnToItemModal) {
    fromItemModal = false;
    openModal("mBarang");
  }
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
    invalidateCache("/items/brands");
    await refreshSelects();
    if (fromItemModal) {
      document.getElementById("fMerek").value = saved.id;
      closeMerekModal();
    } else {
      closeModal("mMerek");
    }
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
    invalidateCache("/items/brands");
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
