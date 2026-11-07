/**
 * Formats a number of seconds as `m:ss`, for example `0:21` or `12:05`.
 * The value is floored, not rounded: the browser's audio control floors its
 * total (`1:36` for 96.72 s), and the last segment must end on that same number.
 */
export function formatTime(seconds: number): string {
  const whole = Math.floor(seconds)
  const minutes = Math.floor(whole / 60)
  const rest = whole % 60
  return `${minutes}:${String(rest).padStart(2, '0')}`
}
