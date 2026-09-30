export function compareNullableNumbers(
  left: number | null | undefined,
  right: number | null | undefined,
  direction: 1 | -1,
): number {
  if (left == null) return right == null ? 0 : 1
  if (right == null) return -1
  if (left < right) return -direction
  if (left > right) return direction
  return 0
}
