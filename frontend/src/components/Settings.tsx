import { useState, useEffect } from 'react'
import { version as APP_VERSION } from '../../package.json'
import { settingsService, AppSettings } from '../services/settingsService'
import {
  getRiskConfig, updateRiskConfig, resetRiskConfig, RiskConfigResponse,
  getStormSettings, updateStormSettings, resetStormSettings, StormSetting,
  getGeneralSettings, updateGeneralSettings, GeneralSetting,
  getSmtpSettings, updateSmtpSettings,
  getSlackSettings, updateSlackSettings, testSlackConnection, testSlackCredentials,
  getPlatformIntelligenceSettings, updatePlatformIntelligenceSettings,
  resetPlatformIntelligenceSettings, PlatformIntelligenceSetting,
  listEventTypeTaxonomy, EventTypeTaxonomyEntry,
} from '../services/api'
import { IconCheck, IconAlertTriangle } from './icons'
import LLMSettings from './LLMSettings'
import NotificationTeams from './NotificationTeams'

// ── Design tokens ──────────────────────────────────────────────────────────────
const DS = {
  bg:     '#0d1117',
  surface:'#1a1f2e',
  raised: '#252c3c',
  border: '#3d4557',
  txtP:   '#e8eef5',
  txtS:   '#7a8ba3',
  txtM:   '#a0aec0',
  accent: '#3b82f6',
}

const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: '6px 10px',
  borderRadius: 6,
  border: `1px solid ${DS.border}`,
  backgroundColor: DS.bg,
  color: DS.txtP,
  fontSize: '0.85rem',
  outline: 'none',
  boxSizing: 'border-box',
}

const primaryBtn: React.CSSProperties = {
  padding: '7px 18px',
  borderRadius: 7,
  border: 'none',
  backgroundColor: DS.accent,
  color: '#fff',
  fontSize: '0.82rem',
  fontWeight: 600,
  cursor: 'pointer',
}

const secondaryBtn: React.CSSProperties = {
  padding: '7px 18px',
  borderRadius: 7,
  border: `1px solid ${DS.border}`,
  backgroundColor: DS.raised,
  color: DS.txtP,
  fontSize: '0.82rem',
  fontWeight: 500,
  cursor: 'pointer',
}

