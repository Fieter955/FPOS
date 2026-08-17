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
  const nama = document.getElementById("sNama");
  editSatId = null;
  document.getElementById("mSatTitle").textContent = "Tambah Satuan";
  nama.value = "";
  document.getElementById("sAbr").value = "";
  if (item) {
    editSatId = item.id;
    document.getElementById("mSatTitle").textContent = "Edit Satuan";
    nama.value = item.name;
    document.getElementById("sAbr").value = item.abbreviation || "";
  }
  openModal("mSat");

  // Fokus eksplisit ke Nama Satuan. Jalankan lagi sesudah event klik/layout selesai
  // karena WebView dapat mengembalikan fokus ke tombol "+" setelah handler onclick.
  const focusNama = () => {
    if (document.getElementById("mSat")?.style.display === "flex") {
      nama.focus({ preventScroll: true });
    }
  };
  focusNama();
  requestAnimationFrame(focusNama);
  setTimeout(focusNama, 0);
}

function editSat(u) {
  openSatModal(u);
}

function closeSatModal() {
  const returnToItemModal = fromItemModal;
  closeModal("mSat");

  // Jika satuan dibuka dari form barang, tampilkan kembali form yang sama
  // tanpa menginisialisasi ulang atau menghapus input yang sudah diisi.
  if (returnToItemModal) {
    fromItemModal = false;
    openModal("mBarang");
  }
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
      // Tutup modal satuan lebih dulu agar modal barang menjadi modal terdepan.
      // Jika dibuka sebelum mSat ditutup, fokus modal akan hilang dan Enter
      // berikutnya kembali ke kontrol pertama (Nama Barang).
      closeModal("mSat");
      fromItemModal = false;
      openModal("mBarang");
      const satuanInput = document.getElementById("fSat");
      setTimeout(() => satuanInput?.focus({ preventScroll: true }), 0);
    } else {
      closeModal("mSat");
    }
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
