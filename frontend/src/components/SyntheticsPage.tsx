import { useState, useEffect, useRef, CSSProperties, ChangeEvent } from 'react'
import { IconCheck, IconX, IconBolt, IconTool, IconFileText, IconTestPipe, IconLoader } from './icons'
import {
  listSyntheticMonitors,
  createSyntheticMonitor,
  updateSyntheticMonitor,
  deleteSyntheticMonitor,
  generateSyntheticScript,
  testSyntheticScript,
} from '../services/api'
import type { SyntheticMonitor } from '../types'

// ── Design tokens ─────────────────────────────────────────────────────────────

const DS = {
  bg:      '#0d1117',
  surface: '#1a1f2e',
  raised:  '#252c3c',
  border:  '#3d4557',
  txtP:    '#e8eef5',
  txtS:    '#7a8ba3',
  txtM:    '#a0aec0',
  accent:  '#3b82f6',
  green:   '#10b981',
  mutedGreen: '#3a7a5a',
  red:     '#ef4444',
  yellow:  '#f59e0b',
} as const

const card: CSSProperties = {
  backgroundColor: DS.surface,
  border: `1px solid ${DS.border}`,
  borderRadius: 10,
  padding: '1.25rem',
  marginBottom: '1rem',
}

const btn = (color: string = DS.accent): CSSProperties => ({
  padding: '7px 16px',
  borderRadius: 7,
  border: 'none',
  backgroundColor: color,
  color: '#fff',
  fontSize: '0.82rem',
  fontWeight: 600,
  cursor: 'pointer',
  display: 'inline-flex',
  alignItems: 'center',
  gap: 6,
  transition: 'opacity 0.15s',
})

const outlineBtn: CSSProperties = {
  padding: '6px 14px',
  borderRadius: 7,
  border: `1px solid ${DS.border}`,
  backgroundColor: DS.raised,
  color: DS.txtP,
  fontSize: '0.82rem',
  fontWeight: 500,
  cursor: 'pointer',
  display: 'inline-flex',
  alignItems: 'center',
  gap: 6,
}

const input: CSSProperties = {
  width: '100%',
  padding: '8px 12px',
  borderRadius: 7,
  border: `1px solid ${DS.border}`,
  backgroundColor: DS.raised,
  color: DS.txtP,
  fontSize: '0.875rem',
  outline: 'none',
  boxSizing: 'border-box',
}

const label: CSSProperties = {
  display: 'block',
  fontSize: '0.78rem',
  fontWeight: 600,
  color: DS.txtM,
  textTransform: 'uppercase',
  letterSpacing: '0.05em',
  marginBottom: 6,
}

const overlay: CSSProperties = {
  position: 'fixed', inset: 0,
  backgroundColor: 'rgba(0,0,0,0.6)',
  display: 'flex', alignItems: 'center', justifyContent: 'center',
  zIndex: 1000,
}

const modal: CSSProperties = {
  backgroundColor: DS.surface,
  border: `1px solid ${DS.border}`,
  borderRadius: 12,
  width: '820px',
  maxWidth: '95vw',
  maxHeight: '90vh',
  display: 'flex',
  flexDirection: 'column',
  overflow: 'hidden',
}

// ── HAR parsing ───────────────────────────────────────────────────────────────

const HAR_ASSET_EXT = /\.(js|mjs|jsx|css|png|jpg|jpeg|gif|webp|avif|svg|ico|woff|woff2|ttf|eot|otf|map)(\?.*)?$/i
const HAR_TRACKING = /(google-analytics\.com|googletagmanager\.com|analytics\.|\.gtm\.|hotjar\.|clarity\.ms|sentry\.io|bugsnag\.com|logrocket\.com|datadog-browser|segment\.io|mixpanel\.com|amplitude\.com|intercom\.io|crisp\.chat|hs-scripts\.com|hubspot\.com|vercel-analytics\.com|vercel-insights\.com|va\.vercel|fonts\.googleapis\.com|fonts\.gstatic\.com|walkme\.com|adobedtm\.com|demdex\.net|omtrdc\.net|trustarc\.com|onetrust\.com|cookielaw\.org|doubleclick\.net|googlesyndication\.com|googleadservices\.com)/i
const HAR_CRED_FIELD = /^(email|user_?password|password|passwd|passcode|username|user_?name|identifier|login|pass|api_?key|client_?id|client_?secret|access_?token|auth_?token|secret_?key?|token)$/i
// Custom headers likely to carry session/CSRF state. Deliberately excludes
// Authorization (handled by the dedicated login/bearer-token flow below) and
// Cookie/Set-Cookie (httpx.Client()'s cookie jar already replays those for free).
const HAR_STATE_HEADER = /^x-[a-z0-9-]*(session|csrf|xsrf|token|auth)[a-z0-9-]*$/i
// Values shorter than this are too likely to be coincidental (e.g. "1", "true")
// to treat as a correlated session/token value.
const MIN_CORR_LEN = 6
// A POST body containing one of these field names is treated as the login
// step, regardless of URL path — narrower than HAR_CRED_FIELD (which also
// matches a lone "email" or "token" field) so it doesn't fire on requests
// that merely carry a credential-shaped field but aren't the login itself.
// Includes Okta Identity Engine's "passcode" (nested under a "credentials"
// object in /idx/challenge/answer, not top-level like a plain password form).
const HAR_PASSWORD_FIELD = /^(user_?password|user_?passwd|password|passwd|pass|passcode)$/i

// Same nesting depth as redactAndSubstituteBody (top level + 1) so a
// password/passcode field wrapped in an object (e.g. Okta's
// {"credentials": {"passcode": "..."}}) is still detected as the login step.
function hasPasswordField(obj: any, depth = 0): boolean {
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return false
  for (const [k, v] of Object.entries(obj)) {
    if (HAR_PASSWORD_FIELD.test(k)) return true
    if (depth < 1 && v !== null && typeof v === 'object' && !Array.isArray(v) && hasPasswordField(v, depth + 1)) return true
  }
  return false
}

// Well-known server-rendered CSRF hidden-input / meta-tag field names.
// Session-based sites embed these in the login page's HTML rather than
// exposing them via a JSON API, so they need HTML scraping, not resp.json().
const CSRF_INPUT_NAMES = ['csrf_token', 'csrfmiddlewaretoken', 'authenticity_token', '_token', '__RequestVerificationToken', '_csrf', 'csrf']
const CSRF_META_NAMES = ['csrf-token', 'csrf_token']

function extractCsrfTokens(html: string): { name: string; value: string; tag: 'input' | 'meta' }[] {
  const out: { name: string; value: string; tag: 'input' | 'meta' }[] = []
  for (const name of CSRF_INPUT_NAMES) {
    const re = new RegExp(`<input\\b(?=[^>]*\\bname=["']${name}["'])(?=[^>]*\\bvalue=["']([^"']*)["'])`, 'i')
    const m = re.exec(html)
    if (m && m[1]) out.push({ name, value: m[1], tag: 'input' })
  }
  for (const name of CSRF_META_NAMES) {
    const re = new RegExp(`<meta\\b(?=[^>]*\\bname=["']${name}["'])(?=[^>]*\\bcontent=["']([^"']*)["'])`, 'i')
    const m = re.exec(html)
    if (m && m[1]) out.push({ name, value: m[1], tag: 'meta' })
  }
  return out
}

interface CredSuggestion {
  key: string
  value: string
}

interface ParsedRequest {
  method: string
  origin: string        // scheme+host, e.g. https://app.example.com — explicit per request
  pathname: string       // path only, no query; may contain {{varName}} tokens for dynamic segments
  displayPath: string    // origin+path+query as recorded, for human-readable logs/summary
  status: number
  queryParams?: { key: string; value: string; varName?: string }[]
  bodyStr?: string       // body fields as a JSON object string; leaves may be "<VAR>" placeholders
  bodyKind?: 'json' | 'form'  // whether bodyStr should be sent as json= or form-encoded data=
  isCredentialSubmit?: boolean // POST body carries a password-shaped field — treat as the login step
  captures?: { varName: string; fieldPath: string }[]        // values to extract from THIS response
  headerCaptures?: { headerName: string; varName: string }[] // captured vars to also set as persistent client headers
}