// ── Component ──────────────────────────────────────────────────────────────────
export default function Settings() {
  // UI preferences (localStorage only — used for dark mode + sidebar)
  const [settings, setSettings] = useState<AppSettings | null>(null)

  const [expandedSections, setExpandedSections] = useState<Set<string>>(new Set(['environment']))
  const [saveSuccess, setSaveSuccess] = useState(false)

  // Risk Assessment config
  const [riskConfig, setRiskConfig] = useState<RiskConfigResponse | null>(null)
  const [riskConfigLoading, setRiskConfigLoading] = useState(false)
  const [riskConfigError, setRiskConfigError] = useState<string | null>(null)
  const [riskConfigSaving, setRiskConfigSaving] = useState(false)
  const [newOverrideCode, setNewOverrideCode] = useState('')
  const [newOverrideValue, setNewOverrideValue] = useState(1.0)
  const [eventTypeOptions, setEventTypeOptions] = useState<EventTypeTaxonomyEntry[]>([])

  // Storm Agent settings
  const [stormSettings, setStormSettings] = useState<StormSetting[]>([])
  const [stormLoading, setStormLoading] = useState(false)
  const [stormError, setStormError] = useState<string | null>(null)
  const [stormSaving, setStormSaving] = useState(false)
  const [stormEdits, setStormEdits] = useState<Record<string, any>>({})

  const [piSettings, setPiSettings] = useState<PlatformIntelligenceSetting[]>([])
  const [piLoading, setPiLoading] = useState(false)
  const [piError, setPiError] = useState<string | null>(null)
  const [piSaving, setPiSaving] = useState(false)
  const [piEdits, setPiEdits] = useState<Record<string, any>>({})

  // General settings (backend — wires Environment, Performance, Notifications, Security, Database)
  const [generalSettings, setGeneralSettings] = useState<GeneralSetting[]>([])
  const [generalEdits, setGeneralEdits] = useState<Record<string, any>>({})
  const [generalLoading, setGeneralLoading] = useState(false)
  const [generalSaving, setGeneralSaving] = useState(false)
  const [generalError, setGeneralError] = useState<string | null>(null)

  // SMTP settings (backend — wired in Notifications section)
  const [smtpEdits, setSmtpEdits] = useState<Record<string, any>>({})
  const [smtpLoading, setSmtpLoading] = useState(false)
  const [smtpSaving, setSmtpSaving] = useState(false)
  const [smtpError, setSmtpError] = useState<string | null>(null)

  // Slack ChatOps settings
  const [slackEdits, setSlackEdits] = useState<Record<string, any>>({})
  const [slackLoading, setSlackLoading] = useState(false)
  const [slackSaving, setSlackSaving] = useState(false)
  const [slackError, setSlackError] = useState<string | null>(null)
  const [slackTestResult, setSlackTestResult] = useState<{ ok: boolean; message: string } | null>(null)
  const [slackTesting, setSlackTesting] = useState(false)

  useEffect(() => {
    setSettings(settingsService.loadSettings())
    loadRiskConfig()
    loadStormSettings()
    loadGeneralSettings()
    loadSmtpSettings()
    loadSlackSettings()
    loadPiSettings()
    listEventTypeTaxonomy({ enabled_only: true })
      .then(res => setEventTypeOptions(res.data))
      .catch(() => { /* taxonomy fetch failure shouldn't block editing the rest of qualification settings */ })
  }, [])

  // Auto-persist UI preferences whenever they change (no Save button needed)
  useEffect(() => {
    if (settings) settingsService.saveSettings(settings)
  }, [settings])

  // ── General settings ─────────────────────────────────────────────────────────

  const loadGeneralSettings = async () => {
    try {
      setGeneralLoading(true)
      setGeneralError(null)
      const res = await getGeneralSettings()
      setGeneralSettings(res.data.settings)
      const initial: Record<string, any> = {}
      res.data.settings.forEach(s => { initial[s.key] = s.value })
      setGeneralEdits(initial)
    } catch {
      setGeneralError('Failed to load general settings')
    } finally {
      setGeneralLoading(false)
    }
  }

  const handleSaveGeneralSettings = async () => {
    try {
      setGeneralSaving(true)
      setGeneralError(null)
      // Strip the "general." prefix before sending to the API
      const payload: Record<string, any> = {}
      Object.entries(generalEdits).forEach(([k, v]) => {
        const shortKey = k.startsWith('general.') ? k.slice(8) : k
        payload[shortKey] = v
      })
      await updateGeneralSettings(payload)
      await loadGeneralSettings()
      setSaveSuccess(true)
      setTimeout(() => setSaveSuccess(false), 3000)
    } catch {
      setGeneralError('Failed to save general settings')
    } finally {
      setGeneralSaving(false)
    }
  }

  const updateGeneralEdit = (key: string, value: any) => {
    setGeneralEdits(prev => ({ ...prev, [key]: value }))
  }

  const generalVal = (key: string) => {
    if (key in generalEdits) return generalEdits[key]
    const s = generalSettings.find(r => r.key === key)
    return s ? s.value : undefined
  }

  // ── SMTP settings ─────────────────────────────────────────────────────────────

  const loadSmtpSettings = async () => {
    try {
      setSmtpLoading(true)
      const res = await getSmtpSettings()
      const initial: Record<string, any> = {}
      res.data.settings.forEach(s => { initial[s.key] = s.value })
      setSmtpEdits(initial)
    } catch {
      // SMTP endpoint requires auth — silently ignore on first load if not yet seeded
      setSmtpEdits({})
    } finally {
      setSmtpLoading(false)
    }
  }

  const handleSaveSmtpSettings = async () => {
    try {
      setSmtpSaving(true)
      setSmtpError(null)
      const payload: Record<string, any> = {}
      Object.entries(smtpEdits).forEach(([k, v]) => {
        const shortKey = k.startsWith('smtp.') ? k.slice(5) : k
        payload[shortKey] = v
      })
      await updateSmtpSettings(payload)
      await loadSmtpSettings()
      setSaveSuccess(true)
      setTimeout(() => setSaveSuccess(false), 3000)
    } catch {
      setSmtpError('Failed to save SMTP settings')
    } finally {
      setSmtpSaving(false)
    }
  }

  const updateSmtpEdit = (key: string, value: any) => {
    setSmtpEdits(prev => ({ ...prev, [key]: value }))
  }

  const smtpVal = (key: string) => {
    if (key in smtpEdits) return smtpEdits[key]
    return undefined
  }

  // ── Slack ChatOps settings ────────────────────────────────────────────────────

  const loadSlackSettings = async () => {
    try {
      setSlackLoading(true)
      const res = await getSlackSettings()
      const initial: Record<string, any> = {}
      res.data.settings.forEach(s => { initial[s.key] = s.value })
      setSlackEdits(initial)
    } catch {
      setSlackEdits({})
    } finally {
      setSlackLoading(false)
    }
  }

  const handleSaveSlackSettings = async () => {
    try {
      setSlackSaving(true)
      setSlackError(null)
      setSlackTestResult(null)
      const payload: Record<string, any> = {}
      Object.entries(slackEdits).forEach(([k, v]) => {
        const shortKey = k.startsWith('slack.') ? k.slice(6) : k
        payload[shortKey] = v
      })
      await updateSlackSettings(payload)
      await loadSlackSettings()
      setSaveSuccess(true)
      setTimeout(() => setSaveSuccess(false), 3000)
    } catch {
      setSlackError('Failed to save Slack settings')
    } finally {
      setSlackSaving(false)
    }
  }

  const handleTestSlack = async () => {
    try {
      setSlackTesting(true)
      setSlackTestResult(null)
      setSlackError(null)
      const botToken = slackVal('slack.bot_token')
      const channel = slackVal('slack.default_channel')
      // A freshly-typed (unsaved) token tests directly, without requiring a Save first.
      // If the field still holds the masked placeholder, fall back to the saved settings.
      if (botToken && botToken !== '••••••••') {
        const res = await testSlackCredentials({ bot_token: botToken, channel: channel || undefined })
        setSlackTestResult({ ok: true, message: res.data.message })
      } else {
        const res = await testSlackConnection()
        setSlackTestResult({ ok: true, message: res.data.message })
      }
    } catch (err: any) {
      const msg = err?.response?.data?.detail || 'Connection test failed'
      setSlackTestResult({ ok: false, message: msg })
    } finally {
      setSlackTesting(false)
    }
  }

  const updateSlackEdit = (key: string, value: any) => {
    setSlackEdits(prev => ({ ...prev, [key]: value }))
  }

  const slackVal = (key: string) => {
    if (key in slackEdits) return slackEdits[key]
    return undefined
  }

  // ── Risk config ──────────────────────────────────────────────────────────────

  const loadRiskConfig = async () => {
    try {
      setRiskConfigLoading(true)
      setRiskConfigError(null)
      const response = await getRiskConfig('default')
      setRiskConfig(response.data)
    } catch {
      setRiskConfigError('Failed to load risk configuration')
    } finally {
      setRiskConfigLoading(false)
    }
  }

  const handleSaveRiskConfig = async () => {
    if (!riskConfig) return
    try {
      setRiskConfigSaving(true)
      setRiskConfigError(null)
      const response = await updateRiskConfig('default', riskConfig.weights)
      setRiskConfig(response.data)
      setSaveSuccess(true)
      setTimeout(() => setSaveSuccess(false), 3000)
    } catch {
      setRiskConfigError('Failed to save risk configuration')
    } finally {
      setRiskConfigSaving(false)
    }
  }

  const handleResetRiskConfig = async () => {
    if (!window.confirm('Reset risk configuration to defaults? This cannot be undone.')) return
    try {
      setRiskConfigSaving(true)
      setRiskConfigError(null)
      const response = await resetRiskConfig('default')
      setRiskConfig(response.data)
      setSaveSuccess(true)
      setTimeout(() => setSaveSuccess(false), 3000)
    } catch {
      setRiskConfigError('Failed to reset risk configuration')
    } finally {
      setRiskConfigSaving(false)
    }
  }

  const updateWeightValue = (path: string[], value: any) => {
    if (!riskConfig) return
    const newWeights = JSON.parse(JSON.stringify(riskConfig.weights))
    let current = newWeights
    for (let i = 0; i < path.length - 1; i++) {
      if (!current[path[i]]) current[path[i]] = {}
      current = current[path[i]]
    }
    current[path[path.length - 1]] = value
    setRiskConfig({ ...riskConfig, weights: newWeights })
  }

  const deleteWeightKey = (path: string[], key: string) => {
    if (!riskConfig) return
    const newWeights = JSON.parse(JSON.stringify(riskConfig.weights))
    let current = newWeights
    for (const k of path) {
      if (!current[k]) return
      current = current[k]
    }
    delete current[key]
    setRiskConfig({ ...riskConfig, weights: newWeights })
  }

  // ── Storm Agent ───────────────────────────────────────────────────────────────

  const loadStormSettings = async () => {
    try {
      setStormLoading(true)
      setStormError(null)
      const res = await getStormSettings()
      setStormSettings(res.data.settings)
      const initial: Record<string, any> = {}
      res.data.settings.forEach(s => { initial[s.key] = s.value })
      setStormEdits(initial)
    } catch {
      setStormError('Failed to load Storm Agent settings')
    } finally {
      setStormLoading(false)
    }
  }

  const handleSaveStormSettings = async () => {
    if (!stormEdits || Object.keys(stormEdits).length === 0) return
    try {
      setStormSaving(true)
      setStormError(null)
      const payload: Record<string, any> = {}
      Object.entries(stormEdits).forEach(([k, v]) => {
        const shortKey = k.startsWith('storm.') ? k.slice(6) : k
        payload[shortKey] = v
      })
      await updateStormSettings(payload)
      await loadStormSettings()
      setSaveSuccess(true)
      setTimeout(() => setSaveSuccess(false), 3000)
    } catch {
      setStormError('Failed to save Storm Agent settings')
    } finally {
      setStormSaving(false)
    }
  }

  const handleResetStormSettings = async () => {
    if (!window.confirm('Reset Storm Agent settings to defaults?')) return
    try {
      setStormSaving(true)
      setStormError(null)
      await resetStormSettings()
      await loadStormSettings()
      setSaveSuccess(true)
      setTimeout(() => setSaveSuccess(false), 3000)
    } catch {
      setStormError('Failed to reset Storm Agent settings')
    } finally {
      setStormSaving(false)
    }
  }

  const updateStormEdit = (key: string, value: any) => {
    setStormEdits(prev => ({ ...prev, [key]: value }))
  }

  const stormVal = (key: string) => {
    if (key in stormEdits) return stormEdits[key]
    const s = stormSettings.find(r => r.key === key)
    return s ? s.value : undefined
  }

  // ── Platform Intelligence ───────────────────────────────────────────────────────

  const loadPiSettings = async () => {
    try {
      setPiLoading(true)
      setPiError(null)
      const res = await getPlatformIntelligenceSettings()
      setPiSettings(res.data.settings)
      const initial: Record<string, any> = {}
      res.data.settings.forEach(s => { initial[s.key] = s.value })
      setPiEdits(initial)
    } catch {
      setPiError('Failed to load Platform Intelligence settings')
    } finally {
      setPiLoading(false)
    }
  }

  const handleSavePiSettings = async () => {
    if (!piEdits || Object.keys(piEdits).length === 0) return
    try {
      setPiSaving(true)
      setPiError(null)
      const payload: Record<string, any> = {}
      Object.entries(piEdits).forEach(([k, v]) => {
        const shortKey = k.startsWith('platform_intelligence.') ? k.slice('platform_intelligence.'.length) : k
        payload[shortKey] = v
      })
      await updatePlatformIntelligenceSettings(payload)
      await loadPiSettings()
      setSaveSuccess(true)
      setTimeout(() => setSaveSuccess(false), 3000)
    } catch {
      setPiError('Failed to save Platform Intelligence settings')
    } finally {
      setPiSaving(false)
    }
  }

  const handleResetPiSettings = async () => {
    if (!window.confirm('Reset Platform Intelligence settings to defaults (auto-apply disabled)?')) return
    try {
      setPiSaving(true)
      setPiError(null)
      await resetPlatformIntelligenceSettings()
      await loadPiSettings()
      setSaveSuccess(true)
      setTimeout(() => setSaveSuccess(false), 3000)
    } catch {
      setPiError('Failed to reset Platform Intelligence settings')
    } finally {
      setPiSaving(false)
    }
  }

  const updatePiEdit = (key: string, value: any) => {
    setPiEdits(prev => ({ ...prev, [key]: value }))
  }

  const piVal = (key: string) => {
    if (key in piEdits) return piEdits[key]
    const s = piSettings.find(r => r.key === key)
    return s ? s.value : undefined
  }

  // ── Accordion + UI helpers ────────────────────────────────────────────────────

  const toggleSection = (section: string) => {
    const next = new Set(expandedSections)
    if (next.has(section)) next.delete(section)
    else next.add(section)
    setExpandedSections(next)
  }

  const updateUISetting = (k: string, v: any) =>
    settings && setSettings({ ...settings, ui: { ...settings.ui, [k]: v } })

  if (!settings) return <div style={{ color: DS.txtM }}>Loading settings…</div>

  const emailEnabled = !!generalVal('general.email_notifications_enabled')

  return (
    <div style={{ maxWidth: 1100, margin: '0 auto', padding: '0 1rem' }}>

      {/* ── Page header ─────────────────────────────────────────────── */}
      <div style={{ marginBottom: '1.75rem' }}>
        <h1 style={{ fontSize: '1.6rem', fontWeight: 700, color: DS.txtP, margin: 0, marginBottom: '0.3rem' }}>
          Settings
        </h1>
        <p style={{ color: DS.txtM, margin: 0, fontSize: '0.85rem' }}>
          Configure application behaviour and preferences
        </p>
      </div>

      {/* ── Global save confirmation ────────────────────────────────── */}
      {saveSuccess && (
        <div style={{
          marginBottom: '1.25rem',
          padding: '0.65rem 1rem',
          borderRadius: 8,
          backgroundColor: 'rgba(16,185,129,0.10)',
          border: '1px solid rgba(16,185,129,0.28)',
          color: '#34d399',
          display: 'flex', alignItems: 'center', gap: 8,
          fontSize: '0.85rem',
        }}>
          <IconCheck size={15} />
          Settings saved successfully
        </div>
      )}

      {/* ── Accordion sections ──────────────────────────────────────── */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem', marginBottom: '2rem' }}>

        {/* ── Environment Settings ───────────────────────────────────── */}
        <SettingSection
          title="Environment Settings"
          isExpanded={expandedSections.has('environment')}
          onToggle={() => toggleSection('environment')}
        >
          {generalError && (
            <Banner type="error"><IconAlertTriangle size={14} />{generalError}</Banner>
          )}
          {generalLoading ? (
            <div style={{ color: DS.txtM, fontSize: '0.85rem' }}>Loading…</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.1rem' }}>
              <FormField label="Environment">
                <select
                  value={String(generalVal('general.env_name') ?? 'development')}
                  onChange={e => updateGeneralEdit('general.env_name', e.target.value)}
                  style={inputStyle}
                >
                  <option value="development">Development</option>
                  <option value="staging">Staging</option>
                  <option value="production">Production</option>
                </select>
              </FormField>
              <FormField label="Debug Mode">
                <CheckRow
                  checked={!!generalVal('general.debug_mode')}
                  onChange={v => updateGeneralEdit('general.debug_mode', v)}
                  label="Enable verbose backend logging (not recommended in production)"
                />
              </FormField>
              <SectionSaveBar onSave={handleSaveGeneralSettings} saving={generalSaving} />
            </div>
          )}
        </SettingSection>

        {/* ── UI Settings ────────────────────────────────────────────── */}
        <SettingSection
          title="UI Settings"
          isExpanded={expandedSections.has('ui')}
          onToggle={() => toggleSection('ui')}
        >
          <FormField label="Sidebar Collapsed">
            <CheckRow
              checked={settings.ui.sidebarCollapsed}
              onChange={v => updateUISetting('sidebarCollapsed', v)}
              label="Collapse sidebar by default"
            />
          </FormField>
          <p style={{ fontSize: '0.72rem', color: DS.txtS, margin: '0.5rem 0 0', fontStyle: 'italic' }}>
            UI preferences are saved automatically on change.
          </p>
        </SettingSection>

        {/* ── Performance Settings ───────────────────────────────────── */}
        <SettingSection
          title="Performance Settings"
          isExpanded={expandedSections.has('performance')}
          onToggle={() => toggleSection('performance')}
        >
          {generalError && (
            <Banner type="error"><IconAlertTriangle size={14} />{generalError}</Banner>
          )}
          {generalLoading ? (
            <div style={{ color: DS.txtM, fontSize: '0.85rem' }}>Loading…</div>
          ) : (
            <div>
              <FormField label="Risk Config Cache" hint="Reduces database load by caching risk weights in memory">
                <CheckRow
                  checked={!!generalVal('general.cache_enabled')}
                  onChange={v => updateGeneralEdit('general.cache_enabled', v)}
                  label="Cache frequently-read configuration in memory"
                />
              </FormField>
              <SectionSaveBar onSave={handleSaveGeneralSettings} saving={generalSaving} />
            </div>
          )}
        </SettingSection>

        {/* ── Notification Settings ──────────────────────────────────── */}
        <SettingSection
          title="Notification Settings"
          isExpanded={expandedSections.has('notifications')}
          onToggle={() => toggleSection('notifications')}
        >
          {generalError && (
            <Banner type="error"><IconAlertTriangle size={14} />{generalError}</Banner>
          )}
          {generalLoading ? (
            <div style={{ color: DS.txtM, fontSize: '0.85rem' }}>Loading…</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.1rem' }}>
              <FormField label="In-App Alerts">
                <CheckRow
                  checked={!!generalVal('general.in_app_alerts')}
                  onChange={v => updateGeneralEdit('general.in_app_alerts', v)}
                  label="Show in-app notification banners for new incidents and approvals"
                />
              </FormField>
              <FormField label="Sound Alerts">
                <CheckRow
                  checked={!!generalVal('general.sound_alerts')}
                  onChange={v => updateGeneralEdit('general.sound_alerts', v)}
                  label="Play audio notification for new critical incidents"
                />
              </FormField>
              <FormField label="Email Notifications">
                <CheckRow
                  checked={emailEnabled}
                  onChange={v => updateGeneralEdit('general.email_notifications_enabled', v)}
                  label="Send email notifications for P1/P2 incidents (requires SMTP configuration below)"
                />
              </FormField>

              <SectionSaveBar onSave={handleSaveGeneralSettings} saving={generalSaving} />

              {/* ── SMTP Configuration (shown when email notifications enabled) ── */}
              {emailEnabled && (
                <div style={{
                  marginTop: '1.25rem',
                  padding: '1rem 1.1rem 1.1rem',
                  backgroundColor: DS.bg,
                  border: `1px solid ${DS.border}`,
                  borderRadius: 8,
                }}>
                  <h4 style={{ fontSize: '0.82rem', fontWeight: 600, color: DS.txtP, margin: '0 0 0.75rem' }}>
                    SMTP Configuration
                  </h4>
                  {smtpError && (
                    <Banner type="error"><IconAlertTriangle size={14} />{smtpError}</Banner>
                  )}
                  {smtpLoading ? (
                    <div style={{ color: DS.txtM, fontSize: '0.85rem' }}>Loading SMTP settings…</div>
                  ) : (
                    <div>
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 160px', gap: '0.75rem 1.5rem' }}>
                        <FormField label="SMTP Host" hint="e.g. smtp.gmail.com or smtp.office365.com">
                          <input
                            type="text"
                            value={String(smtpVal('smtp.host') ?? '')}
                            placeholder="smtp.example.com"
                            onChange={e => updateSmtpEdit('smtp.host', e.target.value)}
                            style={inputStyle}
                          />
                        </FormField>
                        <FormField label="Port">
                          <input
                            type="number" min="1" max="65535"
                            value={Number(smtpVal('smtp.port') ?? 587)}
                            onChange={e => updateSmtpEdit('smtp.port', parseInt(e.target.value))}
                            style={inputStyle}
                          />
                        </FormField>
                      </div>
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem 1.5rem' }}>
                        <FormField label="Username">
                          <input
                            type="text"
                            value={String(smtpVal('smtp.username') ?? '')}
                            placeholder="alerts@yourcompany.com"
                            onChange={e => updateSmtpEdit('smtp.username', e.target.value)}
                            style={inputStyle}
                          />
                        </FormField>
                        <FormField label="Password">
                          <input
                            type="password"
                            value={String(smtpVal('smtp.password') ?? '')}
                            placeholder={smtpVal('smtp.password') === '••••••••' ? 'Password set — enter new to change' : ''}
                            onChange={e => updateSmtpEdit('smtp.password', e.target.value)}
                            style={inputStyle}
                          />
                        </FormField>
                      </div>
                      <FormField label="From Address" hint="Shown to email recipients">
                        <input
                          type="email"
                          value={String(smtpVal('smtp.from_address') ?? '')}
                          placeholder="Agentic Platform <alerts@yourcompany.com>"
                          onChange={e => updateSmtpEdit('smtp.from_address', e.target.value)}
                          style={inputStyle}
                        />
                      </FormField>
                      <FormField label="Alert Recipients" hint="Comma-separated email addresses that receive incident and approval alerts">
                        <input
                          type="text"
                          value={String(smtpVal('smtp.to_addresses') ?? '')}
                          placeholder="oncall@company.com, manager@company.com"
                          onChange={e => updateSmtpEdit('smtp.to_addresses', e.target.value)}
                          style={inputStyle}
                        />
                      </FormField>
                      <FormField label="Security">
                        <CheckRow
                          checked={!!smtpVal('smtp.use_tls')}
                          onChange={v => updateSmtpEdit('smtp.use_tls', v)}
                          label="Use STARTTLS (required for port 587)"
                        />
                      </FormField>
                      <div>
                        <h5 style={{ fontSize: '0.78rem', fontWeight: 600, color: DS.txtM, margin: '0.5rem 0 0.5rem' }}>
                          Send notifications for
                        </h5>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                          <CheckRow
                            checked={!!smtpVal('smtp.notification_on_p1')}
                            onChange={v => updateSmtpEdit('smtp.notification_on_p1', v)}
                            label="P1 — Critical incidents"
                          />
                          <CheckRow
                            checked={!!smtpVal('smtp.notification_on_p2')}
                            onChange={v => updateSmtpEdit('smtp.notification_on_p2', v)}
                            label="P2 — High priority incidents"
                          />
                        </div>
                      </div>
                      <div style={{ marginTop: '1rem', borderTop: `1px solid ${DS.border}`, paddingTop: '0.75rem' }}>
                        <button
                          onClick={handleSaveSmtpSettings}
                          disabled={smtpSaving}
                          style={{ ...primaryBtn, opacity: smtpSaving ? 0.5 : 1, cursor: smtpSaving ? 'not-allowed' : 'pointer' }}
                        >
                          {smtpSaving ? 'Saving…' : 'Save SMTP Settings'}
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </SettingSection>

        {/* ── Backup Settings ────────────────────────────────────────── */}
        <SettingSection
          title="Backup Settings"
          isExpanded={expandedSections.has('backup')}
          onToggle={() => toggleSection('backup')}
        >
          {generalError && (
            <Banner type="error"><IconAlertTriangle size={14} />{generalError}</Banner>
          )}
          {generalLoading ? (
            <div style={{ color: DS.txtM, fontSize: '0.85rem' }}>Loading…</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.1rem' }}>
              <FormField label="Automatic Backups">
                <CheckRow
                  checked={!!generalVal('general.backup_enabled')}
                  onChange={v => updateGeneralEdit('general.backup_enabled', v)}
                  label="Enable scheduled rotating backups of PostgreSQL, Neo4j CMDB, and watcher config"
                />
              </FormField>
              <FormField
                label="Backup Schedule (Cron)"
                hint="Standard cron expression (minute hour day month day_of_week). Default: 0 1 * * * (daily at 01:00 UTC)"
              >
                <input
                  type="text"
                  value={String(generalVal('general.backup_schedule') ?? '0 1 * * *')}
                  placeholder="0 1 * * *"
                  onChange={e => updateGeneralEdit('general.backup_schedule', e.target.value)}
                  style={inputStyle}
                />
              </FormField>
              <FormField
                label="Backup Retention (days)"
                hint="Keep backups for N days; older files are automatically deleted. Reduces disk space usage."
              >
                <input
                  type="number" min="1" max="365" step="1"
                  value={Number(generalVal('general.backup_retention_days') ?? 7)}
                  onChange={e => updateGeneralEdit('general.backup_retention_days', parseInt(e.target.value))}
                  style={inputStyle}
                />
              </FormField>
              <SectionSaveBar onSave={handleSaveGeneralSettings} saving={generalSaving} />
            </div>
          )}
        </SettingSection>

        {/* ── Security Settings ──────────────────────────────────────── */}
        <SettingSection
          title="Security Settings"
          isExpanded={expandedSections.has('security')}
          onToggle={() => toggleSection('security')}
        >
          {generalError && (
            <Banner type="error"><IconAlertTriangle size={14} />{generalError}</Banner>
          )}
          {generalLoading ? (
            <div style={{ color: DS.txtM, fontSize: '0.85rem' }}>Loading…</div>
          ) : (
            <div>
              <FormField
                label="Session Timeout (minutes)"
                hint="Applies to new logins only. Minimum 15 minutes. Default: 480 (8 hours)."
              >
                <input
                  type="number" min="15" step="15"
                  value={Number(generalVal('general.session_timeout_minutes') ?? 480)}
                  onChange={e => updateGeneralEdit('general.session_timeout_minutes', parseInt(e.target.value))}
                  style={inputStyle}
                />
              </FormField>
              <SectionSaveBar onSave={handleSaveGeneralSettings} saving={generalSaving} />
            </div>
          )}
        </SettingSection>

        {/* ── Database Settings ──────────────────────────────────────── */}
        <SettingSection
          title="Database Settings"
          isExpanded={expandedSections.has('database')}
          onToggle={() => toggleSection('database')}
        >
          {generalError && (
            <Banner type="error"><IconAlertTriangle size={14} />{generalError}</Banner>
          )}
          {generalLoading ? (
            <div style={{ color: DS.txtM, fontSize: '0.85rem' }}>Loading…</div>
          ) : (
            <div>
              <FormField
                label="Data Retention (days)"
                hint="Resolved and closed incidents older than this are deleted by the nightly cleanup task (02:00 UTC). Minimum 7 days."
              >
                <input
                  type="number" min="7" step="7"
                  value={Number(generalVal('general.data_retention_days') ?? 90)}
                  onChange={e => updateGeneralEdit('general.data_retention_days', parseInt(e.target.value))}
                  style={inputStyle}
                />
              </FormField>
              <FormField
                label="Backup Retention (days)"
                hint="How many days of backup files to keep on disk. Older files are pruned automatically after each backup run. Minimum 1 day."
              >
                <input
                  type="number" min="1" max="365" step="1"
                  value={Number(generalVal('general.backup_retention_days') ?? 7)}
                  onChange={e => updateGeneralEdit('general.backup_retention_days', parseInt(e.target.value))}
                  style={inputStyle}
                />
              </FormField>
              <SectionSaveBar onSave={handleSaveGeneralSettings} saving={generalSaving} />
            </div>
          )}
        </SettingSection>

        {/* ── Risk Assessment Configuration ──────────────────────────── */}
        <SettingSection
          title="Risk Assessment"
          isExpanded={expandedSections.has('risk-config')}
          onToggle={() => toggleSection('risk-config')}
        >
          {riskConfigError && (
            <Banner type="error"><IconAlertTriangle size={14} />{riskConfigError}</Banner>
          )}
          {riskConfigLoading ? (
            <div style={{ color: DS.txtM, fontSize: '0.85rem' }}>Loading risk configuration…</div>
          ) : riskConfig ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>

              {/* ── Per-Factor Configuration (v2 schema) ─────────────────── */}
              {riskConfig.weights.factors ? (
                <div>
                  <h4 style={{ fontSize: '0.82rem', fontWeight: 600, color: DS.txtP, margin: '0 0 0.4rem' }}>
                    Factor Configuration
                  </h4>
                  <p style={{ margin: '0 0 0.75rem', fontSize: '0.75rem', color: DS.txtS }}>
                    Enable or disable each scoring factor, adjust its weight, and set the missing-data
                    behaviour for when your CMDB has no value for that field.
                    Disabled / excluded factors are redistributed proportionally — the 0–100 scale
                    remains valid regardless of how many factors are active.
                  </p>

                  {/* Weight budget bar */}
                  {(() => {
                    const fc = riskConfig.weights.factors as Record<string, any>
                    const enabled = Object.entries(fc).filter(([, c]) => c.enabled)
                    const totalEnabled = enabled.reduce((s, [, c]) => s + (c.weight || 0), 0)
                    const totalAll     = Object.values(fc).reduce((s: number, c: any) => s + (c.weight || 0), 0)
                    return (
                      <div style={{ marginBottom: '1rem', backgroundColor: DS.bg, border: `1px solid ${DS.border}`, borderRadius: 8, padding: '10px 14px' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '5px' }}>
                          <span style={{ fontSize: '11px', color: DS.txtS }}>Active weight budget</span>
                          <span style={{ fontSize: '11px', fontWeight: 700, color: DS.txtP }}>
                            {totalEnabled} pts enabled &nbsp;/&nbsp; {totalAll} pts total
                          </span>
                        </div>
                        <div style={{ height: '6px', backgroundColor: DS.surface, borderRadius: '999px', overflow: 'hidden', border: `1px solid ${DS.border}` }}>
                          <div style={{ height: '100%', width: `${Math.min(100, (totalEnabled / Math.max(totalAll, 1)) * 100)}%`, backgroundColor: DS.accent, borderRadius: '999px', transition: 'width 0.3s ease' }} />
                        </div>
                        <p style={{ fontSize: '10px', color: '#4b5563', margin: '4px 0 0', fontStyle: 'italic' }}>
                          Score is normalised across active factors — weights set relative importance, not absolute point values.
                        </p>
                      </div>
                    )
                  })()}

                  {/* Factor rows */}
                  <div style={{ display: 'grid', gridTemplateColumns: '20px 90px 140px', gap: '4px 0', marginBottom: '6px', paddingLeft: 'calc(100% - 250px)' }}>
                    <span />
                    <span style={{ fontSize: '9px', fontWeight: 700, color: DS.txtS, letterSpacing: '0.07em', textTransform: 'uppercase', textAlign: 'center' }}>Weight</span>
                    <span style={{ fontSize: '9px', fontWeight: 700, color: DS.txtS, letterSpacing: '0.07em', textTransform: 'uppercase', paddingLeft: '8px' }}>When CMDB missing</span>
                  </div>

                  <div style={{ display: 'flex', flexDirection: 'column', gap: '5px' }}>
                    {([
                      'severity', 'ci_tier', 'environment', 'business_criticality',
                      'user_impact', 'blast_radius', 'failover', 'spof', 'sla', 'history',
                    ] as const).map(key => {
                      const f = (riskConfig.weights.factors as any)[key]
                      if (!f) return null
                      const isEnabled = !!f.enabled
                      return (
                        <div key={key} style={{
                          display: 'grid',
                          gridTemplateColumns: '1fr 20px 90px 140px',
                          gap: '0 12px',
                          alignItems: 'center',
                          backgroundColor: isEnabled ? DS.surface : DS.bg,
                          border: `1px solid ${isEnabled ? DS.border : '#252c3c'}`,
                          borderLeft: `3px solid ${isEnabled ? DS.accent : '#2a3145'}`,
                          borderRadius: '0 7px 7px 0',
                          padding: '9px 12px',
                          opacity: isEnabled ? 1 : 0.55,
                          transition: 'opacity 0.2s',
                        }}>
                          <div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                              <span style={{ fontSize: '12px', fontWeight: 600, color: DS.txtP }}>
                                {f.label || key.replace(/_/g, ' ')}
                              </span>
                              {f.cmdb_sourced ? (
                                <span style={{ fontSize: '9px', fontWeight: 700, color: '#10b981', backgroundColor: 'rgba(16,185,129,0.1)', border: '1px solid rgba(16,185,129,0.25)', borderRadius: 4, padding: '1px 5px', letterSpacing: '0.05em' }}>
                                  CMDB
                                </span>
                              ) : (
                                <span style={{ fontSize: '9px', fontWeight: 700, color: '#3b82f6', backgroundColor: 'rgba(59,130,246,0.1)', border: '1px solid rgba(59,130,246,0.25)', borderRadius: 4, padding: '1px 5px', letterSpacing: '0.05em' }}>
                                  Computed
                                </span>
                              )}
                            </div>
                            {f.description && (
                              <p style={{ margin: '3px 0 0', fontSize: '10px', color: DS.txtS, lineHeight: 1.45 }}>
                                {f.description}
                              </p>
                            )}
                          </div>
                          <div style={{ display: 'flex', justifyContent: 'center' }}>
                            <input
                              type="checkbox"
                              checked={isEnabled}
                              onChange={e => updateWeightValue(['factors', key, 'enabled'], e.target.checked)}
                              title={isEnabled ? 'Disable this factor' : 'Enable this factor'}
                              style={{ width: 15, height: 15, accentColor: DS.accent, cursor: 'pointer' }}
                            />
                          </div>
                          <input
                            type="number" min="0" max="100" step="1"
                            value={f.weight ?? 0}
                            disabled={!isEnabled}
                            onChange={e => updateWeightValue(['factors', key, 'weight'], parseFloat(e.target.value))}
                            title="Base weight (pts)"
                            style={{ ...inputStyle, opacity: isEnabled ? 1 : 0.4, padding: '5px 8px', textAlign: 'center' }}
                          />
                          {f.cmdb_sourced ? (
                            <select
                              value={f.missing_data || 'neutral'}
                              disabled={!isEnabled}
                              onChange={e => updateWeightValue(['factors', key, 'missing_data'], e.target.value)}
                              style={{ ...inputStyle, opacity: isEnabled ? 1 : 0.4, padding: '5px 8px', fontSize: '0.78rem' }}
                            >
                              <option value="neutral">Neutral — safe default</option>
                              <option value="pessimistic">Pessimistic — worst case</option>
                              <option value="exclude">Exclude — skip factor</option>
                            </select>
                          ) : (
                            <span style={{ fontSize: '10px', color: DS.txtS, fontStyle: 'italic' }}>
                              Always available
                            </span>
                          )}
                        </div>
                      )
                    })}
                  </div>
                </div>
              ) : riskConfig.weights.factor_weights ? (
                <div>
                  <h4 style={{ fontSize: '0.82rem', fontWeight: 600, color: DS.txtP, margin: '0 0 0.4rem' }}>
                    Factor Base Weights
                  </h4>
                  <p style={{ margin: '0 0 0.75rem', fontSize: '0.75rem', color: DS.txtS }}>
                    Maximum points each factor contributes. Total:&nbsp;
                    <strong style={{ color: DS.txtP }}>
                      {Object.values(riskConfig.weights.factor_weights as Record<string, number>).reduce((a, b) => a + b, 0)} pts
                    </strong>
                  </p>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: '0.75rem 1.5rem' }}>
                    {([
                      ['severity', 'Severity'], ['ci_tier', 'CI Tier'], ['environment', 'Environment'],
                      ['business_criticality', 'Biz Criticality'], ['user_impact', 'User Impact'],
                      ['blast_radius', 'Blast Radius'], ['failover', 'Failover'], ['spof', 'SPOF'],
                      ['sla', 'SLA'], ['history', 'History'],
                    ] as [string, string][]).map(([key, label]) => (
                      <FormField key={key} label={label}>
                        <input
                          type="number" min="0" max="100" step="1"
                          value={(riskConfig.weights.factor_weights as any)[key] ?? 0}
                          onChange={e => updateWeightValue(['factor_weights', key], parseFloat(e.target.value))}
                          style={inputStyle}
                        />
                      </FormField>
                    ))}
                  </div>
                </div>
              ) : null}

              {/* Environment Multipliers */}
              {(riskConfig.weights.environment_multipliers || riskConfig.weights.environment_multiplier) && (
                <div>
                  <h4 style={{ fontSize: '0.82rem', fontWeight: 600, color: DS.txtP, margin: '0 0 0.4rem' }}>
                    Environment Multipliers
                  </h4>
                  <p style={{ margin: '0 0 0.75rem', fontSize: '0.75rem', color: DS.txtS }}>
                    Scales the environment factor's contribution to the risk score.
                  </p>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '0.75rem 1.5rem' }}>
                    {Object.entries(riskConfig.weights.environment_multipliers ?? riskConfig.weights.environment_multiplier ?? {}).filter(([env]) => !['prod', 'stage', 'dev'].includes(env)).map(([env, value]: [string, any]) => {
                      const label: Record<string, string> = {
                        production: 'Production', staging: 'Staging',
                        development: 'Development', test: 'Test', qa: 'QA', unknown: 'Unknown',
                      }
                      const envKey = riskConfig.weights.environment_multipliers ? 'environment_multipliers' : 'environment_multiplier'
                      return (
                        <FormField key={env} label={label[env] ?? env}>
                          <input
                            type="number" min="0" max="2" step="0.05"
                            value={value as number}
                            onChange={e => updateWeightValue([envKey, env], parseFloat(e.target.value))}
                            style={inputStyle}
                          />
                        </FormField>
                      )
                    })}
                  </div>
                </div>
              )}

              {/* Business Criticality Multipliers */}
              {riskConfig.weights.business_criticality_multiplier && (
                <div>
                  <h4 style={{ fontSize: '0.82rem', fontWeight: 600, color: DS.txtP, margin: '0 0 0.4rem' }}>
                    Business Criticality Multipliers
                  </h4>
                  <p style={{ margin: '0 0 0.75rem', fontSize: '0.75rem', color: DS.txtS }}>
                    Scales business criticality factor contribution.
                  </p>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '0.75rem 1.5rem' }}>
                    {Object.entries(riskConfig.weights.business_criticality_multiplier).map(([tier, value]: [string, any]) => {
                      const label: Record<string, string> = {
                        tier_1: 'Tier 1 — Mission Critical',
                        tier_2: 'Tier 2 — Core Services',
                        tier_3: 'Tier 3 — Infrastructure',
                      }
                      return (
                        <FormField key={tier} label={label[tier] ?? tier}>
                          <input
                            type="number" min="0" max="2" step="0.05"
                            value={value as number}
                            onChange={e => updateWeightValue(['business_criticality_multiplier', tier], parseFloat(e.target.value))}
                            style={inputStyle}
                          />
                        </FormField>
                      )
                    })}
                  </div>
                </div>
              )}

              <div style={{ borderTop: `1px solid ${DS.border}`, paddingTop: '1rem', display: 'flex', gap: 8 }}>
                <button
                  onClick={handleResetRiskConfig}
                  disabled={riskConfigSaving}
                  style={{ ...secondaryBtn, opacity: riskConfigSaving ? 0.5 : 1, cursor: riskConfigSaving ? 'not-allowed' : 'pointer' }}
                >
                  Reset to Defaults
                </button>
                <button
                  onClick={handleSaveRiskConfig}
                  disabled={riskConfigSaving}
                  style={{ ...primaryBtn, opacity: riskConfigSaving ? 0.5 : 1, cursor: riskConfigSaving ? 'not-allowed' : 'pointer' }}
                >
                  {riskConfigSaving ? 'Saving…' : 'Save Configuration'}
                </button>
              </div>
            </div>
          ) : null}
        </SettingSection>

        {/* ── Incident Qualification ──────────────────────────────────── */}
        <SettingSection
          title="Incident Qualification"
          isExpanded={expandedSections.has('incident-qualification')}
          onToggle={() => toggleSection('incident-qualification')}
        >
          {riskConfigError && (
            <Banner type="error"><IconAlertTriangle size={14} />{riskConfigError}</Banner>
          )}
          {riskConfigLoading ? (
            <div style={{ color: DS.txtM, fontSize: '0.85rem' }}>Loading qualification configuration…</div>
          ) : riskConfig ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>

              <p style={{ margin: 0, fontSize: '0.8rem', color: DS.txtS, lineHeight: 1.6 }}>
                Controls the <strong style={{ color: DS.txtP }}>qualification gate</strong> — whether a monitoring
                event becomes an incident workflow at all.
              </p>

              <div>
                <h4 style={{ fontSize: '0.82rem', fontWeight: 600, color: DS.txtP, margin: '0 0 0.75rem' }}>
                  Thresholds
                </h4>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem 1.5rem' }}>
                  <FormField label="Qualification Threshold (0–100)" hint="Events scoring above this trigger an incident workflow">
                    <input
                      type="number" min="0" max="100" step="5"
                      value={riskConfig.weights.qualification_threshold || 50}
                      onChange={e => updateWeightValue(['qualification_threshold'], parseFloat(e.target.value))}
                      style={inputStyle}
                    />
                  </FormField>
                  <FormField label="Unknown CI Score Cap (0–100)" hint="Max score when CI is not in CMDB and policy is 'qualify_as_low'">
                    <input
                      type="number" min="0" max="100" step="5"
                      value={riskConfig.weights.unknown_ci_score_cap ?? 40}
                      onChange={e => updateWeightValue(['unknown_ci_score_cap'], parseFloat(e.target.value))}
                      style={inputStyle}
                    />
                  </FormField>
                </div>
              </div>

              <div>
                <h4 style={{ fontSize: '0.82rem', fontWeight: 600, color: DS.txtP, margin: '0 0 0.35rem' }}>
                  Unknown CI Behaviour
                </h4>
                <p style={{ fontSize: '0.75rem', color: DS.txtS, margin: '0 0 0.75rem' }}>
                  What to do when the affected resource is not found in the CMDB.
                </p>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                  {([
                    ['qualify_normal', 'Qualify normally', 'Score as if environment is unknown (×0.75 multiplier). Recommended default.'],
                    ['qualify_as_low', 'Qualify as low', 'Cap the score at the Unknown CI Score Cap. Events still qualify but with reduced priority.'],
                    ['dismiss',        'Dismiss',         'Reject the event immediately — CI must exist in CMDB to trigger an incident.'],
                  ] as [string, string, string][]).map(([val, label, desc]) => {
                    const active = (riskConfig.weights.unknown_ci_behavior ?? 'qualify_normal') === val
                    return (
                      <label key={val} style={{
                        display: 'flex', alignItems: 'flex-start', gap: 10, cursor: 'pointer',
                        padding: '10px 12px', borderRadius: 8,
                        border: `1px solid ${active ? DS.accent : DS.border}`,
                        background: active ? 'rgba(59,130,246,0.07)' : DS.surface,
                        transition: 'all 0.15s',
                      }}>
                        <input
                          type="radio"
                          name="unknown_ci_behavior"
                          value={val}
                          checked={active}
                          onChange={() => updateWeightValue(['unknown_ci_behavior'], val)}
                          style={{ marginTop: 2, accentColor: DS.accent, flexShrink: 0 }}
                        />
                        <div>
                          <div style={{ fontSize: '0.82rem', fontWeight: 600, color: active ? DS.txtP : DS.txtM }}>{label}</div>
                          <div style={{ fontSize: '0.73rem', color: DS.txtS, marginTop: 2 }}>{desc}</div>
                        </div>
                      </label>
                    )
                  })}
                </div>
              </div>

              <div>
                <h4 style={{ fontSize: '0.82rem', fontWeight: 600, color: DS.txtP, margin: '0 0 0.35rem' }}>
                  Minimum Score by Severity
                </h4>
                <p style={{ fontSize: '0.75rem', color: DS.txtS, margin: '0 0 0.75rem' }}>
                  An event must reach both the qualification threshold AND this floor for its severity level.
                </p>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '0.75rem 1.5rem' }}>
                  {(['info', 'warning', 'critical'] as const).map(level => {
                    const floors = riskConfig.weights.criticality_min_score || { info: 75, warning: 50, critical: 30 }
                    const defaultVal = { info: 75, warning: 50, critical: 30 }[level]
                    const floorColor = { info: '#6b7280', warning: '#f59e0b', critical: '#dc2626' }[level]
                    return (
                      <div key={level}>
                        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.78rem', fontWeight: 600, color: DS.txtM, marginBottom: '0.35rem' }}>
                          <span style={{ width: 8, height: 8, borderRadius: '50%', backgroundColor: floorColor, display: 'inline-block', flexShrink: 0 }} />
                          {level.charAt(0).toUpperCase() + level.slice(1)}
                          <span style={{ fontSize: '0.7rem', color: DS.txtS, fontWeight: 400 }}>(default: {defaultVal})</span>
                        </label>
                        <input
                          type="number" min="0" max="100" step="5"
                          value={(floors as any)[level] ?? defaultVal}
                          onChange={e => updateWeightValue(['criticality_min_score', level], parseFloat(e.target.value))}
                          style={inputStyle}
                        />
                      </div>
                    )
                  })}
                </div>
              </div>

              {(riskConfig.weights.environment_multipliers || riskConfig.weights.environment_multiplier) && (
                <div>
                  <h4 style={{ fontSize: '0.82rem', fontWeight: 600, color: DS.txtP, margin: '0 0 0.4rem' }}>
                    Environment Multipliers
                  </h4>
                  <p style={{ margin: '0 0 0.75rem', fontSize: '0.75rem', color: DS.txtS }}>
                    Scales the final qualification score by deployment environment. Production = 1.0 (full weight); lower values reduce the score for non-production environments.
                  </p>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '0.75rem 1.5rem' }}>
                    {Object.entries(riskConfig.weights.environment_multipliers ?? riskConfig.weights.environment_multiplier ?? {}).filter(([env]) => !['prod', 'stage', 'dev'].includes(env)).map(([env, value]: [string, any]) => {
                      const label: Record<string, string> = {
                        production: 'Production', staging: 'Staging',
                        development: 'Development', test: 'Test', qa: 'QA', unknown: 'Unknown / not in CMDB',
                      }
                      const envKey = riskConfig.weights.environment_multipliers ? 'environment_multipliers' : 'environment_multiplier'
                      return (
                        <FormField key={env} label={label[env] ?? env}>
                          <input
                            type="number" min="0" max="2" step="0.05"
                            value={value as number}
                            onChange={e => updateWeightValue([envKey, env], parseFloat(e.target.value))}
                            style={inputStyle}
                          />
                        </FormField>
                      )
                    })}
                  </div>
                </div>
              )}

              {riskConfig.weights.domain_multipliers && (() => {
                const domainLabels: Record<string, string> = {
                  security: 'Security', database: 'Database', synthetic: 'Synthetic Monitoring',
                  application: 'Application', container: 'Container / Kubernetes',
                  network: 'Network', infrastructure: 'Infrastructure',
                  cloud: 'Cloud', log: 'Log / Events', custom: 'Custom / Unknown',
                }
                const MultRow = ({ label, v, onChange }: { label: string; v: number; onChange: (n: number) => void }) => {
                  const isBoost = v > 1.0; const isSuppress = v < 1.0
                  const badgeColor = isBoost ? '#10b981' : isSuppress ? '#f97316' : '#6b7280'
                  const badgeBg   = isBoost ? 'rgba(16,185,129,0.1)' : isSuppress ? 'rgba(249,115,22,0.1)' : 'rgba(107,114,128,0.1)'
                  const badgeLabel = isBoost ? `+${((v - 1) * 100).toFixed(0)}%` : isSuppress ? `−${((1 - v) * 100).toFixed(0)}%` : 'neutral'
                  return (
                    <div style={{
                      display: 'grid', gridTemplateColumns: '1fr 90px 80px',
                      gap: '0 12px', alignItems: 'center',
                      backgroundColor: DS.surface, border: `1px solid ${DS.border}`,
                      borderLeft: `3px solid ${badgeColor}`,
                      borderRadius: '0 7px 7px 0', padding: '7px 12px',
                    }}>
                      <span style={{ fontSize: '12px', fontWeight: 600, color: DS.txtP }}>{label}</span>
                      <input type="number" min="0" max="5" step="0.1" value={v}
                        onChange={e => onChange(parseFloat(e.target.value))}
                        style={{ ...inputStyle, padding: '4px 8px', textAlign: 'center' }} />
                      <span style={{
                        fontSize: '10px', fontWeight: 700, letterSpacing: '0.04em',
                        color: badgeColor, backgroundColor: badgeBg,
                        border: `1px solid ${badgeColor}30`,
                        borderRadius: 5, padding: '3px 6px', textAlign: 'center',
                      }}>{badgeLabel}</span>
                    </div>
                  )
                }
                return (
                  <>
                    <div>
                      <h4 style={{ fontSize: '0.82rem', fontWeight: 600, color: DS.txtP, margin: '0 0 0.4rem' }}>
                        Domain Multipliers
                      </h4>
                      <p style={{ margin: '0 0 0.75rem', fontSize: '0.75rem', color: DS.txtS }}>
                        Default multiplier for every event type within each domain. Specific overrides below take priority.
                      </p>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '5px' }}>
                        {Object.entries(riskConfig.weights.domain_multipliers as Record<string, number>).map(([domain, value]) => (
                          <MultRow key={domain}
                            label={domainLabels[domain] ?? domain}
                            v={value}
                            onChange={n => updateWeightValue(['domain_multipliers', domain], n)}
                          />
                        ))}
                      </div>
                    </div>

                    <div>
                      <h4 style={{ fontSize: '0.82rem', fontWeight: 600, color: DS.txtP, margin: '0 0 0.25rem' }}>
                        Per-Type Overrides
                      </h4>
                      <p style={{ margin: '0 0 0.75rem', fontSize: '0.75rem', color: DS.txtS }}>
                        Override the domain default for a specific event type. Use canonical dot-notation codes (e.g. <code style={{ fontSize: '0.72rem', backgroundColor: DS.border, borderRadius: 3, padding: '1px 4px' }}>application.availability.service_down</code>).
                      </p>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '5px' }}>
                        {Object.entries(riskConfig.weights.event_type_multipliers ?? {}).map(([code, value]: [string, any]) => {
                          const v = value as number
                          const isBoost = v > 1.0; const isSuppress = v < 1.0
                          const badgeColor = isBoost ? '#10b981' : isSuppress ? '#f97316' : '#6b7280'
                          const badgeBg   = isBoost ? 'rgba(16,185,129,0.1)' : isSuppress ? 'rgba(249,115,22,0.1)' : 'rgba(107,114,128,0.1)'
                          const badgeLabel = isBoost ? `+${((v - 1) * 100).toFixed(0)}%` : isSuppress ? `−${((1 - v) * 100).toFixed(0)}%` : 'neutral'
                          return (
                            <div key={code} style={{
                              display: 'grid', gridTemplateColumns: '1fr 90px 80px 28px',
                              gap: '0 8px', alignItems: 'center',
                              backgroundColor: DS.surface, border: `1px solid ${DS.border}`,
                              borderLeft: `3px solid ${badgeColor}`,
                              borderRadius: '0 7px 7px 0', padding: '7px 10px',
                            }}>
                              <span style={{ fontSize: '11px', fontFamily: 'monospace', color: DS.txtP, wordBreak: 'break-all' }}>{code}</span>
                              <input type="number" min="0" max="5" step="0.1" value={v}
                                onChange={e => updateWeightValue(['event_type_multipliers', code], parseFloat(e.target.value))}
                                style={{ ...inputStyle, padding: '4px 8px', textAlign: 'center' }} />
                              <span style={{
                                fontSize: '10px', fontWeight: 700, letterSpacing: '0.04em',
                                color: badgeColor, backgroundColor: badgeBg,
                                border: `1px solid ${badgeColor}30`,
                                borderRadius: 5, padding: '3px 6px', textAlign: 'center',
                              }}>{badgeLabel}</span>
                              <button onClick={() => deleteWeightKey(['event_type_multipliers'], code)}
                                title="Remove override"
                                style={{ background: 'none', border: 'none', cursor: 'pointer', color: DS.txtS, fontSize: '14px', padding: '2px', lineHeight: 1 }}>×</button>
                            </div>
                          )
                        })}
                        {/* Add new override */}
                        <div style={{ position: 'relative' }}>
                          <div style={{
                            display: 'grid', gridTemplateColumns: '1fr 90px 28px',
                            gap: '0 8px', alignItems: 'center',
                            border: `1px dashed ${DS.border}`, borderRadius: 7, padding: '7px 10px',
                          }}>
                            <input
                              type="text" placeholder="Search event types (e.g. cpu, disk, database)…"
                              value={newOverrideCode}
                              onChange={e => setNewOverrideCode(e.target.value)}
                              autoComplete="off"
                              style={{ ...inputStyle, fontSize: '11px', fontFamily: 'monospace' }}
                            />
                            <input type="number" min="0" max="5" step="0.1" value={newOverrideValue}
                              onChange={e => setNewOverrideValue(parseFloat(e.target.value))}
                              style={{ ...inputStyle, padding: '4px 8px', textAlign: 'center' }} />
                            <button
                              onClick={() => {
                                const code = newOverrideCode.trim()
                                if (!code) return
                                updateWeightValue(['event_type_multipliers', code], newOverrideValue)
                                setNewOverrideCode('')
                                setNewOverrideValue(1.0)
                              }}
                              title="Add override"
                              style={{
                                background: DS.accent, border: 'none', cursor: 'pointer',
                                color: '#fff', fontSize: '16px', borderRadius: 5,
                                padding: '2px', lineHeight: 1, width: 28, height: 28,
                              }}>+</button>
                          </div>

                          {newOverrideCode.trim() && (() => {
                            const q = newOverrideCode.trim().toLowerCase()
                            const existing = riskConfig.weights.event_type_multipliers ?? {}
                            const matches = eventTypeOptions
                              .filter(o => !(o.code in existing) && (
                                o.code.toLowerCase().includes(q)
                                || o.label.toLowerCase().includes(q)
                                || o.category.toLowerCase().includes(q)
                                || o.aliases.some(a => a.toLowerCase().includes(q))
                              ))
                              .slice(0, 30)
                            if (matches.length === 0) return null
                            return (
                              <div style={{
                                position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 10,
                                marginTop: '0.25rem', maxHeight: '220px', overflowY: 'auto',
                                backgroundColor: DS.raised, border: `1px solid ${DS.border}`, borderRadius: '0.4rem',
                                boxShadow: '0 8px 24px rgba(0,0,0,0.35)',
                              }}>
                                {matches.map(o => (
                                  <button
                                    type="button"
                                    key={o.code}
                                    onClick={() => setNewOverrideCode(o.code)}
                                    style={{
                                      display: 'block', width: '100%', textAlign: 'left',
                                      padding: '0.4rem 0.6rem', background: 'none', border: 'none',
                                      color: DS.txtP, cursor: 'pointer', fontSize: '12px',
                                    }}
                                  >
                                    <span>{o.label}</span>
                                    <span style={{ color: DS.txtS, marginLeft: '0.5rem', fontSize: '11px' }}>
                                      {o.category} · {o.code}
                                    </span>
                                  </button>
                                ))}
                              </div>
                            )
                          })()}
                        </div>
                      </div>
                    </div>
                  </>
                )
              })()}

              <div style={{ borderTop: `1px solid ${DS.border}`, paddingTop: '1rem', display: 'flex', gap: 8 }}>
                <button
                  onClick={handleResetRiskConfig}
                  disabled={riskConfigSaving}
                  style={{ ...secondaryBtn, opacity: riskConfigSaving ? 0.5 : 1, cursor: riskConfigSaving ? 'not-allowed' : 'pointer' }}
                >
                  Reset to Defaults
                </button>
                <button
                  onClick={handleSaveRiskConfig}
                  disabled={riskConfigSaving}
                  style={{ ...primaryBtn, opacity: riskConfigSaving ? 0.5 : 1, cursor: riskConfigSaving ? 'not-allowed' : 'pointer' }}
                >
                  {riskConfigSaving ? 'Saving…' : 'Save Configuration'}
                </button>
              </div>
            </div>
          ) : null}
        </SettingSection>

        {/* ── Storm Agent Settings ────────────────────────────────────── */}
        <SettingSection
          title="Storm Agent"
          isExpanded={expandedSections.has('storm')}
          onToggle={() => toggleSection('storm')}
        >
          {stormError && (
            <Banner type="error"><IconAlertTriangle size={14} />{stormError}</Banner>
          )}
          {stormLoading ? (
            <div style={{ color: DS.txtM, fontSize: '0.85rem' }}>Loading Storm Agent settings…</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
              <p style={{ margin: 0, fontSize: '0.8rem', color: DS.txtS, lineHeight: 1.6 }}>
                The Storm Agent detects correlated event bursts across multiple resources and
                groups them into a single parent incident on the Event Storms page, suppressing
                redundant individual remediations until an operator resolves or releases them.
              </p>
              <div>
                <FormField label="Storm Detection">
                  <CheckRow
                    checked={!!stormVal('storm.enabled')}
                    onChange={v => updateStormEdit('storm.enabled', v)}
                    label="Enable Storm Agent (disable to process all events individually)"
                  />
                </FormField>
              </div>
              <div>
                <h4 style={{ fontSize: '0.82rem', fontWeight: 600, color: DS.txtP, margin: '0 0 0.75rem' }}>
                  Detection Parameters
                </h4>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem 1.5rem' }}>
                  <FormField label="Detection Window (s)" hint="Incidents within this window are considered for correlation">
                    <input
                      type="number" min="30" max="600" step="30"
                      value={stormVal('storm.window_seconds') ?? 120}
                      onChange={e => updateStormEdit('storm.window_seconds', parseInt(e.target.value))}
                      style={inputStyle}
                    />
                  </FormField>
                  <FormField label="Minimum Incidents" hint="Below this, no storm is raised">
                    <input
                      type="number" min="2" max="20" step="1"
                      value={stormVal('storm.min_incidents') ?? 3}
                      onChange={e => updateStormEdit('storm.min_incidents', parseInt(e.target.value))}
                      style={inputStyle}
                    />
                  </FormField>
                  <FormField label="Minimum Resources" hint="Distinct affected resources required">
                    <input
                      type="number" min="2" max="20" step="1"
                      value={stormVal('storm.min_resources') ?? 2}
                      onChange={e => updateStormEdit('storm.min_resources', parseInt(e.target.value))}
                      style={inputStyle}
                    />
                  </FormField>
                  <FormField label="Storm Merge Window (min)" hint="Concurrent detections within this window are merged">
                    <input
                      type="number" min="1" max="60" step="1"
                      value={stormVal('storm.merge_window_minutes') ?? 5}
                      onChange={e => updateStormEdit('storm.merge_window_minutes', parseInt(e.target.value))}
                      style={inputStyle}
                    />
                  </FormField>
                  <FormField label="Pipeline Hold Buffer (s)" hint="Delay incident processing by this many seconds after creation, giving storm detection time to group it first. 0 = no delay.">
                    <input
                      type="number" min="0" max="300" step="15"
                      value={stormVal('storm.pipeline_hold_seconds') ?? 0}
                      onChange={e => updateStormEdit('storm.pipeline_hold_seconds', parseInt(e.target.value))}
                      style={inputStyle}
                    />
                  </FormField>
                </div>
              </div>
              <div>
                <h4 style={{ fontSize: '0.82rem', fontWeight: 600, color: DS.txtP, margin: '0 0 0.75rem' }}>
                  Behaviour
                </h4>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                  <FormField label="Require CAB Approval">
                    <CheckRow
                      checked={!!stormVal('storm.require_cab_approval')}
                      onChange={v => updateStormEdit('storm.require_cab_approval', v)}
                      label="Storm parent incidents always require Change Advisory Board approval before coordinated remediation"
                    />
                  </FormField>
                  <FormField label="Auto-Hold Children">
                    <CheckRow
                      checked={!!stormVal('storm.auto_hold_children')}
                      onChange={v => updateStormEdit('storm.auto_hold_children', v)}
                      label="Place child incidents on hold while storm is active"
                    />
                  </FormField>
                  <FormField label="Exclude External Connector Events">
                    <CheckRow
                      checked={!!stormVal('storm.exclude_external_events')}
                      onChange={v => updateStormEdit('storm.exclude_external_events', v)}
                      label="Exclude incidents from Splunk, Datadog, PagerDuty, etc. from storm detection (prevents bulk imports triggering false storms)"
                    />
                  </FormField>
                </div>
              </div>
              <div>
                <h4 style={{ fontSize: '0.82rem', fontWeight: 600, color: DS.txtP, margin: '0 0 0.75rem' }}>
                  Root Cause Analysis
                </h4>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                  <FormField label="LLM Hypothesis">
                    <CheckRow
                      checked={!!stormVal('storm.llm_hypothesis_enabled')}
                      onChange={v => updateStormEdit('storm.llm_hypothesis_enabled', v)}
                      label="Generate AI-powered root cause hypothesis (requires LLM provider)"
                    />
                  </FormField>
                  <FormField label="Neo4j Topology">
                    <CheckRow
                      checked={!!stormVal('storm.neo4j_topology_enabled')}
                      onChange={v => updateStormEdit('storm.neo4j_topology_enabled', v)}
                      label="Query Neo4j CMDB for shared upstream dependency analysis"
                    />
                  </FormField>
                </div>
              </div>
              <div style={{ borderTop: `1px solid ${DS.border}`, paddingTop: '1rem', display: 'flex', gap: 8 }}>
                <button
                  onClick={handleResetStormSettings}
                  disabled={stormSaving}
                  style={{ ...secondaryBtn, opacity: stormSaving ? 0.5 : 1, cursor: stormSaving ? 'not-allowed' : 'pointer' }}
                >
                  Reset to Defaults
                </button>
                <button
                  onClick={handleSaveStormSettings}
                  disabled={stormSaving}
                  style={{ ...primaryBtn, opacity: stormSaving ? 0.5 : 1, cursor: stormSaving ? 'not-allowed' : 'pointer' }}
                >
                  {stormSaving ? 'Saving…' : 'Save Storm Settings'}
                </button>
              </div>
            </div>
          )}
        </SettingSection>

        {/* ── Platform Intelligence Settings ─────────────────────────── */}
        <SettingSection
          title="Platform Intelligence"
          isExpanded={expandedSections.has('platform_intelligence')}
          onToggle={() => toggleSection('platform_intelligence')}
        >
          {piError && (
            <Banner type="error"><IconAlertTriangle size={14} />{piError}</Banner>
          )}
          {piLoading ? (
            <div style={{ color: DS.txtM, fontSize: '0.85rem' }}>Loading Platform Intelligence settings…</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
              <p style={{ margin: 0, fontSize: '0.8rem', color: DS.txtS, lineHeight: 1.6 }}>
                Platform Intelligence analyses resolved incidents and recommends qualification
                config changes. Recommendations always require human accept/reject by default.
                Auto-Apply lets a parameter that has repeatedly proven itself skip that review —
                off by default, and a single verified regression reverts it to manual review
                automatically regardless of this setting.
              </p>
              <div>
                <h4 style={{ fontSize: '0.82rem', fontWeight: 600, color: DS.txtP, margin: '0 0 0.75rem' }}>
                  Scheduled Analysis
                </h4>
                <FormField label="Scheduled Analysis">
                  <CheckRow
                    checked={!!piVal('platform_intelligence.analysis_schedule_enabled')}
                    onChange={v => updatePiEdit('platform_intelligence.analysis_schedule_enabled', v)}
                    label="Run analysis automatically on a schedule, in addition to the Run Analysis Now button"
                  />
                </FormField>
                <div style={{ marginTop: '0.75rem', maxWidth: '320px' }}>
                  <FormField label="Analysis Schedule (Cron, UTC)" hint="Format: minute hour day month day_of_week. Default: 0 6 * * * (06:00 UTC daily)">
                    <input
                      type="text"
                      value={piVal('platform_intelligence.analysis_schedule') ?? '0 6 * * *'}
                      onChange={e => updatePiEdit('platform_intelligence.analysis_schedule', e.target.value)}
                      style={{ ...inputStyle, fontFamily: 'ui-monospace, monospace' }}
                    />
                  </FormField>
                </div>
              </div>
              <div>
                <FormField label="Auto-Apply">
                  <CheckRow
                    checked={!!piVal('platform_intelligence.auto_apply_enabled')}
                    onChange={v => updatePiEdit('platform_intelligence.auto_apply_enabled', v)}
                    label="Allow trusted recommendation parameters to apply themselves without manual review"
                  />
                </FormField>
              </div>
              <div>
                <h4 style={{ fontSize: '0.82rem', fontWeight: 600, color: DS.txtP, margin: '0 0 0.75rem' }}>
                  Trust Parameters
                </h4>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem 1.5rem' }}>
                  <FormField label="Trust Threshold (cycles)" hint="Consecutive verified-improved cycles needed before a parameter can auto-apply">
                    <input
                      type="number" min="1" max="10" step="1"
                      value={piVal('platform_intelligence.auto_apply_min_cycles') ?? 3}
                      onChange={e => updatePiEdit('platform_intelligence.auto_apply_min_cycles', parseInt(e.target.value))}
                      style={inputStyle}
                    />
                  </FormField>
                  <FormField label="Verification Delay (days)" hint="Days to wait after applying before checking if the metric improved">
                    <input
                      type="number" min="1" max="30" step="1"
                      value={piVal('platform_intelligence.verification_delay_days') ?? 7}
                      onChange={e => updatePiEdit('platform_intelligence.verification_delay_days', parseInt(e.target.value))}
                      style={inputStyle}
                    />
                  </FormField>
                </div>
              </div>
              <div style={{ borderTop: `1px solid ${DS.border}`, paddingTop: '1rem', display: 'flex', gap: 8 }}>
                <button
                  onClick={handleResetPiSettings}
                  disabled={piSaving}
                  style={{ ...secondaryBtn, opacity: piSaving ? 0.5 : 1, cursor: piSaving ? 'not-allowed' : 'pointer' }}
                >
                  Reset to Defaults
                </button>
                <button
                  onClick={handleSavePiSettings}
                  disabled={piSaving}
                  style={{ ...primaryBtn, opacity: piSaving ? 0.5 : 1, cursor: piSaving ? 'not-allowed' : 'pointer' }}
                >
                  {piSaving ? 'Saving…' : 'Save Platform Intelligence Settings'}
                </button>
              </div>
            </div>
          )}
        </SettingSection>

        {/* ── LLM Provider Settings ──────────────────────────────────── */}
        <div style={{
          backgroundColor: DS.surface,
          border: `1px solid ${DS.border}`,
          borderRadius: 10,
          overflow: 'hidden',
        }}>
          <LLMSettings isExpanded={expandedSections.has('llm')} onToggle={() => toggleSection('llm')} />
        </div>

        {/* ── Slack ChatOps ─────────────────────────────────────────── */}
        <SettingSection
          title="Slack ChatOps"
          isExpanded={expandedSections.has('slack')}
          onToggle={() => toggleSection('slack')}
        >
          <p style={{ margin: '0 0 1rem', fontSize: '0.8rem', color: DS.txtS, lineHeight: 1.6 }}>
            Connect the AI Ops Assistant to Slack. Once configured, operators can query incidents,
            request approvals, and receive notifications directly in Slack channels or DMs.
          </p>

          {slackError && (
            <Banner type="error"><IconAlertTriangle size={14} />{slackError}</Banner>
          )}

          {slackLoading ? (
            <div style={{ color: DS.txtM, fontSize: '0.85rem' }}>Loading Slack settings…</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.1rem' }}>

              <FormField label="Enable Slack ChatOps">
                <CheckRow
                  checked={!!slackVal('slack.enabled')}
                  onChange={v => updateSlackEdit('slack.enabled', v)}
                  label="Enable Slack integration (bot token and signing secret required)"
                />
              </FormField>

              {/* ── Credentials ──── */}
              <div style={{
                marginTop: '0.75rem',
                padding: '1rem 1.1rem 1.1rem',
                backgroundColor: DS.bg,
                border: `1px solid ${DS.border}`,
                borderRadius: 8,
              }}>
                <h4 style={{ fontSize: '0.82rem', fontWeight: 600, color: DS.txtP, margin: '0 0 0.75rem' }}>
                  Credentials
                </h4>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.1rem' }}>
                  <FormField
                    label="Bot Token"
                    hint="Slack Bot User OAuth Token — starts with xoxb-. OAuth & Permissions → Install App."
                  >
                    <input
                      type="password"
                      value={String(slackVal('slack.bot_token') ?? '')}
                      placeholder={slackVal('slack.bot_token') === '••••••••' ? 'Token set — enter new to replace' : 'xoxb-…'}
                      onChange={e => updateSlackEdit('slack.bot_token', e.target.value)}
                      style={inputStyle}
                      autoComplete="off"
                    />
                  </FormField>
                  <FormField
                    label="Signing Secret"
                    hint="From Basic Information → App Credentials in api.slack.com/apps."
                  >
                    <input
                      type="password"
                      value={String(slackVal('slack.signing_secret') ?? '')}
                      placeholder={slackVal('slack.signing_secret') === '••••••••' ? 'Secret set — enter new to replace' : 'Signing secret…'}
                      onChange={e => updateSlackEdit('slack.signing_secret', e.target.value)}
                      style={inputStyle}
                      autoComplete="off"
                    />
                  </FormField>
                  <FormField
                    label="App-Level Token"
                    hint="Socket Mode token — starts with xapp-. Basic Information → App-Level Tokens → add scope connections:write. Enables inbound chat without a public URL."
                  >
                    <input
                      type="password"
                      value={String(slackVal('slack.app_token') ?? '')}
                      placeholder={slackVal('slack.app_token') === '••••••••' ? 'Token set — enter new to replace' : 'xapp-… (optional, enables Socket Mode)'}
                      onChange={e => updateSlackEdit('slack.app_token', e.target.value)}
                      style={inputStyle}
                      autoComplete="off"
                    />
                  </FormField>
                </div>
              </div>

              {/* ── Notifications ──── */}
              <div style={{
                marginTop: '0.75rem',
                padding: '1rem 1.1rem 1.1rem',
                backgroundColor: DS.bg,
                border: `1px solid ${DS.border}`,
                borderRadius: 8,
              }}>
                <h4 style={{ fontSize: '0.82rem', fontWeight: 600, color: DS.txtP, margin: '0 0 0.75rem' }}>
                  Proactive Notifications
                </h4>
                <FormField
                  label="Default Channel"
                  hint="Channel ID or name for outbound notifications (e.g. #incidents or C0123ABCDEF). Leave blank to disable."
                >
                  <input
                    type="text"
                    value={String(slackVal('slack.default_channel') ?? '')}
                    placeholder="#incidents"
                    onChange={e => updateSlackEdit('slack.default_channel', e.target.value)}
                    style={inputStyle}
                  />
                </FormField>
                <FormField label="New Incident Notifications">
                  <CheckRow
                    checked={!!slackVal('slack.notify_on_new_incident')}
                    onChange={v => updateSlackEdit('slack.notify_on_new_incident', v)}
                    label="Post to default channel when a new critical or high severity incident is created"
                  />
                </FormField>
                <FormField label="Incident Resolved Notifications">
                  <CheckRow
                    checked={slackVal('slack.notify_on_incident_resolved') !== false && slackVal('slack.notify_on_incident_resolved') !== 'false'}
                    onChange={v => updateSlackEdit('slack.notify_on_incident_resolved', v)}
                    label="Post when an incident is resolved, deployed, rolled back, or rejected"
                  />
                </FormField>
                <FormField label="Approval Required Notifications">
                  <CheckRow
                    checked={slackVal('slack.notify_on_approval_required') !== false && !!slackVal('slack.notify_on_approval_required')}
                    onChange={v => updateSlackEdit('slack.notify_on_approval_required', v)}
                    label="Post interactive approve/reject buttons when an incident needs approval"
                  />
                </FormField>
                <FormField label="Event Storm Notifications">
                  <CheckRow
                    checked={slackVal('slack.notify_on_storm_detected') !== false && slackVal('slack.notify_on_storm_detected') !== 'false'}
                    onChange={v => updateSlackEdit('slack.notify_on_storm_detected', v)}
                    label="Post when the Storm Agent groups incidents into a new event storm"
                  />
                </FormField>
              </div>

              {/* ── Webhook URLs (read-only info) ──── */}
              <div style={{
                marginTop: '0.75rem',
                padding: '1rem 1.1rem',
                backgroundColor: DS.bg,
                border: `1px solid ${DS.border}`,
                borderRadius: 8,
              }}>
                <h4 style={{ fontSize: '0.82rem', fontWeight: 600, color: DS.txtP, margin: '0 0 0.5rem' }}>
                  Inbound Chat Setup
                </h4>
                <p style={{ fontSize: '0.75rem', color: DS.txtS, margin: '0 0 0.5rem' }}>
                  <strong style={{ color: DS.txtM }}>Socket Mode (recommended):</strong>{' '}
                  Paste an App-Level Token above and restart the backend. No public URL needed —
                  the server connects outbound to Slack. Enable Socket Mode in your Slack app under{' '}
                  <a href="https://api.slack.com/apps" target="_blank" rel="noreferrer"
                    style={{ color: DS.accent }}>Settings → Socket Mode</a>.
                </p>
                <p style={{ fontSize: '0.75rem', color: DS.txtS, margin: '0 0 0.75rem' }}>
                  <strong style={{ color: DS.txtM }}>Webhook / Events API (requires public URL):</strong>{' '}
                  Configure these in your Slack app at{' '}
                  <a href="https://api.slack.com/apps" target="_blank" rel="noreferrer"
                    style={{ color: DS.accent }}>api.slack.com/apps</a>.
                  Replace <code style={{ color: DS.txtM }}>{'<host>'}</code> with your public hostname or ngrok URL.
                </p>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                  {[
                    { label: 'Event Subscriptions', path: '/api/webhooks/slack/events' },
                    { label: 'Interactivity & Shortcuts', path: '/api/webhooks/slack/actions' },
                  ].map(({ label, path }) => (
                    <div key={path}>
                      <div style={{ fontSize: '0.72rem', fontWeight: 600, color: DS.txtS, marginBottom: 3 }}>{label}</div>
                      <code style={{
                        display: 'block',
                        fontSize: '0.75rem',
                        color: DS.txtM,
                        backgroundColor: DS.surface,
                        border: `1px solid ${DS.border}`,
                        borderRadius: 5,
                        padding: '5px 10px',
                      }}>
                        {'https://<host>'}{path}
                      </code>
                    </div>
                  ))}
                </div>
              </div>

              {/* ── Test & Save ──── */}
              <div style={{
                borderTop: `1px solid ${DS.border}`,
                paddingTop: '0.85rem',
                marginTop: '0.25rem',
                display: 'flex',
                alignItems: 'center',
                gap: '0.6rem',
              }}>
                <button
                  onClick={handleTestSlack}
                  disabled={slackTesting}
                  style={{
                    ...secondaryBtn,
                    fontSize: '0.78rem',
                    padding: '5px 14px',
                    opacity: slackTesting ? 0.5 : 1,
                    cursor: slackTesting ? 'not-allowed' : 'pointer',
                  }}
                >
                  {slackTesting ? 'Testing…' : 'Test Connection'}
                </button>
                <button
                  onClick={handleSaveSlackSettings}
                  disabled={slackSaving}
                  style={{ ...primaryBtn, opacity: slackSaving ? 0.5 : 1, cursor: slackSaving ? 'not-allowed' : 'pointer' }}
                >
                  {slackSaving ? 'Saving…' : 'Save Slack Settings'}
                </button>
                {slackTestResult && (
                  <span style={{
                    fontSize: '0.78rem',
                    color: slackTestResult.ok ? '#34d399' : '#f87171',
                  }}>
                    {slackTestResult.ok ? '✓' : '✗'} {slackTestResult.message}
                  </span>
                )}
              </div>

            </div>
          )}
        </SettingSection>

        {/* ── Notification Teams ─────────────────────────────────────── */}
        <div style={{
          backgroundColor: DS.surface,
          border: `1px solid ${DS.border}`,
          borderRadius: 10,
          overflow: 'hidden',
        }}>
          <NotificationTeams isExpanded={expandedSections.has('notification-teams')} onToggle={() => toggleSection('notification-teams')} />
        </div>

        {/* ── About ── */}
        <div style={{
          borderTop: `1px solid ${DS.border}`,
          marginTop: '0.5rem',
          paddingTop: '1.25rem',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}>
          <div>
            <span style={{ fontSize: '0.78rem', fontWeight: 700, color: DS.txtP, letterSpacing: '0.03em' }}>
              Agentic Platform
            </span>
            <span style={{ fontSize: '0.72rem', color: DS.txtS, marginLeft: '0.5rem' }}>
              v{APP_VERSION}
            </span>
          </div>
          <div style={{ fontSize: '0.7rem', color: DS.txtS, textAlign: 'right' }}>
            <span style={{ fontFamily: 'monospace', letterSpacing: '0.02em' }}>
              {__BUILD_DATE__} · #{__GIT_COMMIT__}
            </span>
            <span> · </span>
            <a
              href="https://github.com/bsmike1/agentic-platformi-v2/releases"
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: DS.accent, textDecoration: 'none' }}
            >
              Release notes ↗
            </a>
          </div>
        </div>

      </div>
    </div>
  )
}

