export function computeMaxPage(
  totalSize: number | null,
  countPerPage: number
): number | null {
  if (totalSize == null) {
    return null;
  }
  let result = Math.floor(totalSize / countPerPage);
  if (totalSize % countPerPage) {
    result++;
  }
  return result;
}
