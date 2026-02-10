import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useProjectStore } from "@/stores/project";
import type { ClassifiedApiError, Project } from "@/types";

// Mock the api client
vi.mock("@/api/client", () => ({
  api: vi.fn(),
  unwrapResults: (res: any) => Array.isArray(res) ? res : res.results,
  classifyApiError: (err: unknown) => ({
    kind: "unknown",
    status: null,
    message: err instanceof Error ? err.message : "Unknown error",
    raw: err,
  }),
}));

describe("useProjectStore", () => {
  let store: ReturnType<typeof useProjectStore>;
  let mockApi: any;

  const mockProject: Project = {
    id: 1,
    name: "Test Project",
    user_column: "user_id",
    item_column: "item_id",
    time_column: "timestamp",
    owner: 1,
    ins_datetime: "2026-01-01T00:00:00Z",
  };

  const mockProject2: Project = {
    id: 2,
    name: "Another Project",
    user_column: "user",
    item_column: "item",
    time_column: null,
    owner: 1,
    ins_datetime: "2026-01-02T00:00:00Z",
  };

  beforeEach(async () => {
    // Setup Pinia
    setActivePinia(createPinia());
    store = useProjectStore();

    // Reset and setup api mock
    const { api } = await import("@/api/client");
    mockApi = api as any;
    vi.clearAllMocks();
  });

  describe("fetchProjects", () => {
    it("should_load_projects_into_state_when_fetch_succeeds", async () => {
      // Arrange
      const mockResponse = {
        results: [mockProject, mockProject2],
      };
      mockApi.mockResolvedValueOnce(mockResponse);

      // Act
      await store.fetchProjects();

      // Assert
      expect(mockApi).toHaveBeenCalledWith("/project/");
      expect(store.projects).toEqual([mockProject, mockProject2]);
      expect(store.loading).toBe(false);
      expect(store.error).toBeNull();
    });

    it("should_handle_non_paginated_response", async () => {
      // Arrange - response without pagination wrapper
      const mockResponse = [mockProject];
      mockApi.mockResolvedValueOnce(mockResponse);

      // Act
      await store.fetchProjects();

      // Assert
      expect(store.projects).toEqual([mockProject]);
      expect(store.loading).toBe(false);
      expect(store.error).toBeNull();
    });

    it("should_set_loading_true_during_fetch", async () => {
      // Arrange
      let loadingDuringFetch = false;
      mockApi.mockImplementationOnce(async () => {
        loadingDuringFetch = store.loading;
        return { results: [mockProject] };
      });

      // Act
      await store.fetchProjects();

      // Assert
      expect(loadingDuringFetch).toBe(true);
      expect(store.loading).toBe(false);
    });

    it("should_set_error_when_fetch_fails", async () => {
      // Arrange
      mockApi.mockRejectedValueOnce(new Error("Network error"));

      // Act
      await store.fetchProjects();

      // Assert
      expect(store.error).not.toBeNull();
      expect(store.error!.kind).toBe("unknown");
      expect(store.error!.message).toBe("Network error");
      expect(store.loading).toBe(false);
      expect(store.projects).toEqual([]);
    });

    it("should_reset_error_state_before_new_fetch", async () => {
      // Arrange - set error state
      store.error = { kind: "unknown", status: null, message: "Previous error" } as ClassifiedApiError;
      mockApi.mockResolvedValueOnce({ results: [mockProject] });

      // Act
      await store.fetchProjects();

      // Assert
      expect(store.error).toBeNull();
      expect(store.projects).toEqual([mockProject]);
    });
  });

  describe("fetchProject", () => {
    it("should_load_single_project_into_currentProject_when_fetch_succeeds", async () => {
      // Arrange
      mockApi.mockResolvedValueOnce(mockProject);

      // Act
      await store.fetchProject(1);

      // Assert
      expect(mockApi).toHaveBeenCalledWith("/project/1/");
      expect(store.currentProject).toEqual(mockProject);
      expect(store.loading).toBe(false);
      expect(store.error).toBeNull();
    });

    it("should_set_error_when_fetch_single_project_fails", async () => {
      // Arrange
      mockApi.mockRejectedValueOnce(new Error("Project not found"));

      // Act
      await store.fetchProject(999);

      // Assert
      expect(store.error).not.toBeNull();
      expect(store.error!.kind).toBe("unknown");
      expect(store.error!.message).toBe("Project not found");
      expect(store.loading).toBe(false);
      expect(store.currentProject).toBeNull();
    });

    it("should_set_loading_during_fetch", async () => {
      // Arrange
      let loadingDuringFetch = false;
      mockApi.mockImplementationOnce(async () => {
        loadingDuringFetch = store.loading;
        return mockProject;
      });

      // Act
      await store.fetchProject(1);

      // Assert
      expect(loadingDuringFetch).toBe(true);
      expect(store.loading).toBe(false);
    });
  });

  describe("createProject", () => {
    it("should_add_new_project_to_beginning_of_list_when_creation_succeeds", async () => {
      // Arrange
      store.projects = [mockProject2];
      const newProjectData = {
        name: "New Project",
        user_column: "user_id",
        item_column: "item_id",
        time_column: "ts",
        owner: 1,
      };
      const createdProject: Project = {
        id: 3,
        ...newProjectData,
        ins_datetime: "2026-01-03T00:00:00Z",
      };
      mockApi.mockResolvedValueOnce(createdProject);

      // Act
      const result = await store.createProject(newProjectData);

      // Assert
      expect(mockApi).toHaveBeenCalledWith("/project/", {
        method: "POST",
        body: newProjectData,
      });
      expect(result).toEqual(createdProject);
      expect(store.projects).toEqual([createdProject, mockProject2]);
      expect(store.projects[0]).toEqual(createdProject);
    });

    it("should_return_created_project", async () => {
      // Arrange
      const newProjectData = {
        name: "Test",
        user_column: "u",
        item_column: "i",
        time_column: null,
        owner: null,
      };
      const createdProject: Project = {
        id: 5,
        ...newProjectData,
        ins_datetime: "2026-01-05T00:00:00Z",
      };
      mockApi.mockResolvedValueOnce(createdProject);

      // Act
      const result = await store.createProject(newProjectData);

      // Assert
      expect(result).toEqual(createdProject);
    });

    it("should_throw_error_when_creation_fails", async () => {
      // Arrange
      const newProjectData = {
        name: "",
        user_column: "u",
        item_column: "i",
        time_column: null,
        owner: null,
      };
      mockApi.mockRejectedValueOnce(new Error("Validation error"));

      // Act & Assert
      await expect(store.createProject(newProjectData)).rejects.toThrow(
        "Validation error"
      );
    });
  });

  describe("deleteProject", () => {
    it("should_remove_project_from_list_when_deletion_succeeds", async () => {
      // Arrange
      store.projects = [mockProject, mockProject2];
      mockApi.mockResolvedValueOnce(undefined);

      // Act
      await store.deleteProject(1);

      // Assert
      expect(mockApi).toHaveBeenCalledWith("/project/1/", {
        method: "DELETE",
      });
      expect(store.projects).toEqual([mockProject2]);
      expect(store.projects.length).toBe(1);
    });

    it("should_clear_currentProject_if_deleted_project_is_current", async () => {
      // Arrange
      store.projects = [mockProject, mockProject2];
      store.currentProject = mockProject;
      mockApi.mockResolvedValueOnce(undefined);

      // Act
      await store.deleteProject(1);

      // Assert
      expect(store.currentProject).toBeNull();
      expect(store.projects).toEqual([mockProject2]);
    });

    it("should_not_clear_currentProject_if_different_project_deleted", async () => {
      // Arrange
      store.projects = [mockProject, mockProject2];
      store.currentProject = mockProject;
      mockApi.mockResolvedValueOnce(undefined);

      // Act
      await store.deleteProject(2);

      // Assert
      expect(store.currentProject).toEqual(mockProject);
      expect(store.projects).toEqual([mockProject]);
    });

    it("should_throw_error_when_deletion_fails", async () => {
      // Arrange
      store.projects = [mockProject, mockProject2];
      mockApi.mockRejectedValueOnce(new Error("Delete failed"));

      // Act & Assert
      await expect(store.deleteProject(1)).rejects.toThrow("Delete failed");
      // Projects list should remain unchanged on error
      expect(store.projects).toEqual([mockProject, mockProject2]);
    });

    it("should_handle_deleting_non_existent_project", async () => {
      // Arrange
      store.projects = [mockProject];
      mockApi.mockResolvedValueOnce(undefined);

      // Act
      await store.deleteProject(999);

      // Assert
      expect(store.projects).toEqual([mockProject]);
    });
  });

  describe("initial state", () => {
    it("should_initialize_with_empty_arrays_and_null_values", () => {
      // Arrange & Act
      const freshStore = useProjectStore();

      // Assert
      expect(freshStore.projects).toEqual([]);
      expect(freshStore.currentProject).toBeNull();
      expect(freshStore.loading).toBe(false);
      expect(freshStore.error).toBeNull();
    });
  });
});
