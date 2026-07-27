/**
 * Analytics loader — Umami tracking script, injected only on hosts we've
 * opted in. Ships in every build but no-ops for hostnames not in the
 * ANALYTICS_HOSTS list, so bundling the code has zero effect on any
 * self-hosted install.
 *
 * Umami itself runs as a separate container on the same host, served at
 * `/analytics/`. The tracking snippet is small (~2KB), respects DNT,
 * uses no cookies, and doesn't send PII.
 *
 * To enable analytics on a new host: add its hostname to ANALYTICS_HOSTS
 * below AND deploy an Umami container reachable at /analytics/ on that
 * host (see docs/DEMO_DEPLOY.md — TODO).
 */

// Hosts where analytics should load. Additive; blank list = disabled everywhere.
const ANALYTICS_HOSTS = new Set([
  'instance.axiometica.com',   // Oracle demo instance
])

// Website id in the Umami database — configured once when the site is
// created inside the Umami UI. Same value across all analytics-enabled
// hosts (they share one Umami instance if desired, or each host gets its
// own id). Left as a placeholder string until the Umami site is created;
// the loader still fires but Umami will reject unknown ids silently.
const UMAMI_WEBSITE_ID = 'REPLACE_WITH_UMAMI_SITE_ID'

export function initAnalytics(): void {
  if (typeof window === 'undefined') return
  if (!ANALYTICS_HOSTS.has(window.location.hostname)) return
  if (document.querySelector('script[data-umami]')) return   // already injected

  const script = document.createElement('script')
  script.async = true
  script.defer = true
  // Same-origin path — Umami is served through the platform's own nginx
  // under /analytics/ (see nginx demo overlay). Same-origin avoids CORS
  // and works even when the demo is behind a strict CSP.
  script.src = '/analytics/script.js'
  script.setAttribute('data-website-id', UMAMI_WEBSITE_ID)
  script.setAttribute('data-umami', 'true')
  document.head.appendChild(script)
}
