export function prettyFileSize(x: number | null): string {
  if (x === null) {
    return "unknown";
  }
  if (x < 1024) {
    return `${x}B`;
  }
  if (x < 1048576) {
    return `${(x / 1024).toFixed(1)}kB`;
  }
  if (x < 1073741824) {
    return `${(x / 1048576).toFixed(1)}MB`;
  }
  return `${(x / 1073741824).toFixed(1)}GB`;
}

export function numberInputValueToNumberOrUndefined(
  value: number | undefined | null | string
): number | undefined {
  if (typeof value === "number" || value === undefined) {
    return value;
  } else {
    return undefined;
  }
}
