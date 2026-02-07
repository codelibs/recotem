import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useNotificationStore } from "@/stores/notification";

describe("useNotificationStore", () => {
  let store: ReturnType<typeof useNotificationStore>;

  beforeEach(() => {
    vi.useFakeTimers();
    setActivePinia(createPinia());
    store = useNotificationStore();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  describe("add", () => {
    it("should_add_notification_to_list", () => {
      store.add("info", "Test message");

      expect(store.notifications).toHaveLength(1);
      expect(store.notifications[0].type).toBe("info");
      expect(store.notifications[0].message).toBe("Test message");
    });

    it("should_assign_unique_ids", () => {
      store.add("info", "First");
      store.add("info", "Second");

      expect(store.notifications[0].id).not.toBe(store.notifications[1].id);
    });

    it("should_auto_remove_after_timeout", () => {
      store.add("info", "Will disappear", 3000);

      expect(store.notifications).toHaveLength(1);

      vi.advanceTimersByTime(3000);

      expect(store.notifications).toHaveLength(0);
    });

    it("should_not_auto_remove_when_timeout_is_zero", () => {
      store.add("info", "Persistent", 0);

      vi.advanceTimersByTime(60000);

      expect(store.notifications).toHaveLength(1);
    });
  });

  describe("remove", () => {
    it("should_remove_notification_by_id", () => {
      store.add("info", "To keep");
      store.add("warning", "To remove");

      const idToRemove = store.notifications[1].id;
      store.remove(idToRemove);

      expect(store.notifications).toHaveLength(1);
      expect(store.notifications[0].message).toBe("To keep");
    });

    it("should_handle_removing_non_existent_id", () => {
      store.add("info", "Only one");
      store.remove(99999);

      expect(store.notifications).toHaveLength(1);
    });
  });

  describe("convenience methods", () => {
    it("should_add_success_notification", () => {
      store.success("Done!");

      expect(store.notifications).toHaveLength(1);
      expect(store.notifications[0].type).toBe("success");
      expect(store.notifications[0].message).toBe("Done!");
    });

    it("should_add_error_notification_with_longer_timeout", () => {
      store.error("Something broke");

      expect(store.notifications).toHaveLength(1);
      expect(store.notifications[0].type).toBe("error");

      // Default timeout for success (5s) should not remove error
      vi.advanceTimersByTime(5000);
      expect(store.notifications).toHaveLength(1);

      // Error timeout (10s) should remove it
      vi.advanceTimersByTime(5000);
      expect(store.notifications).toHaveLength(0);
    });

    it("should_add_warning_notification", () => {
      store.warning("Watch out");

      expect(store.notifications[0].type).toBe("warning");
    });

    it("should_add_info_notification", () => {
      store.info("FYI");

      expect(store.notifications[0].type).toBe("info");
    });
  });

  describe("multiple notifications", () => {
    it("should_handle_multiple_notifications_with_independent_timeouts", () => {
      store.add("info", "First", 2000);
      store.add("info", "Second", 4000);

      expect(store.notifications).toHaveLength(2);

      vi.advanceTimersByTime(2000);
      expect(store.notifications).toHaveLength(1);
      expect(store.notifications[0].message).toBe("Second");

      vi.advanceTimersByTime(2000);
      expect(store.notifications).toHaveLength(0);
    });
  });
});
