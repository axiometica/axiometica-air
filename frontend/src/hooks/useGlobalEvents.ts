/**
 * useGlobalEvents — subscribe to the global incident/approval event stream.
 *
 * The hook opens (and shares) a single WebSocket per browser tab and calls
 * `callback` whenever a relevant server event arrives.  The socket is kept
 * alive for the lifetime of the component that calls this hook; it is
 * automatically torn down when the last subscriber unmounts.
 *
 * Usage:
 *   useGlobalEvents(useCallback((event) => {
 *     if (event.type === 'incident_updated') refetch()
 *   }, [refetch]))
 */

import { useEffect, useCallback } from 'react'
import { globalEvents, GlobalEvent } from '../services/eventSocket'

export type { GlobalEvent }

export function useGlobalEvents(
  callback: (event: GlobalEvent) => void,
): void {
  // Stable ref to the callback — avoids tearing down the subscription on
  // every render while still always calling the latest version.
  const stableCallback = useCallback(callback, [callback])

  useEffect(() => {
    globalEvents.connect()
    const unsub = globalEvents.subscribe(stableCallback)
    return () => {
      unsub()
      globalEvents.disconnect()
    }
  }, [stableCallback])
}
