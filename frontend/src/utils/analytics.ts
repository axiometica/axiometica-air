/**
 * Analytics loader — Umami tracking script, injected only on hosts we've
 * opted in. Ships in every build but no-ops for hostnames not in the
 * ANALYTICS_HOSTS list, so bundling the code has zero effect on any
 * self-hosted install.
 *
 * Umami runs on its OWN subdomain (analytics.axiometica.com), not as a
 * sub-path of the platform. Reason: Umami's Next.js image references
 * assets at root (`/_next/...`) and its dashboard calls `/api/...` which
 * would collide with the platform's own `/api`. Serving Umami at its
 * own subdomain avoids all rewriting and CORS-adjacent headaches. The
 * tracking snippet still runs on the platform's page and posts events
 * cross-origin to the umami subdomain.
 */

// Hosts where analytics should load. Additive; blank list = disabled everywhere.
const ANALYTICS_HOSTS = new Set([
  'instance.axiometica.com',   // Oracle demo instance
  'demo.axiometica.com',       // Oracle demo marketing gateway
])

// Umami install this deployment reports to. Absolute origin — CORS is
// handled by Umami's script (it POSTs with mode: 'no-cors' to the URL
// under data-host-url). Leave the trailing bit off; the tracker appends
// /api/send itself.
const UMAMI_ORIGIN = 'https://analytics.axiometica.com'

// Website id in the Umami database — one per tracked property. Set at
// analytics.axiometica.com > Websites > "Axiometica AIR Demo". If the
// Umami install is redeployed and this id no longer exists, the tracker
// keeps firing but events are silently rejected — nothing else breaks.
const UMAMI_WEBSITE_ID = 'b7fc29cd-21c1-4c12-b7f7-84b38e7f0db8'

export function initAnalytics(): void {
  if (typeof window === 'undefined') return
  if (!ANALYTICS_HOSTS.has(window.location.hostname)) return
  if (document.querySelector('script[data-umami]')) return   // already injected

  const script = document.createElement('script')
  script.async = true
  script.defer = true
  script.src = `${UMAMI_ORIGIN}/script.js`
  script.setAttribute('data-website-id', UMAMI_WEBSITE_ID)
  // Explicit host-url so tracker knows where to POST events. Without
  // this the tracker defaults to the script's src origin, which happens
  // to be right here, but being explicit is more resilient to any
  // future refactor of where the script lives.
  script.setAttribute('data-host-url', UMAMI_ORIGIN)
  script.setAttribute('data-umami', 'true')
  document.head.appendChild(script)
}
