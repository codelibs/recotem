import { api } from "./client";
import { ENDPOINTS } from "./endpoints";
import type { ManagedUser, UserCreatePayload, UserUpdatePayload } from "@/types";

export const getUsers = (signal?: AbortSignal) =>
  api<ManagedUser[]>(ENDPOINTS.USERS, { signal });

export const createUser = (data: UserCreatePayload) =>
  api<ManagedUser>(ENDPOINTS.USERS, { method: "POST", body: data });

export const updateUser = (id: number, data: UserUpdatePayload) =>
  api<ManagedUser>(ENDPOINTS.USER_DETAIL(id), { method: "PATCH", body: data });

export const deactivateUser = (id: number) =>
  api<ManagedUser>(ENDPOINTS.USER_DEACTIVATE(id), { method: "POST" });

export const activateUser = (id: number) =>
  api<ManagedUser>(ENDPOINTS.USER_ACTIVATE(id), { method: "POST" });

export const resetUserPassword = (id: number, newPassword: string) =>
  api(ENDPOINTS.USER_RESET_PASSWORD(id), { method: "POST", body: { new_password: newPassword } });

export const changeOwnPassword = (oldPassword: string, newPassword: string) =>
  api(ENDPOINTS.USER_CHANGE_PASSWORD, { method: "POST", body: { old_password: oldPassword, new_password: newPassword } });