// ── Sub-components ─────────────────────────────────────────────────────────────

interface SettingSectionProps {
  title: string
  isExpanded: boolean
  onToggle: () => void
  children: React.ReactNode
}

function SettingSection({ title, isExpanded, onToggle, children }: SettingSectionProps) {
  return (
    <div style={{
      backgroundColor: DS.surface,
      border: `1px solid ${DS.border}`,
      borderRadius: 10,
      overflow: 'hidden',
    }}>
      <button
        onClick={onToggle}
        style={{
          width: '100%',
          padding: '0.8rem 1.25rem',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          background: 'none',
          border: 'none',
          cursor: 'pointer',
          color: DS.txtP,
        }}
      >
        <span style={{ fontWeight: 600, fontSize: '0.9rem', letterSpacing: '0.01em' }}>{title}</span>
        <span style={{
          color: DS.txtS,
          fontSize: '0.7rem',
          display: 'inline-block',
          transform: isExpanded ? 'rotate(180deg)' : 'none',
          transition: 'transform 0.18s ease',
        }}>▼</span>
      </button>
      {isExpanded && (
        <div style={{ borderTop: `1px solid ${DS.border}`, padding: '1.1rem 1.25rem 1.25rem' }}>
          {children}
        </div>
      )}
    </div>
  )
}

