import { api } from "./client";
import { ENDPOINTS } from "./endpoints";
import type {
  ApiKey,
  ApiKeyCreateResponse,
  RetrainingSchedule,
  RetrainingRun,
  DeploymentSlot,
  ABTest,
  ABTestResult,
} from "@/types/production";

// API Keys
export const getApiKeys = (projectId: number, signal?: AbortSignal) =>
  api<{ results: ApiKey[] }>(ENDPOINTS.API_KEYS, { params: { project: projectId }, signal });

export const createApiKey = (data: { project: number; name: string; scopes: string[]; expires_at?: string }) =>
  api<ApiKeyCreateResponse>(ENDPOINTS.API_KEYS, { method: "POST", body: data });

export const revokeApiKey = (id: number) =>
  api(ENDPOINTS.API_KEY_REVOKE(id), { method: "POST" });

export const deleteApiKey = (id: number) =>
  api(ENDPOINTS.API_KEY_DETAIL(id), { method: "DELETE" });

// Retraining
export const getRetrainingSchedules = (projectId: number, signal?: AbortSignal) =>
  api<{ results: RetrainingSchedule[] }>(ENDPOINTS.RETRAINING_SCHEDULE, { params: { project: projectId }, signal });

export const createRetrainingSchedule = (data: Partial<RetrainingSchedule>) =>
  api<RetrainingSchedule>(ENDPOINTS.RETRAINING_SCHEDULE, { method: "POST", body: data });

export const updateRetrainingSchedule = (id: number, data: Partial<RetrainingSchedule>) =>
  api<RetrainingSchedule>(ENDPOINTS.RETRAINING_SCHEDULE_DETAIL(id), { method: "PATCH", body: data });

export const triggerRetraining = (id: number) =>
  api(ENDPOINTS.RETRAINING_SCHEDULE_TRIGGER(id), { method: "POST" });

export const getRetrainingRuns = (scheduleId: number, signal?: AbortSignal) =>
  api<{ results: RetrainingRun[] }>(ENDPOINTS.RETRAINING_RUN, { params: { schedule: scheduleId }, signal });

// Deployment Slots
export const getDeploymentSlots = (projectId: number, signal?: AbortSignal) =>
  api<{ results: DeploymentSlot[] }>(ENDPOINTS.DEPLOYMENT_SLOT, { params: { project: projectId }, signal });

export const createDeploymentSlot = (data: { project: number; name: string; trained_model: number; weight: number }) =>
  api<DeploymentSlot>(ENDPOINTS.DEPLOYMENT_SLOT, { method: "POST", body: data });

export const updateDeploymentSlot = (id: number, data: Partial<DeploymentSlot>) =>
  api<DeploymentSlot>(ENDPOINTS.DEPLOYMENT_SLOT_DETAIL(id), { method: "PATCH", body: data });

export const deleteDeploymentSlot = (id: number) =>
  api(ENDPOINTS.DEPLOYMENT_SLOT_DETAIL(id), { method: "DELETE" });

// A/B Tests
export const getABTests = (projectId: number, signal?: AbortSignal) =>
  api<{ results: ABTest[] }>(ENDPOINTS.AB_TEST, { params: { project: projectId }, signal });

export const createABTest = (data: Partial<ABTest>) =>
  api<ABTest>(ENDPOINTS.AB_TEST, { method: "POST", body: data });

export const startABTest = (id: number) =>
  api<ABTest>(ENDPOINTS.AB_TEST_START(id), { method: "POST" });

export const stopABTest = (id: number) =>
  api<ABTest>(ENDPOINTS.AB_TEST_STOP(id), { method: "POST" });

export const getABTestResults = (id: number, signal?: AbortSignal) =>
  api<ABTestResult>(ENDPOINTS.AB_TEST_RESULTS(id), { signal });

export const getABTestDetail = (id: number, signal?: AbortSignal) =>
  api<ABTest>(ENDPOINTS.AB_TEST_DETAIL(id), { signal });

export const promoteWinner = (id: number, slotId: number) =>
  api(ENDPOINTS.AB_TEST_PROMOTE_WINNER(id), { method: "POST", body: { slot_id: slotId } });
