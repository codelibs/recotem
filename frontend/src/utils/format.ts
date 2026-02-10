/**
 * Shared formatting utilities used across pages and components.
 */

export function formatDate(dt: string): string {
  return new Date(dt).toLocaleString();
}

export function formatFileSize(bytes: number | null | undefined): string {
  if (bytes == null) return "-";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1073741824) return `${(bytes / 1048576).toFixed(1)} MB`;
  return `${(bytes / 1073741824).toFixed(1)} GB`;
}

export function formatScore(score: number | null | undefined, digits = 4): string {
  if (score == null) return "-";
  return score.toFixed(digits);
}
