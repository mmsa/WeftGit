const compactNavigation = window.matchMedia("(max-width: 650px)");
function setNavigationSize() {
  document.querySelectorAll(".nav-sections").forEach(section => {
    section.open = !compactNavigation.matches;
  });
}
setNavigationSize();
compactNavigation.addEventListener("change", setNavigationSize);
