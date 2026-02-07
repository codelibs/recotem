import { defineStore } from "pinia";
import { ref } from "vue";
import { api } from "@/api/client";
import type { Project } from "@/types";

type ProjectCreateInput = Pick<
  Project,
  "name" | "user_column" | "item_column" | "time_column"
>;

export const useProjectStore = defineStore("project", () => {
  const projects = ref<Project[]>([]);
  const currentProject = ref<Project | null>(null);
  const loading = ref(false);
  const error = ref(false);

  async function fetchProjects() {
    loading.value = true;
    error.value = false;
    try {
      const response = await api("/project/");
      projects.value = response.results ?? response;
    } catch {
      error.value = true;
    } finally {
      loading.value = false;
    }
  }

  async function fetchProject(id: number) {
    loading.value = true;
    error.value = false;
    try {
      currentProject.value = await api(`/project/${id}/`);
    } catch {
      error.value = true;
    } finally {
      loading.value = false;
    }
  }

  async function createProject(data: ProjectCreateInput) {
    const project = await api("/project/", { method: "POST", body: data });
    projects.value.unshift(project);
    return project;
  }

  async function deleteProject(id: number) {
    await api(`/project/${id}/`, { method: "DELETE" });
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
