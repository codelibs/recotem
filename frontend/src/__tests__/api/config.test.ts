import { describe, it, expect, vi, beforeEach } from "vitest";
import { toApiUrl } from "@/api/config";

describe("api/config", () => {
  describe("toApiUrl", () => {
    it("joins base URL with path", () => {
      expect(toApiUrl("project/")).toBe("/api/v1/project/");
    });

    it("strips leading slashes from path", () => {
      expect(toApiUrl("/project/")).toBe("/api/v1/project/");
    });

    it("handles paths with multiple leading slashes", () => {
      expect(toApiUrl("///project/")).toBe("/api/v1/project/");
    });
  });

  describe("buildWsBaseUrl", () => {
    beforeEach(() => {
      vi.resetModules();
    });

    it("returns ws: URL for http: location", async () => {
      vi.stubGlobal("location", { protocol: "http:", host: "localhost:5173" });
      const { buildWsBaseUrl } = await import("@/api/config");
      expect(buildWsBaseUrl()).toBe("ws://localhost:5173");
      vi.unstubAllGlobals();
    });

    it("returns wss: URL for https: location", async () => {
      vi.stubGlobal("location", {
        protocol: "https:",
        host: "example.com",
      });
      const { buildWsBaseUrl } = await import("@/api/config");
      expect(buildWsBaseUrl()).toBe("wss://example.com");
      vi.unstubAllGlobals();
    });
  });
});
