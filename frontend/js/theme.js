function initTheme() {
  const savedTheme = localStorage.getItem("ipos_theme");
  const isDark =
    savedTheme === "dark" ||
    (!savedTheme && window.matchMedia("(prefers-color-scheme: dark)").matches);
  if (isDark) document.body.classList.add("dark-mode");
  else document.body.classList.remove("dark-mode");
}
function toggleTheme() {
  const isDark = document.body.classList.toggle("dark-mode");
  localStorage.setItem("ipos_theme", isDark ? "dark" : "light");
}
document.addEventListener("DOMContentLoaded", initTheme);
