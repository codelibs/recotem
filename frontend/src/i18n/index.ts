import { createI18n } from "vue-i18n";
import en from "./locales/en.json";
import ja from "./locales/ja.json";

type Locale = "en" | "ja";
const SUPPORTED_LOCALES: Locale[] = ["en", "ja"];

function isLocale(val: string): val is Locale {
  return (SUPPORTED_LOCALES as string[]).includes(val);
}

function getPreferredLocale(): Locale {
  const stored = localStorage.getItem("locale");
  if (stored && isLocale(stored)) return stored;
  const browserLang = navigator.language.split("-")[0];
  return browserLang === "ja" ? "ja" : "en";
}

const i18n = createI18n({
  legacy: false,
  locale: getPreferredLocale(),
  fallbackLocale: "en",
  messages: { en, ja },
});

export function setLocale(locale: string) {
  const resolved: Locale = isLocale(locale) ? locale : "en";
  if (i18n.global.locale && typeof i18n.global.locale === "object") {
    i18n.global.locale.value = resolved;
  }
  localStorage.setItem("locale", resolved);
}

export function getLocale(): string {
  if (i18n.global.locale && typeof i18n.global.locale === "object") {
    return i18n.global.locale.value;
  }
  return "en";
}

export default i18n;
