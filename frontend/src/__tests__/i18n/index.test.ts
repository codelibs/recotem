import { describe, it, expect, beforeEach } from "vitest";
import { setLocale, getLocale } from "@/i18n";

describe("i18n", () => {
  beforeEach(() => {
    localStorage.clear();
    setLocale("en"); // reset
  });

  describe("getLocale", () => {
    it("returns current locale", () => {
      expect(getLocale()).toBe("en");
    });
  });

  describe("setLocale", () => {
    it("changes locale to ja", () => {
      setLocale("ja");
      expect(getLocale()).toBe("ja");
      expect(localStorage.getItem("locale")).toBe("ja");
    });

    it("falls back to en for invalid locale", () => {
      setLocale("invalid");
      expect(getLocale()).toBe("en");
      expect(localStorage.getItem("locale")).toBe("en");
    });

    it("changes back to en", () => {
      setLocale("ja");
      setLocale("en");
      expect(getLocale()).toBe("en");
    });
  });
});
