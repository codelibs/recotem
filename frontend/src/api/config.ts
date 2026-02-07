const rawApiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";

export const API_BASE_URL = rawApiBaseUrl.replace(/\/+$/, "");

export function toApiUrl(path: string): string {
  const normalizedPath = path.replace(/^\/+/, "");
  return `${API_BASE_URL}/${normalizedPath}`;
}
