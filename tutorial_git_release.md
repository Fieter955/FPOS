stay di root utama



di backend/config.py rubah app versin menjadi versi terupdate misal 
APP_VERSION: str = "5.0.7"



build frontend nya karna ini pake next js agar perubahannya diterapkan

npm run build


jalankan script build untuk release bernama build-release.ps1. NOTE: semisal lama itu wajar, biarin dulu aja terminalnya

powershell -ExecutionPolicy Bypass -File .\build-release.ps1 `
  -Version 5.0.8 `
  -DownloadUrl "https://github.com/Fieter955/FPOS/releases/download/v5.0.8/FPOS-5.0.8-Windows.zip" `
  -Notes "Perbaikan fitur dan peningkatan stabilitas" `
  -CondaEnv base



  commit dan push ke branch yang benar (branch yang memang dibaca oleh aplikasi sebagai acuan release)

  git add backend/app/config.py version.json frontend-dist/
git commit -m "chore(release): bump version to v5.0.7"
git push origin local-main-2026-08-20



buat git tag untuk entar dijadiin release

git tag -a v5.0.7 -m "Release FPOS v5.0.7"
git push origin v5.0.7


setelah nya tinggal ke github lalu pastikan buka branch yang sesuai dan piilh tag yang udh ada sebgai release update, BARU Upload file ZIP yang sebelumnya udh terbuat di langkah atas




