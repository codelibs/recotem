import { describe, it, expect } from "vitest";
import { formatDate, formatFileSize, formatScore } from "@/utils/format";

describe("formatDate", () => {
  it("returns a locale string for a valid ISO date", () => {
    const result = formatDate("2024-01-15T10:30:00Z");
    expect(result).toBe(new Date("2024-01-15T10:30:00Z").toLocaleString());
  });
});

describe("formatFileSize", () => {
  it('returns "-" for null', () => {
    expect(formatFileSize(null)).toBe("-");
  });

  it('returns "-" for undefined', () => {
    expect(formatFileSize(undefined)).toBe("-");
  });

  it('returns "0 B" for 0', () => {
    expect(formatFileSize(0)).toBe("0 B");
  });

  it('returns "500 B" for 500', () => {
    expect(formatFileSize(500)).toBe("500 B");
  });

  it('returns "1.0 KB" for 1024', () => {
    expect(formatFileSize(1024)).toBe("1.0 KB");
  });

  it('returns "1.5 KB" for 1536', () => {
    expect(formatFileSize(1536)).toBe("1.5 KB");
  });

  it('returns "1.0 MB" for 1048576', () => {
    expect(formatFileSize(1048576)).toBe("1.0 MB");
  });

  it('returns "1.0 GB" for 1073741824', () => {
    expect(formatFileSize(1073741824)).toBe("1.0 GB");
  });
});

describe("formatScore", () => {
  it('returns "-" for null', () => {
    expect(formatScore(null)).toBe("-");
  });

  it('returns "-" for undefined', () => {
    expect(formatScore(undefined)).toBe("-");
  });

  it("formats to 4 decimal places by default", () => {
    expect(formatScore(0.12345)).toBe("0.1235");
  });

  it("formats to custom decimal places", () => {
    expect(formatScore(0.12345, 2)).toBe("0.12");
  });
});
