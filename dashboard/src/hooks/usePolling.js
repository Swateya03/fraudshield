import { useState, useEffect, useCallback } from 'react'

/**
 * Polls a fetch function every `interval` ms.
 * Returns { data, loading, error, refetch }.
 */
export function usePolling(fetchFn, interval = 3000, deps = []) {
  const [data,    setData]    = useState(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState(null)

  const refetch = useCallback(async () => {
    try {
      const result = await fetchFn()
      setData(result)
      setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, deps) // eslint-disable-line

  useEffect(() => {
    refetch()
    const id = setInterval(refetch, interval)
    return () => clearInterval(id)
  }, [refetch, interval])

  return { data, loading, error, refetch }
}
