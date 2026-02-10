import { ref, watch, onUnmounted, getCurrentInstance } from "vue";

const STORAGE_KEY = "dark-mode";

// Sync initial ref with what the inline script in index.html already applied,
// so there is no flash of unstyled content (FOUC).
function getInitialDark(): boolean {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored !== null) return stored === "true";
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

const isDark = ref(getInitialDark());

function applyTheme(dark: boolean) {
  document.documentElement.classList.toggle("dark-mode", dark);
}

export function useDarkMode() {
  watch(isDark, (val) => {
    localStorage.setItem(STORAGE_KEY, String(val));
    applyTheme(val);
  }, { immediate: true });

  // Follow OS preference changes when user hasn't explicitly set a preference
  const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
  function onSystemChange(e: MediaQueryListEvent) {
    // Only follow system if user hasn't stored a preference
    if (localStorage.getItem(STORAGE_KEY) === null) {
      isDark.value = e.matches;
    }
  }
  mediaQuery.addEventListener("change", onSystemChange);

  if (getCurrentInstance()) {
    onUnmounted(() => {
      mediaQuery.removeEventListener("change", onSystemChange);
    });
  }

  function toggle() {
    isDark.value = !isDark.value;
  }

  return { isDark, toggle };
}