interface ParsedPage {
  name: string
  requests: ParsedRequest[]
  bodyPattern?: string   // optional regex — any response in the page must match
}

interface HarParseResult {
  pageCount: number
  requestCount: number
  credSuggestions: CredSuggestion[]
  summary: string
  pages: ParsedPage[]
}

// Page names come from the HAR's page title, which Chrome DevTools sets to the
// full page URL — often long enough to blow out row alignment. Show just the
// path (host is implied — all pages in a monitor share one origin); the full
// URL is still available via the title/tooltip attribute.
function shortPagePath(name: string): string {
  try {
    const u = new URL(name)
    return (u.pathname || '/') + u.search
  } catch {
    return name
  }
}

function toEnvKey(field: string): string {
  return field.replace(/([a-z])([A-Z])/g, '$1_$2').replace(/[-\s]/g, '_').toUpperCase()
}

function sanitizeVarName(raw: string): string {
  let s = raw.replace(/([a-z0-9])([A-Z])/g, '$1_$2').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')
  if (!s) s = 'value'
  if (/^[0-9]/.test(s)) s = 'v_' + s
  return s
}

// Flattens scalar leaves up to 1 level of nesting, e.g. {a: {b: "x"}} -> [{path: "a.b", value: "x"}]
function flattenScalars(obj: any, prefix = '', depth = 0): { path: string; value: string }[] {
  if (obj === null || obj === undefined || typeof obj !== 'object' || Array.isArray(obj)) return []
  const out: { path: string; value: string }[] = []
  for (const [k, v] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${k}` : k
    if (v !== null && typeof v === 'object' && !Array.isArray(v)) {
      if (depth < 1) out.push(...flattenScalars(v, path, depth + 1))
    } else if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') {
      const s = String(v)
      if (s.length >= MIN_CORR_LEN) out.push({ path, value: s })
    }
  }
  return out
}

// Redacts credential fields (top level and one level of nesting — e.g. Okta
// Identity Engine's {"credentials": {"passcode": "..."}}) and substitutes
// correlated dynamic values (up to 1 level of nesting) with <varName> sentinels.
function redactAndSubstituteBody(
  body: any,
  credMap: Map<string, string>,
  bodyMatches: Map<string, string>,
  prefix = '',
  depth = 0,
): any {
  if (body === null || typeof body !== 'object' || Array.isArray(body)) return body
  const out: Record<string, any> = {}
  for (const [k, v] of Object.entries(body)) {
    const path = prefix ? `${prefix}.${k}` : k
    if (depth <= 1 && HAR_CRED_FIELD.test(k)) {
      const envKey = toEnvKey(k)
      if (!credMap.has(envKey)) credMap.set(envKey, '')
      out[k] = `<${envKey}>`
    } else if (bodyMatches.has(path)) {
      out[k] = `<${bodyMatches.get(path)}>`
    } else if (v !== null && typeof v === 'object' && !Array.isArray(v) && depth < 1) {
      out[k] = redactAndSubstituteBody(v, credMap, bodyMatches, path, depth + 1)
    } else {
      out[k] = v
    }
  }
  return out
}

interface CandidateEntry {
  index: number
  pageRef: string
  method: string
  origin: string
  rawSegments: string[]
  decodedSegments: string[]
  queryParams: { key: string; value: string }[]
  status: number
  kind: 'html' | 'xhr'
  bodyObj?: any
  formParams?: any[]
  bodyRawFallback?: string
  headers: { name: string; value: string }[]
  responseJson?: any
  csrfTokens?: { name: string; value: string; tag: 'input' | 'meta' }[]
  displayPath: string
}

function parseHar(harJson: string): HarParseResult {
  let har: any
  try { har = JSON.parse(harJson) } catch {
    return { pageCount: 0, requestCount: 0, credSuggestions: [], summary: 'Could not parse HAR file.', pages: [] }
  }

  const entries: any[] = har?.log?.entries ?? []
  const harPages: any[] = har?.log?.pages ?? []

  const pageNames = new Map<string, string>()
  for (const p of harPages) {
    pageNames.set(p.id, p.title || p.id)
  }

  // Some HAR exports contain literal duplicate entries for the same request
  // — one correctly tagged with a pageref, one with pageref missing/invalid
  // (observed from real ServiceNow/UI16 recordings). Left alone, the
  // untagged copy falls into a synthetic "Other" page, producing a bogus
  // extra page that just re-duplicates a real page's content. Build the set
  // of requests that already have a properly-paged copy so the orphaned
  // duplicate can be dropped instead of kept.
  const pagedSignatures = new Set<string>()
  for (const entry of entries) {
    const req = entry?.request
    if (req && entry.pageref && pageNames.has(entry.pageref)) {
      pagedSignatures.add(`${req.method}|${req.url}`)
    }
  }

  // ── Phase A: classify entries and collect everything we might need,
  // without deciding yet what gets kept. Every request keeps its own
  // explicit absolute origin (no BASE_URL) since a session can span domains.
  const candidates: CandidateEntry[] = []
  for (const entry of entries) {
    const req = entry?.request
    const res = entry?.response
    if (!req || !res) continue

    const hasValidPageref = entry.pageref && pageNames.has(entry.pageref)
    if (!hasValidPageref && pagedSignatures.has(`${req.method}|${req.url}`)) continue

    let u: URL
    try { u = new URL(req.url || '') } catch { continue }

    const pathname = u.pathname
    if (HAR_ASSET_EXT.test(pathname)) continue
    if (HAR_TRACKING.test(u.hostname)) continue
    if (req.method === 'POST' && /\/auth\/logout\b/.test(pathname)) continue
    if ((res.status || 0) === 101) continue

    const mimeType: string = (res.content?.mimeType || '').toLowerCase()
    const kind: 'html' | 'xhr' = mimeType.startsWith('text/html') ? 'html' : 'xhr'

    const rawSegments = pathname.split('/')
    const decodedSegments = rawSegments.map(s => { try { return decodeURIComponent(s) } catch { return s } })

    const queryParams: { key: string; value: string }[] = []
    u.searchParams.forEach((value, key) => queryParams.push({ key, value }))

    const headers: { name: string; value: string }[] = (req.headers ?? []).map((h: any) => ({ name: h.name, value: h.value }))

    let bodyObj: any
    let formParams: any[] | undefined
    let bodyRawFallback: string | undefined
    const postData = req.postData
    if (postData?.text) {
      const ct: string = postData.mimeType || ''
      if (ct.includes('json')) {
        try { bodyObj = JSON.parse(postData.text) } catch { bodyRawFallback = postData.text.slice(0, 300) }
      } else if (ct.includes('form') || ct.includes('urlencoded')) {
        formParams = postData.params ?? []
      } else {
        bodyRawFallback = postData.text.slice(0, 300)
      }
    }

    let responseJson: any
    if (mimeType.includes('json')) {
      try { responseJson = JSON.parse(res.content?.text ?? '') } catch { /* not JSON */ }
    }

    let csrfTokens: { name: string; value: string; tag: 'input' | 'meta' }[] | undefined
    if (kind === 'html' && res.content?.text) {
      const found = extractCsrfTokens(res.content.text)
      if (found.length) csrfTokens = found
    }

    const fullDisplay = u.origin + pathname + (u.search || '')
    const displayPath = fullDisplay.length > 120 ? fullDisplay.slice(0, 120) + '…' : fullDisplay

    candidates.push({
      index: candidates.length,
      pageRef: entry.pageref || '_unknown',
      method: req.method || 'GET',
      origin: u.origin,
      rawSegments, decodedSegments, queryParams,
      status: res.status || 0,
      kind,
      bodyObj, formParams, bodyRawFallback,
      headers,
      responseJson,
      csrfTokens,
      displayPath,
    })
  }

  // ── Phase B: index every scalar value that appeared in a response body —
  // these are candidate "sources" for correlation (session ids, tokens, ...).
  interface SourceField { entryIdx: number; path: string; value: string }
  const sourceFields: SourceField[] = []
  for (const c of candidates) {
    if (!c.responseJson) continue
    for (const f of flattenScalars(c.responseJson)) sourceFields.push({ entryIdx: c.index, ...f })
  }
  // HTML-embedded CSRF tokens (hidden <input>/<meta>) — same correlation
  // pipeline as JSON sources, just a different extraction path at codegen
  // time (see extractExpr's "__csrf__:" handling). Tag is encoded in the path
  // (not re-derived from the name) since e.g. "csrf_token" is a plausible
  // name for either an <input> or a <meta> tag.
  for (const c of candidates) {
    if (!c.csrfTokens) continue
    for (const tok of c.csrfTokens) {
      if (tok.value.length >= MIN_CORR_LEN) sourceFields.push({ entryIdx: c.index, path: `__csrf__:${tok.tag}:${tok.name}`, value: tok.value })
    }
  }

  // ── Phase C: collect every place a value gets *used* in a later request —
  // query params, path segments, JSON/form body fields, and state-looking headers.
  interface UsageSite { entryIdx: number; siteType: 'query' | 'path' | 'body' | 'header'; siteKey: string; value: string }
  const usageSites: UsageSite[] = []
  for (const c of candidates) {
    for (const q of c.queryParams) {
      if (q.value && q.value.length >= MIN_CORR_LEN) usageSites.push({ entryIdx: c.index, siteType: 'query', siteKey: q.key, value: q.value })
    }
    c.decodedSegments.forEach((seg, i) => {
      if (seg && seg.length >= MIN_CORR_LEN) usageSites.push({ entryIdx: c.index, siteType: 'path', siteKey: String(i), value: seg })
    })
    if (c.bodyObj && typeof c.bodyObj === 'object') {
      const topKeys = new Set(Object.keys(c.bodyObj).filter(k => !HAR_CRED_FIELD.test(k)))
      for (const f of flattenScalars(c.bodyObj)) {
        if (topKeys.has(f.path.split('.')[0])) usageSites.push({ entryIdx: c.index, siteType: 'body', siteKey: f.path, value: f.value })
      }
    }
    if (c.formParams) {
      for (const p of c.formParams) {
        const val = String(p.value ?? '')
        if (val && val.length >= MIN_CORR_LEN && !HAR_CRED_FIELD.test(p.name)) {
          usageSites.push({ entryIdx: c.index, siteType: 'body', siteKey: p.name, value: val })
        }
      }
    }
    for (const h of c.headers) {
      if (HAR_STATE_HEADER.test(h.name) && h.value && h.value.length >= MIN_CORR_LEN) {
        usageSites.push({ entryIdx: c.index, siteType: 'header', siteKey: h.name, value: h.value })
      }
    }
  }

  // ── Phase D: match each usage to the closest *earlier* response that produced
  // the same exact value (exact-string match keeps false positives rare).
  // Indexed by value first — a large HAR (thousands of entries, heavy
  // telemetry) can produce tens of thousands of source fields and usage
  // sites; comparing every pair (the previous approach) is O(n²) and can
  // freeze the tab for a long time. Grouping by exact value first keeps the
  // inner scan limited to genuine duplicates of that value, which in
  // practice is a handful, not the whole dataset.
  interface Match { usage: UsageSite; sourceIdx: number; sourcePath: string }
  const sourceFieldsByValue = new Map<string, SourceField[]>()
  for (const sf of sourceFields) {
    let bucket = sourceFieldsByValue.get(sf.value)
    if (!bucket) { bucket = []; sourceFieldsByValue.set(sf.value, bucket) }
    bucket.push(sf)
  }
  const matches: Match[] = []
  for (const u2 of usageSites) {
    const candidates = sourceFieldsByValue.get(u2.value)
    if (!candidates) continue
    let best: SourceField | null = null
    for (const sf of candidates) {
      if (sf.entryIdx < u2.entryIdx && (!best || sf.entryIdx > best.entryIdx)) best = sf
    }
    if (best) matches.push({ usage: u2, sourceIdx: best.entryIdx, sourcePath: best.path })
  }

  // ── Phase E: assign a stable, unique python variable name per (source, field).
  const varNameByKey = new Map<string, string>()
  const usedNames = new Set<string>()
  function assignVarName(sourceIdx: number, sourcePath: string): string {
    const key = `${sourceIdx}:${sourcePath}`
    const existing = varNameByKey.get(key)
    if (existing) return existing
    const base = sanitizeVarName(sourcePath.split('.').pop() || 'value')
    let name = base, n = 2
    while (usedNames.has(name)) { name = `${base}_${n}`; n++ }
    usedNames.add(name)
    varNameByKey.set(key, name)
    return name
  }
  for (const m of matches) assignVarName(m.sourceIdx, m.sourcePath)

  const matchesByUsageEntry = new Map<number, Match[]>()
  const matchesBySourceEntry = new Map<number, Match[]>()
  for (const m of matches) {
    if (!matchesByUsageEntry.has(m.usage.entryIdx)) matchesByUsageEntry.set(m.usage.entryIdx, [])
    matchesByUsageEntry.get(m.usage.entryIdx)!.push(m)
    if (!matchesBySourceEntry.has(m.sourceIdx)) matchesBySourceEntry.set(m.sourceIdx, [])
    matchesBySourceEntry.get(m.sourceIdx)!.push(m)
  }

  // ── Phase F: decide what to keep. HTML page navigations are always kept.
  // Many SPA "pages" render entirely from a JS-driven XHR/fetch data call —
  // there's no HTML document to key off at all — so a same-origin JSON API
  // response is also treated as page content. "Same-origin" here means the
  // origin of a real page navigation (or, if the HAR has no HTML docs at all,
  // the origin of the very first recorded request). Cross-origin XHR/fetch
  // calls are kept only when they carry state that's reused later (as the
  // source) or consume a previously-captured value (as the usage site) —
  // static assets, trackers, and unrelated third-party calls are still dropped.
  const primaryOrigins = new Set<string>(candidates.filter(c => c.kind === 'html').map(c => c.origin))
  if (primaryOrigins.size === 0 && candidates.length > 0) primaryOrigins.add(candidates[0].origin)

  const keptIdx = new Set<number>()
  for (const c of candidates) {
    if (c.kind === 'html') keptIdx.add(c.index)
    else if (c.responseJson && primaryOrigins.has(c.origin)) keptIdx.add(c.index)
  }
  for (const m of matches) { keptIdx.add(m.sourceIdx); keptIdx.add(m.usage.entryIdx) }

  // ── Phase G: assemble the final, substituted requests.
  const credMap = new Map<string, string>()   // envKey → suggestedValue (empty for secrets)
  const pageRequests = new Map<string, ParsedRequest[]>()

  for (const c of candidates) {
    if (!keptIdx.has(c.index)) continue

    const usageHere = matchesByUsageEntry.get(c.index) ?? []

    const segs = [...c.rawSegments]
    for (const m of usageHere.filter(m => m.usage.siteType === 'path')) {
      segs[parseInt(m.usage.siteKey, 10)] = `{{${varNameByKey.get(`${m.sourceIdx}:${m.sourcePath}`)}}}`
    }
    const pathname = segs.join('/')

    const queryVarByKey = new Map<string, string>()
    for (const m of usageHere.filter(m => m.usage.siteType === 'query')) {
      queryVarByKey.set(m.usage.siteKey, varNameByKey.get(`${m.sourceIdx}:${m.sourcePath}`)!)
    }
    const queryParams = c.queryParams.length > 0
      ? c.queryParams.map(q => ({ key: q.key, value: q.value, varName: queryVarByKey.get(q.key) }))
      : undefined

    const bodyMatches = new Map<string, string>()
    for (const m of usageHere.filter(m => m.usage.siteType === 'body')) {
      bodyMatches.set(m.usage.siteKey, varNameByKey.get(`${m.sourceIdx}:${m.sourcePath}`)!)
    }

    let bodyStr: string | undefined
    let bodyKind: 'json' | 'form' | undefined
    if (c.bodyObj && typeof c.bodyObj === 'object') {
      bodyStr = JSON.stringify(redactAndSubstituteBody(c.bodyObj, credMap, bodyMatches))
      bodyKind = 'json'
    } else if (c.formParams) {
      // Same object shape as the JSON case (flat key -> value/placeholder) so it
      // flows through the same pyLiteral substitution at codegen time — only the
      // kwarg (json= vs data=) differs, chosen from bodyKind below.
      const formObj: Record<string, string> = {}
      for (const p of c.formParams) {
        if (HAR_CRED_FIELD.test(p.name)) {
          const envKey = toEnvKey(p.name)
          if (!credMap.has(envKey)) credMap.set(envKey, '')
          formObj[p.name] = `<${envKey}>`
        } else if (bodyMatches.has(p.name)) {
          formObj[p.name] = `<${bodyMatches.get(p.name)}>`
        } else {
          formObj[p.name] = String(p.value ?? '').slice(0, 200)
        }
      }
      bodyStr = JSON.stringify(formObj)
      bodyKind = 'form'
    } else if (c.bodyRawFallback) {
      bodyStr = JSON.stringify(c.bodyRawFallback)
      bodyKind = 'json'
    }

    const isCredentialSubmit = c.method === 'POST' && Boolean(
      hasPasswordField(c.bodyObj)
      || (c.formParams && c.formParams.some((p: any) => HAR_PASSWORD_FIELD.test(p.name)))
    )

    const sourceMatches = matchesBySourceEntry.get(c.index) ?? []
    const captureVarPaths = new Map<string, string>()
    for (const m of sourceMatches) captureVarPaths.set(varNameByKey.get(`${m.sourceIdx}:${m.sourcePath}`)!, m.sourcePath)
    const captures = Array.from(captureVarPaths.entries()).map(([varName, fieldPath]) => ({ varName, fieldPath }))

    const headerCaptures: { headerName: string; varName: string }[] = []
    const seenHeaderVar = new Set<string>()
    for (const m of sourceMatches.filter(m => m.usage.siteType === 'header')) {
      const varName = varNameByKey.get(`${m.sourceIdx}:${m.sourcePath}`)!
      const key = `${m.usage.siteKey}:${varName}`
      if (!seenHeaderVar.has(key)) { seenHeaderVar.add(key); headerCaptures.push({ headerName: m.usage.siteKey, varName }) }
    }

    const pr: ParsedRequest = {
      method: c.method,
      origin: c.origin,
      pathname,
      displayPath: c.displayPath,
      status: c.status,
      queryParams,
      bodyStr,
      bodyKind,
      isCredentialSubmit,
      captures: captures.length ? captures : undefined,
      headerCaptures: headerCaptures.length ? headerCaptures : undefined,
    }

    if (!pageRequests.has(c.pageRef)) pageRequests.set(c.pageRef, [])
    pageRequests.get(c.pageRef)!.push(pr)
  }

  // Assemble HAR pages in order
  const harPageList: ParsedPage[] = []
  for (const p of harPages) {
    const reqs = pageRequests.get(p.id)
    if (reqs && reqs.length > 0) harPageList.push({ name: pageNames.get(p.id) || p.id, requests: reqs })
  }
  const unknownReqs = pageRequests.get('_unknown')
  if (unknownReqs && unknownReqs.length > 0) harPageList.push({ name: 'Other', requests: unknownReqs })

  const allReqs: ParsedRequest[] = harPageList.flatMap(p => p.requests)
  const totalRequests = allReqs.length

  // SPAs produce only 1 HAR page for every navigation — group by URL pattern
  function logicalGroup(pathname: string): string {
    if (pathname === '/' || /^\/api\/(ready|health|ping)/.test(pathname) || /^\/api\/auth/.test(pathname)) return 'Login'
    const m = pathname.match(/^\/api\/([^/?]+)/)
    if (m) return m[1].charAt(0).toUpperCase() + m[1].slice(1)
    return 'Other'
  }

  let summaryPages: ParsedPage[]
  if (harPageList.length <= 1 && allReqs.length > 0) {
    // SPA: derive logical pages from URL patterns
    const groupOrder: string[] = []
    const groupMap = new Map<string, ParsedRequest[]>()
    for (const r of allReqs) {
      const g = logicalGroup(r.pathname)
      if (!groupMap.has(g)) { groupMap.set(g, []); groupOrder.push(g) }
      groupMap.get(g)!.push(r)
    }
    summaryPages = groupOrder.map(name => ({ name, requests: groupMap.get(name)! }))
  } else {
    summaryPages = harPageList
  }

  // Build compact summary (for display/debug; deterministic generator uses `pages` directly)
  const totalCaptures = allReqs.reduce((n, r) => n + (r.captures?.length ?? 0), 0)
  const lines: string[] = [
    `HAR summary — ${summaryPages.length} logical pages, ${totalRequests} requests (static assets and trackers filtered out; every request keeps its own explicit URL)`,
    '',
  ]
  if (totalCaptures > 0) {
    lines.push(`Detected ${totalCaptures} dynamic value(s) (session ids, tokens, ...) captured from a response and reused in a later request — see generated script for extraction code.`)
  }
  const credKeyList = Array.from(credMap.keys())
  if (credKeyList.length > 0) {
    lines.push(`Credential env vars (from POST body fields): ${credKeyList.join(', ')}`)
    lines.push(`Use os.environ.get() for these — values injected at runtime.`)
  }
  lines.push('')
  for (let i = 0; i < summaryPages.length; i++) {
    const page = summaryPages[i]
    lines.push(`## Page ${i + 1}: ${page.name}`)
    for (const r of page.requests) {
      lines.push(`${r.method} ${r.displayPath} -> ${r.status}`)
      if (r.bodyStr) lines.push(`  Body: ${r.bodyStr}`)
      if (r.captures?.length) lines.push(`  Captures: ${r.captures.map(c => c.varName).join(', ')}`)
    }
    lines.push('')
  }

  // Build credential suggestions for UI
  const credSuggestions: CredSuggestion[] = []
  for (const k of credMap.keys()) credSuggestions.push({ key: k, value: '' })

  return {
    pageCount: summaryPages.length,
    requestCount: totalRequests,
    credSuggestions,
    summary: lines.join('\n'),
    pages: summaryPages,
  }
}