interface FormFieldProps {
  label: string
  hint?: string
  children: React.ReactNode
}

function FormField({ label, hint, children }: FormFieldProps) {
  return (
    <div style={{ marginBottom: '1rem' }}>
      <label style={{ display: 'block', fontSize: '0.78rem', fontWeight: 600, color: DS.txtM, marginBottom: '0.35rem' }}>
        {label}
      </label>
      {children}
      {hint && <p style={{ fontSize: '0.7rem', color: DS.txtS, margin: '3px 0 0' }}>{hint}</p>}
    </div>
  )
}

function CheckRow({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <label style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer' }}>
      <input
        type="checkbox"
        checked={checked}
        onChange={e => onChange(e.target.checked)}
        style={{ width: 15, height: 15, accentColor: DS.accent }}
      />
      <span style={{ fontSize: '0.83rem', color: DS.txtM }}>{label}</span>
    </label>
  )
}

function Banner({ type, children }: { type: 'error' | 'success'; children: React.ReactNode }) {
  const isErr = type === 'error'
  return (
    <div style={{
      marginBottom: '0.85rem',
      padding: '0.55rem 0.85rem',
      borderRadius: 7,
      backgroundColor: isErr ? 'rgba(239,68,68,0.10)' : 'rgba(16,185,129,0.10)',
      border: `1px solid ${isErr ? 'rgba(239,68,68,0.28)' : 'rgba(16,185,129,0.28)'}`,
      color: isErr ? '#f87171' : '#34d399',
      display: 'flex', alignItems: 'center', gap: 7,
      fontSize: '0.82rem',
    }}>
      {children}
    </div>
  )
}

function SectionSaveBar({ onSave, saving, label = 'Save Settings' }: {
  onSave: () => void
  saving: boolean
  label?: string
}) {
  return (
    <div style={{ borderTop: `1px solid ${DS.border}`, paddingTop: '0.85rem', marginTop: '0.25rem' }}>
      <button
        onClick={onSave}
        disabled={saving}
        style={{ ...primaryBtn, opacity: saving ? 0.5 : 1, cursor: saving ? 'not-allowed' : 'pointer' }}
      >
        {saving ? 'Saving…' : label}
      </button>
    </div>
  )
}
