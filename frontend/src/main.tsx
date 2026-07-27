import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.tsx'
import './index.css'
import { initAnalytics } from './utils/analytics'

// Opt-in analytics loader — inert unless window.location.hostname is on
// the allow-list inside analytics.ts. See that file for how to enable
// tracking on a new deployment.
initAnalytics()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
