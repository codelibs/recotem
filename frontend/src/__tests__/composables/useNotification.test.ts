import { describe, it, expect, vi, beforeEach } from "vitest";
import { useNotification } from "@/composables/useNotification";

const mockAdd = vi.fn();
vi.mock("primevue/usetoast", () => ({
  useToast: () => ({ add: mockAdd }),
}));

describe("useNotification", () => {
  beforeEach(() => {
    mockAdd.mockClear();
  });

  it("success() calls toast.add with severity 'success' and default life 5000", () => {
    const { success } = useNotification();
    success("Operation completed");

    expect(mockAdd).toHaveBeenCalledWith({
      severity: "success",
      summary: "Operation completed",
      life: 5000,
    });
  });

  it("error() calls toast.add with severity 'error' and default life 10000", () => {
    const { error } = useNotification();
    error("Something went wrong");

    expect(mockAdd).toHaveBeenCalledWith({
      severity: "error",
      summary: "Something went wrong",
      life: 10000,
    });
  });

  it("warning() calls toast.add with severity 'warn' and default life 5000", () => {
    const { warning } = useNotification();
    warning("Be careful");

    expect(mockAdd).toHaveBeenCalledWith({
      severity: "warn",
      summary: "Be careful",
      life: 5000,
    });
  });

  it("info() calls toast.add with severity 'info' and default life 5000", () => {
    const { info } = useNotification();
    info("FYI");

    expect(mockAdd).toHaveBeenCalledWith({
      severity: "info",
      summary: "FYI",
      life: 5000,
    });
  });

  it("custom life parameter works for success", () => {
    const { success } = useNotification();
    success("Quick message", 2000);

    expect(mockAdd).toHaveBeenCalledWith({
      severity: "success",
      summary: "Quick message",
      life: 2000,
    });
  });

  it("custom life parameter works for error", () => {
    const { error } = useNotification();
    error("Custom error", 3000);

    expect(mockAdd).toHaveBeenCalledWith({
      severity: "error",
      summary: "Custom error",
      life: 3000,
    });
  });

  it("custom life parameter works for warning", () => {
    const { warning } = useNotification();
    warning("Custom warning", 7000);

    expect(mockAdd).toHaveBeenCalledWith({
      severity: "warn",
      summary: "Custom warning",
      life: 7000,
    });
  });

  it("custom life parameter works for info", () => {
    const { info } = useNotification();
    info("Custom info", 1000);

    expect(mockAdd).toHaveBeenCalledWith({
      severity: "info",
      summary: "Custom info",
      life: 1000,
    });
  });
});
