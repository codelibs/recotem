import { defineStore } from "pinia";
import { ref } from "vue";
import { api, classifyApiError, unwrapResults } from "@/api/client";
import { ENDPOINTS } from "@/api/endpoints";
import type { ClassifiedApiError, Project } from "@/types";

type ProjectCreateInput = Pick<
  Project,
  "name" | "user_column" | "item_column" | "time_column"
>;

export const useProjectStore = defineStore("project", () => {
  const projects = ref<Project[]>([]);
  const currentProject = ref<Project | null>(null);
  const loading = ref(false);
  const error = ref<ClassifiedApiError | null>(null);

  async function fetchProjects() {
    loading.value = true;
    error.value = null;
    try {
      const response = await api(ENDPOINTS.PROJECT);
      projects.value = unwrapResults(response);
    } catch (err) {
      error.value = classifyApiError(err);
    } finally {
      loading.value = false;
    }
  }

  async function fetchProject(id: number) {
    loading.value = true;
    error.value = null;
    try {
      currentProject.value = await api(`${ENDPOINTS.PROJECT}${id}/`);
      error.value = null;
    } catch (err) {
      error.value = classifyApiError(err);
    } finally {
      loading.value = false;
    }
  }

  async function createProject(data: ProjectCreateInput) {
    error.value = null;
    const project = await api(ENDPOINTS.PROJECT, { method: "POST", body: data });
    projects.value.unshift(project);
    return project;
  }

  async function deleteProject(id: number) {
    error.value = null;
    await api(`${ENDPOINTS.PROJECT}${id}/`, { method: "DELETE" });
    projects.value = projects.value.filter((p) => p.id !== id);
    if (currentProject.value?.id === id) {
      currentProject.value = null;
    }
  }

  return {
    projects,
    currentProject,
    loading,
    error,
    fetchProjects,
    fetchProject,
    createProject,
    deleteProject,
  };
});
