// Motyw i rozmiar tekstu przed pierwszym malowaniem (bez mignięcia jasnego tła).
// Osobny plik, nie skrypt w <head> — polityka CSP strony nie dopuszcza skryptów inline.
try {
  var t = localStorage.getItem("usbot.theme");
  if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
  var f = Number(localStorage.getItem("usbot.fontScale"));
  if (f >= 0.875 && f <= 1.5) document.documentElement.style.fontSize = f * 100 + "%";
} catch (e) {}
