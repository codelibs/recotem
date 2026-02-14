import { describe, it, expect } from "vitest";
import { ENDPOINTS } from "@/api/endpoints";

describe("ENDPOINTS", () => {
  describe("static paths", () => {
    it("has correct static paths", () => {
      expect(ENDPOINTS.PROJECT).toBe("/project/");
      expect(ENDPOINTS.TRAINING_DATA).toBe("/training_data/");
      expect(ENDPOINTS.PARAMETER_TUNING_JOB).toBe("/parameter_tuning_job/");
      expect(ENDPOINTS.TRAINED_MODEL).toBe("/trained_model/");
      expect(ENDPOINTS.MODEL_CONFIGURATION).toBe("/model_configuration/");
      expect(ENDPOINTS.SPLIT_CONFIG).toBe("/split_config/");
      expect(ENDPOINTS.EVALUATION_CONFIG).toBe("/evaluation_config/");
      expect(ENDPOINTS.TASK_LOG).toBe("/task_log/");
      expect(ENDPOINTS.AUTH_LOGIN).toBe("/auth/login/");
      expect(ENDPOINTS.AUTH_TOKEN_REFRESH).toBe("/auth/token/refresh/");
      expect(ENDPOINTS.AUTH_LOGOUT).toBe("/auth/logout/");
      expect(ENDPOINTS.AUTH_USER).toBe("/auth/user/");
      expect(ENDPOINTS.PING).toBe("/ping/");
      expect(ENDPOINTS.API_KEYS).toBe("/api_keys/");
      expect(ENDPOINTS.RETRAINING_SCHEDULE).toBe("/retraining_schedule/");
      expect(ENDPOINTS.RETRAINING_RUN).toBe("/retraining_run/");
      expect(ENDPOINTS.DEPLOYMENT_SLOT).toBe("/deployment_slot/");
      expect(ENDPOINTS.AB_TEST).toBe("/ab_test/");
    });

    it("all static endpoints are strings", () => {
      const staticKeys = Object.entries(ENDPOINTS).filter(
        ([, v]) => typeof v === "string",
      );
      expect(staticKeys.length).toBeGreaterThan(0);
      for (const [, value] of staticKeys) {
        expect(typeof value).toBe("string");
        expect(value).toMatch(/^\//); // starts with /
        expect(value).toMatch(/\/$/); // ends with /
      }
    });
  });

  describe("dynamic paths", () => {
    it("generates detail paths correctly", () => {
      expect(ENDPOINTS.PROJECT_SUMMARY(42)).toBe("/project_summary/42/");
      expect(ENDPOINTS.TRAINING_DATA_DETAIL(7)).toBe("/training_data/7/");
      expect(ENDPOINTS.TRAINING_DATA_PREVIEW(7)).toBe("/training_data/7/preview/");
      expect(ENDPOINTS.PARAMETER_TUNING_JOB_DETAIL(3)).toBe("/parameter_tuning_job/3/");
      expect(ENDPOINTS.TRAINED_MODEL_DETAIL(5)).toBe("/trained_model/5/");
      expect(ENDPOINTS.TRAINED_MODEL_RECOMMENDATION(5)).toBe("/trained_model/5/recommendation/");
      expect(ENDPOINTS.MODEL_CONFIGURATION_DETAIL(10)).toBe("/model_configuration/10/");
      expect(ENDPOINTS.API_KEY_DETAIL(1)).toBe("/api_keys/1/");
      expect(ENDPOINTS.API_KEY_REVOKE(1)).toBe("/api_keys/1/revoke/");
      expect(ENDPOINTS.RETRAINING_SCHEDULE_DETAIL(2)).toBe("/retraining_schedule/2/");
      expect(ENDPOINTS.RETRAINING_SCHEDULE_TRIGGER(2)).toBe("/retraining_schedule/2/trigger/");
      expect(ENDPOINTS.DEPLOYMENT_SLOT_DETAIL(3)).toBe("/deployment_slot/3/");
      expect(ENDPOINTS.AB_TEST_DETAIL(4)).toBe("/ab_test/4/");
      expect(ENDPOINTS.AB_TEST_START(4)).toBe("/ab_test/4/start/");
      expect(ENDPOINTS.AB_TEST_STOP(4)).toBe("/ab_test/4/stop/");
      expect(ENDPOINTS.AB_TEST_RESULTS(4)).toBe("/ab_test/4/results/");
      expect(ENDPOINTS.AB_TEST_PROMOTE_WINNER(4)).toBe("/ab_test/4/promote_winner/");
    });

    it("interpolates different IDs correctly", () => {
      expect(ENDPOINTS.PROJECT_SUMMARY(1)).toBe("/project_summary/1/");
      expect(ENDPOINTS.PROJECT_SUMMARY(999)).toBe("/project_summary/999/");
      expect(ENDPOINTS.TRAINING_DATA_DETAIL(0)).toBe("/training_data/0/");
    });

    it("all dynamic endpoints are functions", () => {
      const dynamicKeys = Object.entries(ENDPOINTS).filter(
        ([, v]) => typeof v === "function",
      );
      expect(dynamicKeys.length).toBeGreaterThan(0);
      for (const [, fn] of dynamicKeys) {
        const result = (fn as (id: number) => string)(1);
        expect(typeof result).toBe("string");
        expect(result).toMatch(/^\//); // starts with /
        expect(result).toMatch(/\/$/); // ends with /
        expect(result).toContain("1");
      }
    });
  });

  describe("completeness", () => {
    it("exports all expected endpoint keys", () => {
      const expectedKeys = [
        "PROJECT",
        "PROJECT_SUMMARY",
        "TRAINING_DATA",
        "TRAINING_DATA_DETAIL",
        "TRAINING_DATA_PREVIEW",
        "PARAMETER_TUNING_JOB",
        "PARAMETER_TUNING_JOB_DETAIL",
        "TRAINED_MODEL",
        "TRAINED_MODEL_DETAIL",
        "TRAINED_MODEL_RECOMMENDATION",
        "MODEL_CONFIGURATION",
        "MODEL_CONFIGURATION_DETAIL",
        "SPLIT_CONFIG",
        "EVALUATION_CONFIG",
        "TASK_LOG",
        "AUTH_LOGIN",
        "AUTH_TOKEN_REFRESH",
        "AUTH_LOGOUT",
        "AUTH_USER",
        "PING",
        "API_KEYS",
        "API_KEY_DETAIL",
        "API_KEY_REVOKE",
        "RETRAINING_SCHEDULE",
        "RETRAINING_SCHEDULE_DETAIL",
        "RETRAINING_SCHEDULE_TRIGGER",
        "RETRAINING_RUN",
        "DEPLOYMENT_SLOT",
        "DEPLOYMENT_SLOT_DETAIL",
        "AB_TEST",
        "AB_TEST_DETAIL",
        "AB_TEST_START",
        "AB_TEST_STOP",
        "AB_TEST_RESULTS",
        "AB_TEST_PROMOTE_WINNER",
        "USERS",
        "USER_DETAIL",
        "USER_DEACTIVATE",
        "USER_ACTIVATE",
        "USER_RESET_PASSWORD",
        "USER_CHANGE_PASSWORD",
      ];
      for (const key of expectedKeys) {
        expect(ENDPOINTS).toHaveProperty(key);
      }
    });

    it("has no unexpected keys beyond the known set", () => {
      const knownKeys = [
        "PROJECT",
        "PROJECT_SUMMARY",
        "TRAINING_DATA",
        "TRAINING_DATA_DETAIL",
        "TRAINING_DATA_PREVIEW",
        "PARAMETER_TUNING_JOB",
        "PARAMETER_TUNING_JOB_DETAIL",
        "TRAINED_MODEL",
        "TRAINED_MODEL_DETAIL",
        "TRAINED_MODEL_RECOMMENDATION",
        "MODEL_CONFIGURATION",
        "MODEL_CONFIGURATION_DETAIL",
        "SPLIT_CONFIG",
        "EVALUATION_CONFIG",
        "TASK_LOG",
        "AUTH_LOGIN",
        "AUTH_TOKEN_REFRESH",
        "AUTH_LOGOUT",
        "AUTH_USER",
        "PING",
        "API_KEYS",
        "API_KEY_DETAIL",
        "API_KEY_REVOKE",
        "RETRAINING_SCHEDULE",
        "RETRAINING_SCHEDULE_DETAIL",
        "RETRAINING_SCHEDULE_TRIGGER",
        "RETRAINING_RUN",
        "DEPLOYMENT_SLOT",
        "DEPLOYMENT_SLOT_DETAIL",
        "AB_TEST",
        "AB_TEST_DETAIL",
        "AB_TEST_START",
        "AB_TEST_STOP",
        "AB_TEST_RESULTS",
        "AB_TEST_PROMOTE_WINNER",
        "USERS",
        "USER_DETAIL",
        "USER_DEACTIVATE",
        "USER_ACTIVATE",
        "USER_RESET_PASSWORD",
        "USER_CHANGE_PASSWORD",
      ];
      const actualKeys = Object.keys(ENDPOINTS);
      expect(actualKeys.sort()).toEqual(knownKeys.sort());
    });
  });
});