// ── Deterministic script generator ────────────────────────────────────────────

function pyLiteral(v: any): string {
  if (v === null || v === undefined) return 'None'
  if (typeof v === 'string') {
    if (/^<[A-Za-z_][A-Za-z0-9_]*>$/.test(v)) return v.slice(1, -1)   // bare identifier: env var or captured var
    // JSON.stringify's escaping (\\, \", \n, \r, \t, \uXXXX, ...) is a strict
    // subset of Python double-quoted string escape syntax, so it's also a
    // correct Python literal — unlike hand-escaping just backslash/quote,
    // this is safe for values containing raw newlines or other control
    // characters (e.g. multi-line/NDJSON bodies captured as raw fallback text).
    return JSON.stringify(v)
  }
  if (typeof v === 'number' || typeof v === 'boolean') return JSON.stringify(v)
  if (Array.isArray(v)) return `[${v.map(pyLiteral).join(', ')}]`
  if (typeof v === 'object') return `{${Object.entries(v).map(([k, vv]) => `"${k}": ${pyLiteral(vv)}`).join(', ')}}`
  return JSON.stringify(v)
}

function bodyToPythonLiteral(bodyStr: string): string {
  try { return pyLiteral(JSON.parse(bodyStr)) } catch { return JSON.stringify(bodyStr) }
}

