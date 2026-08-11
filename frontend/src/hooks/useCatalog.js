// frontend/src/hooks/useCatalog.js
// One page at a time out of QMetry's catalogue — test runs (TR) or the whole
// project test case library (TC).
//
// Neither list can be loaded whole: ~430 runs and ~2500 cases, and the cases
// only carry names because the backend asks for them. So the search box is not
// a filter over what's on screen — the query goes to the server, which pushes it
// down to QMetry's own substring match. That keeps a search over 2500 cases as
// cheap as a search over 50, at the cost of a round trip per query (debounced).

import { useCallback, useEffect, useRef, useState } from 'react'

const PAGE_SIZE = 50
const DEBOUNCE_MS = 300

// mode 'tr' -> GET /cycles, mode 'tc' -> GET /testcases. Both answer with the
// same page shape; only the array's key differs.
const ENDPOINTS = {
  tr: { path: '/cycles', key: 'cycles' },
  tc: { path: '/testcases', key: 'cases' },
}

export function useCatalog(mode) {
  const [query, setQuery] = useState('')
  const [debounced, setDebounced] = useState('')
  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)
  // Where the next page starts, as told by the server. Not items.length: the
  // server may drop rows (archived cycles) from a page, and counting kept rows
  // would drift off QMetry's offset and skip records.
  const [nextStart, setNextStart] = useState(0)
  // A multi-term search stopped scanning at its cap, so `total` is a floor.
  const [truncated, setTruncated] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  // Guards against a slow early response overwriting a newer one: only the
  // most recent request is allowed to publish its results.
  const requestRef = useRef(0)

  useEffect(() => {
    const t = setTimeout(() => setDebounced(query.trim()), DEBOUNCE_MS)
    return () => clearTimeout(t)
  }, [query])

  // Switching TR <-> TC starts a different list; clear the query with it so the
  // tester never sees an empty list that's really a stale filter.
  useEffect(() => {
    setQuery('')
    setDebounced('')
  }, [mode])

  const fetchPage = useCallback(
    async (start, append) => {
      const { path, key } = ENDPOINTS[mode] ?? ENDPOINTS.tr
      const token = ++requestRef.current
      setLoading(true)
      try {
        const url = `${path}?q=${encodeURIComponent(debounced)}&start=${start}&limit=${PAGE_SIZE}`
        const res = await fetch(url, { cache: 'no-store' })
        if (!res.ok) {
          const detail = await res.json().catch(() => ({}))
          throw new Error(detail.detail || `${res.status} ${res.statusText}`)
        }
        const body = await res.json()
        if (token !== requestRef.current) return // a newer query already won
        const rows = body[key] ?? []
        setItems((prev) => (append ? [...prev, ...rows] : rows))
        setTotal(body.total ?? rows.length)
        setNextStart(body.next_start ?? start + rows.length)
        setTruncated(Boolean(body.truncated))
        setError(null)
      } catch (e) {
        if (token === requestRef.current) setError(e.message)
      } finally {
        if (token === requestRef.current) setLoading(false)
      }
    },
    [mode, debounced],
  )

  useEffect(() => {
    fetchPage(0, false)
  }, [fetchPage])

  const hasMore = nextStart < total
  const loadMore = useCallback(() => {
    if (!loading && hasMore) fetchPage(nextStart, true)
  }, [fetchPage, loading, hasMore, nextStart])

  return {
    query, setQuery, items, total, loading, error, hasMore, truncated, loadMore,
  }
}
