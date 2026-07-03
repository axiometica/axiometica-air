/**
 * User-friendly date/time formatting utilities
 *
 * The backend stores timestamps in UTC but returns them without a 'Z' suffix
 * (e.g. "2026-05-24T13:41:18.842075").  Browsers (especially Chrome/V8) parse
 * date-time strings that lack a timezone designator as UTC, which causes them
 * to display in UTC rather than the user's local timezone.
 *
 * parseUTC() normalises any such string to an unambiguous UTC Date so that all
 * subsequent toLocale* calls correctly apply the user's browser timezone.
 */
export function parseUTC(dateString: string): Date {
  if (!dateString) return new Date(NaN)
  const s = dateString.trim()
  // Already has a timezone designator (Z, +HH:MM, -HH:MM) — parse as-is
  if (/Z$|[+-]\d{2}:\d{2}$/.test(s)) return new Date(s)
  // No designator → treat as UTC by appending Z
  return new Date(s + 'Z')
}

export function formatRelativeTime(dateString: string): string {
  const date = parseUTC(dateString)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffSecs = Math.floor(diffMs / 1000)
  const diffMins = Math.floor(diffSecs / 60)
  const diffHours = Math.floor(diffMins / 60)
  const diffDays = Math.floor(diffHours / 24)

  if (diffSecs < 60) {
    return `${diffSecs}s ago`
  } else if (diffMins < 60) {
    return `${diffMins}m ago`
  } else if (diffHours < 24) {
    return `${diffHours}h ago`
  } else if (diffDays < 7) {
    return `${diffDays}d ago`
  } else {
    return formatDate(dateString)
  }
}

export function formatDate(dateString: string): string {
  const date = parseUTC(dateString)
  const options: Intl.DateTimeFormatOptions = {
    month: 'short',
    day: 'numeric',
    year: date.getFullYear() !== new Date().getFullYear() ? 'numeric' : undefined,
  }
  return date.toLocaleDateString('en-US', options)
}

export function formatTime(dateString: string): string {
  const date = parseUTC(dateString)
  const options: Intl.DateTimeFormatOptions = {
    hour: '2-digit',
    minute: '2-digit',
    hour12: true,
  }
  return date.toLocaleTimeString('en-US', options)
}

export function formatDateTime(dateString: string): string {
  return `${formatDate(dateString)} at ${formatTime(dateString)}`
}

export function formatDuration(seconds: number): string {
  if (seconds < 60) {
    return `${Math.round(seconds)}s`
  } else if (seconds < 3600) {
    const mins = Math.floor(seconds / 60)
    const secs = Math.round(seconds % 60)
    return `${mins}m ${secs}s`
  } else {
    const hours = Math.floor(seconds / 3600)
    const mins = Math.floor((seconds % 3600) / 60)
    return `${hours}h ${mins}m`
  }
}