function queryDictLiteral(params: { key: string; value: string; varName?: string }[]): string {
  return `{${params.map(({ key, value, varName }) => `"${key}": ${varName ? varName : pyLiteral(value)}`).join(', ')}}`
}

function pyEscape(s: string): string {
  return s.replace(/\\/g, '\\\\').replace(/"/g, '\\"')
}

function buildUrlExpr(origin: string, pathname: string): string {
  const escaped = pyEscape(origin + pathname)
  if (escaped.includes('{{')) return `f"${escaped.replace(/\{\{/g, '{').replace(/\}\}/g, '}')}"`
  return `"${escaped}"`
}

function extractExpr(fieldPath: string): string {
  if (fieldPath.startsWith('__csrf__:')) {
    const [, tag, fieldName] = fieldPath.split(':')
    const attr = tag === 'meta' ? 'content' : 'value'
    const pattern = `<${tag}\\b(?=[^>]*\\bname=["']${fieldName}["'])(?=[^>]*\\b${attr}=["']([^"']*)["'])`
    return `(m.group(1) if (m := re.search(r"""${pattern}""", resp.text)) else None)`
  }
  let expr = 'resp.json()'
  for (const part of fieldPath.split('.')) expr = `(${expr} or {}).get("${part}")`
  return expr
}

function generateScriptDeterministically(
  pages: ParsedPage[],
  credKeys: string[],
): string {
  const L: string[] = []
  const p = (line: string) => L.push(line)

  p('import sys, os, time, re')
  p('')
  p('try:')
  p('    import httpx')
  p('except ImportError:')
  p('    import subprocess')
  p('    subprocess.run([sys.executable, "-m", "pip", "install", "httpx", "--quiet"], check=True)')
  p('    import httpx')
  p('')

  for (const key of credKeys) p(`${key} = os.environ.get("${key}")`)
  p('')

  p('def main():')
  if (credKeys.length > 0) {
    const kvPairs = credKeys.map(k => `"${k}": ${k}`).join(', ')
    p(`    missing = [k for k, v in {${kvPairs}}.items() if not v]`)
    p('    if missing:')
    p(`        print(f"RESULT : FAIL -- required env vars not set: {', '.join(missing)}")`)
    p('        sys.exit(1)')
    p('')
  }

  p(`    total_pages = ${pages.length}`)
  p('    total_pages_passed = 0')
  p('    client = httpx.Client(verify=False, timeout=8, follow_redirects=True)')
  p('')

  for (let i = 0; i < pages.length; i++) {
    const page = pages[i]
    const assertPattern = page.bodyPattern?.trim() ?? ''
    // First password-bearing POST in the page is treated as the login step,
    // regardless of its URL — not every site's login endpoint is /api/auth/login.
    const loginReq = page.requests.find(r => r.isCredentialSubmit)

    p(`    print(f"Start Page ${i + 1}: ${pyEscape(page.name)}")`)
    p('    try:')
    p('        page_start = time.time()')
    p('        page_responses = []')
    if (assertPattern) p('        page_bodies = []')
    p('')

    for (const req of page.requests) {
      const urlExpr = buildUrlExpr(req.origin, req.pathname)
      const kwargs: string[] = []
      if (req.queryParams?.length) kwargs.push(`params=${queryDictLiteral(req.queryParams)}`)
      if (req.bodyStr) kwargs.push(`${req.bodyKind === 'form' ? 'data' : 'json'}=${bodyToPythonLiteral(req.bodyStr)}`)
      const method = req.method.toLowerCase()
      const callExpr = `client.${method}(${urlExpr}${kwargs.length ? ', ' + kwargs.join(', ') : ''})`
      const methodLabel = req.method.toUpperCase().padEnd(5)

      p(`        t0 = time.time()`)
      p(`        resp = ${callExpr}`)
      p(`        elapsed = int((time.time() - t0) * 1000)`)
      p(`        print(f"  ${methodLabel} ${pyEscape(req.displayPath)}  [{resp.status_code}]  {elapsed}ms")`)
      p(`        page_responses.append(("${req.method}", "${pyEscape(req.displayPath)}", resp.status_code))`)
      if (assertPattern) p(`        page_bodies.append(resp.text)`)

      for (const cap of req.captures ?? []) {
        p(`        ${cap.varName} = ${extractExpr(cap.fieldPath)}`)
      }
      for (const hc of req.headerCaptures ?? []) {
        p(`        if ${hc.varName}:`)
        p(`            client.headers["${hc.headerName}"] = str(${hc.varName})`)
      }

      if (req === loginReq) {
        // Hard-fail on a bad login status regardless of auth style. A bearer-token
        // API and a session-cookie site both count "login errored" the same way;
        // what differs is what happens next, handled below.
        p(`        if resp.status_code >= 300:`)
        p(`            print(f"End Page ${i + 1} - FAILED -- login returned {resp.status_code}: {resp.text[:300]}")`)
        p(`            sys.exit(1)`)
        // Bearer-token APIs return JSON with a token to carry forward; session-cookie
        // sites return HTML/redirect with no token — that's expected, not a failure,
        // since httpx.Client's cookie jar already carries the session forward.
        p(`        try:`)
        p(`            _login_json = resp.json()`)
        p(`            _token = _login_json.get("access_token") or _login_json.get("token") or (_login_json.get("data") or {}).get("access_token")`)
        p(`            if _token:`)
        p(`                client.headers["Authorization"] = f"Bearer {_token}"`)
        p(`        except Exception:`)
        p(`            pass  # not a JSON/bearer-token response -- session-cookie auth`)
      }

      p('')
    }

    p(`        page_time = int((time.time() - page_start) * 1000)`)
    p(`        if all(r[2] < 300 for r in page_responses):`)
    if (assertPattern) {
      p(`            assert_pattern = ${JSON.stringify(assertPattern)}`)
      p(`            assert_ok = any(re.search(assert_pattern, b, re.IGNORECASE) for b in page_bodies)`)
      p(`            print(f"  Assert {assert_pattern!r} ... {'found' if assert_ok else 'NOT FOUND'}")`)
      p(`            if not assert_ok:`)
      p(`                print(f"End Page ${i + 1} - FAILED -- no response matched the assertion")`)
      p(`                sys.exit(1)`)
    }
    p(`            print(f"End Page ${i + 1} - PASSED ({page_time}ms)")`)
    p(`            total_pages_passed += 1`)
    p(`        else:`)
    p(`            bad = next(r for r in page_responses if r[2] >= 300)`)
    p(`            print(f"End Page ${i + 1} - FAILED -- {bad[0]} {bad[1]} returned {bad[2]}")`)
    p('')
    p(`    except httpx.TimeoutException as e:`)
    p(`        print(f"End Page ${i + 1} - FAILED -- request timed out: {e.request.url}")`)
    p(`        sys.exit(1)`)
    p(`    except Exception as e:`)
    p(`        print(f"End Page ${i + 1} - FAILED -- {type(e).__name__}: {e}")`)
    p(`        sys.exit(1)`)
    p('')
  }

  p(`    print("-" * 40)`)
  p(`    if total_pages_passed == total_pages:`)
  p(`        print(f"RESULT : PASS -- {total_pages_passed}/{total_pages} pages passed")`)
  p(`    else:`)
  p(`        print(f"RESULT : FAIL -- {total_pages_passed}/{total_pages} pages passed")`)
  p(`        sys.exit(1)`)
  p('')
  p('')
  p('if __name__ == "__main__":')
  p('    main()')

  return L.join('\n')
}

// ── Types ─────────────────────────────────────────────────────────────────────

type ModalStep = 'details' | 'generate' | 'test' | 'done'

interface CredentialPair {
  key: string
  value: string
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function statusBadge(status: string | null) {
  if (!status) return <span style={{ color: DS.txtS, fontSize: '0.78rem' }}>—</span>
  const colors: Record<string, string> = { pass: DS.green, fail: DS.red, error: DS.yellow }
  const color = colors[status] ?? DS.txtS
  return (
    <span style={{
      padding: '2px 8px', borderRadius: 20,
      backgroundColor: color + '22', color,
      fontSize: '0.74rem', fontWeight: 700, textTransform: 'uppercase',
    }}>
      {status}
    </span>
  )
}

function relativeTime(iso: string | null) {
  if (!iso) return '—'
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

function credsToDict(pairs: CredentialPair[]): Record<string, string> {
  const result: Record<string, string> = {}
  for (const { key, value } of pairs) {
    if (key.trim()) result[key.trim()] = value
  }
  return result
}

function dictToPairs(dict: Record<string, string>): CredentialPair[] {
  return Object.entries(dict).map(([key, value]) => ({ key, value }))
}

// ── Main component ────────────────────────────────────────────────────────────

export default function SyntheticsPage() {
  const [monitors, setMonitors]   = useState<SyntheticMonitor[]>([])
  const [loading, setLoading]     = useState(true)
  const [error, setError]         = useState<string | null>(null)
  const [showModal, setShowModal] = useState(false)
  const [editing, setEditing]     = useState<SyntheticMonitor | null>(null)
  const [outputModal, setOutputModal] = useState<{ name: string; output: string } | null>(null)

  const refresh = async () => {
    try {
      const res = await listSyntheticMonitors()
      setMonitors(res.data)
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Failed to load monitors')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { refresh() }, [])

  const handleDelete = async (mon: SyntheticMonitor) => {
    if (!confirm(`Delete monitor "${mon.name}"?`)) return
    await deleteSyntheticMonitor(mon.id)
    refresh()
  }

  const handleToggle = async (mon: SyntheticMonitor) => {
    await updateSyntheticMonitor(mon.id, { enabled: !mon.enabled })
    refresh()
  }

  return (
    <div style={{ color: DS.txtP }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1.5rem' }}>
        <div>
          <h1 style={{ fontSize: '1.5rem', fontWeight: 700, margin: 0 }}>Synthetic Monitoring</h1>
          <p style={{ color: DS.txtS, fontSize: '0.875rem', margin: '4px 0 0' }}>
            Scripted user journey replay — detect failures before users do
          </p>
        </div>
        <button style={btn()} onClick={() => { setEditing(null); setShowModal(true) }}>
          + New Monitor
        </button>
      </div>

      {/* Error */}
      {error && (
        <div style={{ ...card, borderColor: DS.red, color: DS.red, padding: '0.75rem 1rem' }}>
          {error}
        </div>
      )}

      {/* Table */}
      {loading ? (
        <div style={{ color: DS.txtS, textAlign: 'center', padding: '3rem' }}>Loading…</div>
      ) : monitors.length === 0 ? (
        <div style={{ ...card, textAlign: 'center', padding: '3rem', color: DS.txtS }}>
          <div style={{ fontSize: '2rem', marginBottom: '0.5rem' }}>🔬</div>
          <div style={{ fontWeight: 600, color: DS.txtM }}>No synthetic monitors yet</div>
          <div style={{ fontSize: '0.875rem', marginTop: 4 }}>
            Click "New Monitor" to create your first scripted journey
          </div>
        </div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.875rem' }}>
            <thead>
              <tr style={{ borderBottom: `1px solid ${DS.border}` }}>
                {['Name', 'Schedule', 'Last Run', 'Status', 'Enabled', 'Actions'].map(h => (
                  <th key={h} style={{ padding: '10px 14px', textAlign: 'left', color: DS.txtS, fontWeight: 600, fontSize: '0.78rem', textTransform: 'uppercase' }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {monitors.map(mon => (
                <tr key={mon.id} style={{ borderBottom: `1px solid ${DS.border}22` }}>
                  <td style={{ padding: '12px 14px', fontWeight: 600 }}>
                    {mon.name}
                    {mon.har_filename && (
                      <div style={{ fontSize: '0.74rem', color: DS.txtS, marginTop: 2 }}>
                        HAR: {mon.har_filename}
                      </div>
                    )}
                  </td>
                  <td style={{ padding: '12px 14px', color: DS.txtM }}>
                    Every {mon.schedule_mins}m
                  </td>
                  <td style={{ padding: '12px 14px', color: DS.txtS }}>
                    {relativeTime(mon.last_run_at)}
                  </td>
                  <td style={{ padding: '12px 14px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      {statusBadge(mon.last_status)}
                      {mon.last_output && (
                        <button
                          style={{ ...outlineBtn, padding: '2px 8px', fontSize: '0.74rem' }}
                          onClick={() => setOutputModal({ name: mon.name, output: mon.last_output! })}
                        >
                          Log
                        </button>
                      )}
                    </div>
                  </td>
                  <td style={{ padding: '12px 14px' }}>
                    <button
                      style={{
                        width: 36, height: 20, borderRadius: 10, border: 'none', cursor: 'pointer',
                        backgroundColor: mon.enabled ? DS.mutedGreen : DS.border,
                        transition: 'background 0.2s',
                        position: 'relative',
                      }}
                      onClick={() => handleToggle(mon)}
                      title={mon.enabled ? 'Disable' : 'Enable'}
                    >
                      <span style={{
                        position: 'absolute', top: 3, left: mon.enabled ? 18 : 3,
                        width: 14, height: 14, borderRadius: '50%',
                        backgroundColor: '#fff', transition: 'left 0.2s',
                      }} />
                    </button>
                  </td>
                  <td style={{ padding: '12px 14px' }}>
                    <div style={{ display: 'flex', gap: 6 }}>
                      <button style={{ ...outlineBtn, padding: '5px 10px' }} onClick={() => { setEditing(mon); setShowModal(true) }} title="Edit">
                        ✎
                      </button>
                      <button style={{ ...outlineBtn, padding: '5px 10px', color: DS.red }} onClick={() => handleDelete(mon)} title="Delete">
                        ✕
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Create/Edit modal */}
      {showModal && (
        <MonitorModal
          monitor={editing}
          onClose={() => { setShowModal(false); refresh() }}
        />
      )}

      {/* Output viewer */}
      {outputModal && (
        <div style={overlay} onClick={() => setOutputModal(null)}>
          <div style={{ ...modal, maxWidth: '700px' }} onClick={e => e.stopPropagation()}>
            <div style={{ padding: '1rem 1.25rem', borderBottom: `1px solid ${DS.border}`, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ fontWeight: 700 }}>{outputModal.name} — Last Output</span>
              <button style={outlineBtn} onClick={() => setOutputModal(null)}><IconX size={14} /></button>
            </div>
            <pre style={{
              padding: '1.25rem', margin: 0, overflowY: 'auto', maxHeight: '70vh',
              fontSize: '0.78rem', lineHeight: 1.6, color: DS.txtM,
              backgroundColor: DS.bg, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
            }}>
              {outputModal.output}
            </pre>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Create / Edit Modal ───────────────────────────────────────────────────────

export function MonitorModal({
  monitor,
  onClose,
}: {
  monitor: SyntheticMonitor | null
  onClose: () => void
}) {
  const isEdit = !!monitor

  // Details state
  const [name, setName]             = useState(monitor?.name ?? '')
  const [scheduleMins, setSchedule] = useState(String(monitor?.schedule_mins ?? 15))
  const [creds, setCreds]           = useState<CredentialPair[]>(
    monitor?.credentials ? dictToPairs(monitor.credentials) : []
  )

  // HAR / script state
  const [step, setStep]             = useState<ModalStep>(isEdit && monitor?.script ? 'done' : 'details')
  const [harFilename, setHarFilename] = useState(monitor?.har_filename ?? '')
  const [parsedPages, setParsedPages] = useState<ParsedPage[]>((monitor?.pages as ParsedPage[]) ?? [])
  const [parsedInfo, setParsedInfo]   = useState<{ pages: number; reqs: number } | null>(
    monitor?.pages?.length
      ? { pages: monitor.pages.length, reqs: monitor.pages.reduce((n: number, pg: ParsedPage) => n + pg.requests.length, 0) }
      : null
  )
  const [script, setScript]           = useState(monitor?.script ?? '')
  const [generating, setGenerating]   = useState(false)
  const [testing, setTesting]         = useState(false)
  const [testOutput, setTestOutput]   = useState('')
  const [testStatus, setTestStatus]   = useState<string | null>(null)
  const [genError, setGenError]       = useState<string | null>(null)
  const [saving, setSaving]           = useState(false)
  const [showAssertions, setShowAssertions] = useState(false)

  const fileRef = useRef<HTMLInputElement>(null)

  const handleHarFile = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setHarFilename(file.name)
    const reader = new FileReader()
    reader.onload = ev => {
      const content = ev.target?.result as string ?? ''
      const result = parseHar(content)
      setParsedPages(result.pages)
      setParsedInfo({ pages: result.pageCount, reqs: result.requestCount })

      // Merge extracted cred suggestions without overwriting filled values
      setCreds(existing => {
        const existingKeys = new Set(existing.map(p => p.key))
        const newPairs = result.credSuggestions.filter(s => !existingKeys.has(s.key))
        return [...existing, ...newPairs]
      })
    }
    reader.readAsText(file)
  }

  const handleGenerate = () => {
    if (!parsedPages.length) {
      setGenError('Upload a HAR file on the Details tab first.')
      return
    }
    setGenError(null)
    const credKeys = creds.map(c => c.key).filter(Boolean)
    const generated = generateScriptDeterministically(parsedPages, credKeys)
    setScript(generated)
    setStep('test')
    setTestOutput('')
    setTestStatus(null)
  }

  const handleTest = async () => {
    // Warn if any secret credential key has an empty value
    const emptySecrets = creds.filter(
      p => p.key && !p.value && /password|passwd|secret|token|key/i.test(p.key)
    )
    if (emptySecrets.length > 0) {
      setTestOutput(
        `Missing credential values for: ${emptySecrets.map(p => p.key).join(', ')}\n` +
        `Go to the Details tab and fill in the values before testing.`
      )
      setTestStatus('error')
      return
    }
    setTesting(true)
    setTestOutput('')
    setTestStatus(null)
    try {
      const res = await testSyntheticScript({ script, credentials: credsToDict(creds) })
      setTestOutput(res.data.output)
      setTestStatus(res.data.status)
      if (res.data.status === 'pass') setStep('done')
    } catch (e: any) {
      setTestOutput(e?.response?.data?.detail ?? 'Test request failed')
      setTestStatus('error')
    } finally {
      setTesting(false)
    }
  }

  const handleFixWithAI = async () => {
    setGenerating(true)
    setGenError(null)
    try {
      const res = await generateSyntheticScript({ current_script: script, error_output: testOutput })
      setScript(res.data.script)
      setStep('test')
      setTestOutput('')
      setTestStatus(null)
    } catch (e: any) {
      setGenError(e?.response?.data?.detail ?? 'Fix failed — check Settings > LLM')
    } finally {
      setGenerating(false)
    }
  }

  const handleSave = async () => {
    if (!name.trim()) { alert('Name is required'); return }
    setSaving(true)
    try {
      const payload = {
        name: name.trim(),
        har_filename: harFilename || undefined,
        script: script || undefined,
        pages: parsedPages.length ? parsedPages : undefined,
        credentials: credsToDict(creds),
        schedule_mins: parseInt(scheduleMins) || 15,
        enabled: true,
      }
      if (isEdit && monitor) {
        await updateSyntheticMonitor(monitor.id, payload)
      } else {
        await createSyntheticMonitor(payload)
      }
      onClose()
    } catch (e: any) {
      alert(e?.response?.data?.detail ?? 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const updatePageAssertion = (idx: number, pattern: string) =>
    setParsedPages(prev => prev.map((pg, i) => i === idx ? { ...pg, bodyPattern: pattern } : pg))

  const addCred = () => setCreds(c => [...c, { key: '', value: '' }])
  const removeCred = (i: number) => setCreds(c => c.filter((_, idx) => idx !== i))
  const updateCred = (i: number, field: 'key' | 'value', val: string) =>
    setCreds(c => c.map((p, idx) => idx === i ? { ...p, [field]: val } : p))

  const canSave = name.trim() && (script || !parsedPages.length)

  const tabs: { id: ModalStep; label: string }[] = [
    { id: 'details', label: '1 Details' },
    { id: 'generate', label: '2 Generate' },
    { id: 'test', label: '3 Test' },
  ]

  return (
    <div style={overlay}>
      <div style={modal} onClick={e => e.stopPropagation()}>
        {/* Modal header */}
        <div style={{
          padding: '1rem 1.25rem',
          borderBottom: `1px solid ${DS.border}`,
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        }}>
          <span style={{ fontWeight: 700, fontSize: '1rem' }}>
            {isEdit ? `Edit — ${monitor!.name}` : 'New Synthetic Monitor'}
          </span>
          <button style={outlineBtn} onClick={onClose}><IconX size={14} /></button>
        </div>

        {/* Tabs */}
        <div style={{ display: 'flex', gap: 0, borderBottom: `1px solid ${DS.border}` }}>
          {tabs.map(t => {
            const reachable = t.id === 'details' || t.id === 'generate' || (t.id === 'test' && (step === 'test' || step === 'done'))
            const active = step === t.id || (step === 'done' && t.id === 'test')
            return (
              <button
                key={t.id}
                style={{
                  padding: '10px 20px',
                  border: 'none',
                  borderBottom: active ? `2px solid ${DS.accent}` : '2px solid transparent',
                  backgroundColor: 'transparent',
                  color: active ? DS.accent : reachable ? DS.txtM : DS.txtS,
                  fontWeight: active ? 700 : 500,
                  fontSize: '0.85rem',
                  cursor: reachable ? 'pointer' : 'default',
                }}
                onClick={() => {
                  if (!reachable) return
                  if (t.id === 'generate' && !parsedPages.length && isEdit && script) setStep('test')
                  else setStep(t.id)
                }}
              >
                {t.label}
              </button>
            )
          })}
          {(step === 'done' || (isEdit && script)) && (
            <span style={{ padding: '10px 16px', color: DS.green, fontSize: '0.85rem', display: 'flex', alignItems: 'center', gap: 4 }}>
              <IconCheck size={14} /> Script ready
            </span>
          )}
        </div>

        {/* Tab body */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '1.25rem' }}>

          {/* ── Step 1: Details ── */}
          {step === 'details' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>

              {/* HAR upload — first so we can auto-extract credential keys */}
              <div>
                <label style={label}>HAR File</label>
                <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                  <button style={outlineBtn} onClick={() => fileRef.current?.click()}>
                    <IconFileText size={14} /> {harFilename ? 'Change file' : 'Choose HAR file'}
                  </button>
                  <span style={{ color: harFilename ? DS.txtM : DS.txtS, fontSize: '0.85rem' }}>
                    {harFilename || 'No file selected'}
                  </span>
                  <input ref={fileRef} type="file" accept=".har,application/json" style={{ display: 'none' }} onChange={handleHarFile} />
                </div>
                {parsedInfo ? (
                  <div style={{
                    marginTop: 8, padding: '8px 12px', borderRadius: 6,
                    backgroundColor: DS.green + '15', border: `1px solid ${DS.green}40`,
                    fontSize: '0.82rem', color: DS.green, display: 'flex', alignItems: 'center', gap: 6,
                  }}>
                    <IconCheck size={13} /> Parsed: {parsedInfo.pages} pages, {parsedInfo.reqs} API requests
                    {creds.length > 0 && ` — ${creds.length} credential key${creds.length > 1 ? 's' : ''} extracted below`}
                  </div>
                ) : (
                  <p style={{ color: DS.txtS, fontSize: '0.78rem', margin: '6px 0 0' }}>
                    Upload a HAR to auto-detect credential keys and generate the script.
                    Record in Chrome DevTools → Network → Export as HAR with content.
                  </p>
                )}
              </div>

              {/* Page Assertions — always visible so it stays discoverable/editable even when
                  no HAR has been (re)parsed yet (e.g. a monitor saved before this feature existed) */}
              <div>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: (showAssertions && parsedPages.length > 0) ? 10 : 0 }}>
                  <div>
                    <span style={label}>Page Assertions <span style={{ color: DS.txtS, fontWeight: 400, fontSize: '0.72rem', textTransform: 'none', letterSpacing: 0 }}>(optional)</span></span>
                    <p style={{ color: DS.txtS, fontSize: '0.75rem', margin: '2px 0 0' }}>
                      Verify each page's responses contain expected content — any response in the page must match.
                    </p>
                  </div>
                  {parsedPages.length > 0 && (
                    <button
                      style={{ ...outlineBtn, padding: '4px 10px', fontSize: '0.72rem', flexShrink: 0, alignSelf: 'flex-start' }}
                      onClick={() => setShowAssertions(x => !x)}
                    >
                      {showAssertions ? 'Hide' : 'Show'}
                    </button>
                  )}
                </div>
                {parsedPages.length === 0 ? (
                  <div style={{ color: DS.txtS, fontSize: '0.82rem', padding: '8px 0' }}>
                    No page data yet — upload the HAR file above to detect pages and add assertions.
                  </div>
                ) : showAssertions && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {parsedPages.map((page, i) => (
                      <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        <div
                          title={page.name}
                          style={{
                            flexShrink: 0, width: 190, fontSize: '0.78rem',
                            fontFamily: 'monospace',
                            color: DS.txtM, fontWeight: 600,
                            padding: '7px 10px', borderRadius: 7,
                            backgroundColor: DS.raised, border: `1px solid ${DS.border}`,
                            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                            cursor: 'default',
                          }}
                        >
                          <span style={{ color: DS.txtS, fontWeight: 400 }}>{i + 1}.</span> {shortPagePath(page.name)}
                        </div>
                        <input
                          style={{ ...input, fontFamily: 'monospace', fontSize: '0.78rem' }}
                          placeholder={`body must match (regex)  e.g. "items":\\[`}
                          value={page.bodyPattern ?? ''}
                          onChange={e => updatePageAssertion(i, e.target.value)}
                        />
                        {page.bodyPattern && (
                          <button
                            style={{ ...outlineBtn, padding: '5px 8px', flexShrink: 0 }}
                            onClick={() => updatePageAssertion(i, '')}
                            title="Clear"
                          >
                            <IconX size={12} />
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Credentials — auto-populated from HAR, editable */}
              <div>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                  <span style={label}>Credentials (injected as env vars)</span>
                  <button style={{ ...outlineBtn, padding: '4px 10px', fontSize: '0.78rem' }} onClick={addCred}>+ Add</button>
                </div>
                {creds.length === 0 ? (
                  <div style={{ color: DS.txtS, fontSize: '0.82rem', padding: '8px 0' }}>
                    Upload a HAR file above to auto-detect required keys, or add manually.
                  </div>
                ) : (
                  <>
                    <div style={{ display: 'flex', gap: 8, marginBottom: 4 }}>
                      <span style={{ ...label, flex: 1, marginBottom: 0 }}>Key</span>
                      <span style={{ ...label, flex: 2, marginBottom: 0 }}>Value</span>
                      <span style={{ width: 36 }} />
                    </div>
                    {creds.map((pair, i) => (
                      <div key={i} style={{ display: 'flex', gap: 8, marginBottom: 6 }}>
                        <input style={{ ...input, flex: 1, fontFamily: 'monospace', fontSize: '0.82rem' }}
                          placeholder="ENV_KEY" value={pair.key}
                          onChange={e => updateCred(i, 'key', e.target.value)} />
                        <input style={{ ...input, flex: 2 }}
                          placeholder="value"
                          type={/password|passwd|secret|token|key/i.test(pair.key) ? 'password' : 'text'}
                          value={pair.value}
                          onChange={e => updateCred(i, 'value', e.target.value)} />
                        <button style={{ ...outlineBtn, padding: '5px 10px', color: DS.red }} onClick={() => removeCred(i)}><IconX size={12} /></button>
                      </div>
                    ))}
                  </>
                )}
              </div>

              {/* Name and Schedule */}
              <div>
                <label style={label}>Monitor Name *</label>
                <input style={input} value={name} onChange={e => setName(e.target.value)} placeholder="Login Flow — Production" />
              </div>
              <div>
                <label style={label}>Run every (minutes)</label>
                <input style={{ ...input, width: 120 }} type="number" min={1} max={10080}
                  value={scheduleMins} onChange={e => setSchedule(e.target.value)} />
              </div>

              <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                <button style={btn()} onClick={() => {
                  // If we're editing a monitor that already has a script and no new
                  // HAR was (re)parsed this session, there's nothing to generate —
                  // go straight to Test instead of making the user click through
                  // a "no HAR loaded" warning on the Generate tab.
                  if (!parsedPages.length && isEdit && script) setStep('test')
                  else setStep('generate')
                }}>Next: Generate Script →</button>
              </div>
            </div>
          )}

          {/* ── Step 2: Generate ── */}
          {step === 'generate' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>

              {/* Summary of what was parsed */}
              {parsedInfo ? (
                <div style={{
                  padding: '10px 14px', borderRadius: 7,
                  backgroundColor: DS.raised, border: `1px solid ${DS.border}`,
                  fontSize: '0.85rem', color: DS.txtM,
                }}>
                  <div style={{ fontWeight: 600, color: DS.txtP, marginBottom: 4 }}>
                    {harFilename}
                  </div>
                  <div>{parsedInfo.pages} pages · {parsedInfo.reqs} API requests · {creds.length} credential vars</div>
                  {creds.length > 0 && (
                    <div style={{ marginTop: 6, fontFamily: 'monospace', fontSize: '0.78rem', color: DS.txtS }}>
                      {creds.map(c => c.key).join(', ')}
                    </div>
                  )}
                </div>
              ) : (
                <div style={{
                  padding: '10px 14px', borderRadius: 7,
                  backgroundColor: DS.yellow + '15', border: `1px solid ${DS.yellow}40`,
                  fontSize: '0.85rem', color: DS.yellow,
                }}>
                  No HAR loaded — go back to Details and upload a HAR file first.
                </div>
              )}

              {genError && (
                <div style={{ padding: '10px 14px', borderRadius: 7, backgroundColor: DS.red + '15', border: `1px solid ${DS.red}40`, color: DS.red, fontSize: '0.85rem' }}>
                  {genError}
                </div>
              )}

              <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                <button
                  style={btn(!parsedPages.length ? DS.border : DS.accent)}
                  disabled={!parsedPages.length}
                  onClick={handleGenerate}
                >
                  <IconBolt size={13} /> Generate Script
                </button>
                {isEdit && script && (
                  <button style={outlineBtn} onClick={() => setStep('test')}>
                    Use existing script →
                  </button>
                )}
              </div>
            </div>
          )}

          {/* ── Step 3: Test (and Done) ── */}
          {(step === 'test' || step === 'done') && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
              {/* Script editor */}
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                  <span style={label}>Generated Script</span>
                  <button
                    style={{
                      ...outlineBtn, padding: '3px 10px', fontSize: '0.74rem',
                      opacity: parsedPages.length ? 1 : 0.5,
                      cursor: parsedPages.length ? 'pointer' : 'not-allowed',
                    }}
                    disabled={!parsedPages.length}
                    title={parsedPages.length ? 'Rebuild the script from the parsed HAR pages/assertions' : 'Re-upload the HAR file on the Details tab to enable regenerate'}
                    onClick={handleGenerate}
                  >
                    Regenerate
                  </button>
                </div>
                <textarea
                  style={{
                    ...input,
                    height: 260,
                    fontFamily: 'monospace',
                    fontSize: '0.75rem',
                    lineHeight: 1.6,
                    resize: 'vertical',
                    whiteSpace: 'pre',
                    overflowX: 'auto',
                  }}
                  value={script}
                  onChange={e => setScript(e.target.value)}
                  spellCheck={false}
                />
              </div>

              {/* Test controls */}
              <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                <button
                  style={btn(testing ? DS.border : DS.accent)}
                  disabled={testing || !script}
                  onClick={handleTest}
                >
                  {testing ? <><IconLoader size={13} /> Running…</> : <><IconTestPipe size={13} /> Test Script</>}
                </button>
                {(testStatus === 'fail' || testStatus === 'error') && (
                  <button
                    style={btn(generating ? DS.border : DS.yellow)}
                    disabled={generating}
                    onClick={handleFixWithAI}
                  >
                    {generating ? <><IconLoader size={13} /> Fixing…</> : <><IconTool size={13} /> Fix with AI</>}
                  </button>
                )}
              </div>

              {/* Test output */}
              {testOutput && (
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
                    <span style={label}>Test Output</span>
                    {testStatus && statusBadge(testStatus)}
                  </div>
                  <pre style={{
                    backgroundColor: DS.bg,
                    border: `1px solid ${testStatus === 'pass' ? DS.green + '40' : DS.red + '40'}`,
                    borderRadius: 7, padding: '12px 14px',
                    fontSize: '0.75rem', lineHeight: 1.6,
                    color: DS.txtM, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                    maxHeight: 260, overflowY: 'auto', margin: 0,
                  }}>
                    {testOutput}
                  </pre>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div style={{
          padding: '1rem 1.25rem',
          borderTop: `1px solid ${DS.border}`,
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        }}>
          <button style={outlineBtn} onClick={onClose}>Cancel</button>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            {step === 'done' && (
              <span style={{ fontSize: '0.78rem', color: DS.txtS }}>
                <IconCheck size={13} /> All tests passed
              </span>
            )}
            <button
              style={btn(saving ? DS.border : DS.accent)}
              disabled={saving || !canSave}
              onClick={handleSave}
            >
              {saving ? 'Saving…' : isEdit ? 'Save Changes' : 'Save Monitor'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
