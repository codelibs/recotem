import { describe, it, expect } from "vitest";
import { JOB_STATUS } from "@/types";

describe("JOB_STATUS", () => {
  it("has all expected status values", () => {
    expect(JOB_STATUS.PENDING).toBe("PENDING");
    expect(JOB_STATUS.RUNNING).toBe("RUNNING");
    expect(JOB_STATUS.COMPLETED).toBe("COMPLETED");
    expect(JOB_STATUS.FAILED).toBe("FAILED");
  });

  it("has exactly 4 entries", () => {
    expect(Object.keys(JOB_STATUS)).toHaveLength(4);
  });
});
