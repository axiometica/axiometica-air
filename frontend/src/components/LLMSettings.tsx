import { useState, useEffect } from 'react'
import { llmService, LLMStatus, SupportedProvider } from '../services/llmService'
import { IconCheck, IconAlertTriangle, IconRefresh } from './icons'

interface LLMSettingsProps {
  isExpanded?: boolean
  onToggle?: () => void
}

export default function LLMSettings({ isExpanded = true, onToggle = () => {} }: LLMSettingsProps) {
  const [llmStatus, setLlmStatus]           = useState<LLMStatus | null>(null)
  const [providers, setProviders]           = useState<SupportedProvider[]>([])
  const [selectedProvider, setSelectedProvider] = useState('openai')
  const [selectedModel, setSelectedModel]   = useState('gpt-4o')
  const [apiKey, setApiKey]                 = useState('')
  const [baseUrl, setBaseUrl]               = useState('http://localhost:11434')
  const [insightsEnabled, setInsightsEnabled] = useState(true)
  const [insightsSaving, setInsightsSaving]   = useState(false)
  const [loading, setLoading]               = useState(false)
  const [testing, setTesting]               = useState(false)
  const [testPassed, setTestPassed]         = useState(false)   // unlocks Save button
  const [saveSuccess, setSaveSuccess]       = useState(false)
  const [error, setError]                   = useState<string | null>(null)
  const [testResult, setTestResult]         = useState<string | null>(null)
  const [internalExpanded, setInternalExpanded] = useState(isExpanded)

  useEffect(() => { if (isExpanded !== undefined) setInternalExpanded(isExpanded) }, [isExpanded])

  useEffect(() => {
    if (internalExpanded) { loadLLMStatus(); loadProviders() }
  }, [internalExpanded])

  const isOllama = selectedProvider === 'ollama'
  const currentProvider = providers.find(p => p.name === selectedProvider)

  // Reset "tested" badge whenever credentials change
  useEffect(() => { setTestPassed(false); setTestResult(null); setError(null) }, [apiKey, baseUrl, selectedProvider, selectedModel])

  const loadLLMStatus = async () => {
    try {
      const response = await llmService.getStatus()
      setLlmStatus(response.data)
      if (response.data.provider) setSelectedProvider(response.data.provider)
      if (response.data.model)    setSelectedModel(response.data.model)
      setInsightsEnabled(response.data.insights_enabled ?? true)
    } catch (err) { console.error('Failed to load LLM status:', err) }
  }

  const loadProviders = async () => {
    try {
      const response = await llmService.getSupportedProviders()
      setProviders(response.data.providers)
    } catch (err) { console.error('Failed to load providers:', err) }
  }

  // Step 1: Test credentials without saving
  const handleTestConfig = async () => {
    if (isOllama && !baseUrl.trim()) { setError('Enter the Ollama base URL first'); return }
    if (!isOllama && !apiKey.trim()) { setError('Enter an API key first'); return }
    try {
      setTesting(true); setTestResult(null); setError(null)
      const response = await llmService.testConfig({
        provider: selectedProvider,
        ...(isOllama ? { base_url: baseUrl } : { api_key: apiKey }),
        model: selectedModel,
      })
      setTestResult(response.data.test_summary || 'Connection test passed!')
      setTestPassed(true)
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Test failed — check your API key')
      setTestPassed(false)
    } finally { setTesting(false) }
  }

  // Step 2: Save (only enabled after test passes)
  const handleSaveConfig = async () => {
    if (!testPassed) { setError('Test the credentials first'); return }
    try {
      setLoading(true); setError(null)
      await llmService.setConfig({
        provider: selectedProvider,
        ...(isOllama ? { base_url: baseUrl } : { api_key: apiKey }),
        model: selectedModel,
      })
      setSaveSuccess(true)
      setTimeout(() => setSaveSuccess(false), 3000)
      await loadLLMStatus()
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to save configuration')
    } finally { setLoading(false) }
  }

  const handleInsightsToggle = async (enabled: boolean) => {
    setInsightsEnabled(enabled)
    setInsightsSaving(true)
    try {
      await llmService.setInsightsEnabled(enabled)
    } catch (err) {
      // revert on failure
      setInsightsEnabled(!enabled)
    } finally {
      setInsightsSaving(false)
    }
  }

  const getProviderOptions = () => providers.find(p => p.name === selectedProvider)?.models || []
  const handleToggle = () => { setInternalExpanded(!internalExpanded); onToggle() }

  return (
    <>
      {/* Header */}
      <button
        onClick={handleToggle}
        style={{ width: '100%', padding: '0.8rem 1.25rem', display: 'flex', alignItems: 'center',
          justifyContent: 'space-between', background: 'none', border: 'none', cursor: 'pointer', color: '#e8eef5' }}
      >
        <span style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 600, fontSize: '0.9rem', letterSpacing: '0.01em' }}>
          LLM Provider
          {llmStatus?.configured && (
            <span style={{ fontSize: '0.7rem', fontWeight: 600, padding: '1px 7px', borderRadius: 4, color: '#10b981', backgroundColor: '#064e3b' }}>
              Configured
            </span>
          )}
        </span>
        <span style={{ color: '#7a8ba3', fontSize: '0.7rem', display: 'inline-block',
          transform: internalExpanded ? 'rotate(180deg)' : 'none', transition: 'transform 0.18s ease' }}>▼</span>
      </button>

      {/* Content */}
      {internalExpanded && (
        <div className="px-6 pb-6 border-t space-y-4" style={{ borderColor: '#3d4557' }}>
          <div className="pt-6 space-y-4">

            {/* Provider */}
            <div>
              <label style={{ color: '#a0aec0' }} className="text-xs font-semibold uppercase tracking-wider block mb-2">Provider</label>
              <select value={selectedProvider}
                onChange={(e) => {
                  setSelectedProvider(e.target.value)
                  const p = providers.find(p => p.name === e.target.value)
                  if (p) setSelectedModel(p.default_model)
                  setApiKey(''); setTestPassed(false); setTestResult(null); setError(null)
                }}
                className="w-full px-3 py-2 rounded-lg text-sm"
                style={{ backgroundColor: '#2d3748', color: '#e8eef5', border: '1px solid #3d4557' }}
              >
                {providers.map(p => (
                  <option key={p.name} value={p.name}>
                    {p.name.charAt(0).toUpperCase() + p.name.slice(1)} — {p.description}
                  </option>
                ))}
              </select>
            </div>

            {/* Model */}
            <div>
              <label style={{ color: '#a0aec0' }} className="text-xs font-semibold uppercase tracking-wider block mb-2">Model</label>
              {isOllama ? (
                <>
                  <input type="text" value={selectedModel} onChange={(e) => setSelectedModel(e.target.value)}
                    placeholder="e.g. qwen2.5:3b, llama3:latest"
                    className="w-full px-3 py-2 rounded-lg text-sm"
                    style={{ backgroundColor: '#2d3748', color: '#e8eef5', border: '1px solid #3d4557' }}
                    list="ollama-model-suggestions"
                  />
                  <datalist id="ollama-model-suggestions">
                    {getProviderOptions().map(m => <option key={m} value={m} />)}
                  </datalist>
                  <p style={{ color: '#7a8ba3' }} className="text-xs mt-1">
                    Enter the exact model name as pulled — run <code style={{ color: '#a0aec0' }}>ollama list</code> to see available models.
                  </p>
                </>
              ) : (
                <select value={selectedModel} onChange={(e) => setSelectedModel(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg text-sm"
                  style={{ backgroundColor: '#2d3748', color: '#e8eef5', border: '1px solid #3d4557' }}
                >
                  {getProviderOptions().map(m => <option key={m} value={m}>{m}</option>)}
                </select>
              )}
            </div>

            {/* API Key / Base URL */}
            {isOllama ? (
              <div>
                <label style={{ color: '#a0aec0' }} className="text-xs font-semibold uppercase tracking-wider block mb-2">Ollama Base URL</label>
                <input type="text" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)}
                  placeholder="http://localhost:11434"
                  className="w-full px-3 py-2 rounded-lg text-sm"
                  style={{ backgroundColor: '#2d3748', color: '#e8eef5', border: '1px solid #3d4557' }}
                />
                <p style={{ color: '#7a8ba3' }} className="text-xs mt-1">
                  URL where Ollama is running. No API key needed — model must already be pulled (<code style={{ color: '#a0aec0' }}>ollama pull {selectedModel}</code>).
                </p>
              </div>
            ) : (
              <div>
                <label style={{ color: '#a0aec0' }} className="text-xs font-semibold uppercase tracking-wider block mb-2">API Key</label>
                <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)}
                  placeholder="Enter your API key"
                  className="w-full px-3 py-2 rounded-lg text-sm"
                  style={{ backgroundColor: '#2d3748', color: '#e8eef5', border: '1px solid #3d4557' }}
                />
                <p style={{ color: '#7a8ba3' }} className="text-xs mt-1">
                  Your API key is stored securely in the database and persists across restarts.
                </p>
              </div>
            )}

            {/* Error */}
            {error && (
              <div className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm" style={{ backgroundColor: '#7f1d1d', color: '#f87171' }}>
                <IconAlertTriangle size={16} />{error}
              </div>
            )}

            {/* Save success */}
            {saveSuccess && (
              <div className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm" style={{ backgroundColor: '#064e3b', color: '#6ee7b7' }}>
                <IconCheck size={16} />Configuration saved and active
              </div>
            )}

            {/* Test result */}
            {testResult && (
              <div className="px-3 py-2 rounded-lg text-sm" style={{ backgroundColor: '#064e3b', color: '#6ee7b7' }}>
                <p className="font-semibold mb-1">✓ Test passed — key is valid</p>
                <p className="text-xs">{testResult}</p>
              </div>
            )}

            {/* Current Status */}
            {llmStatus && (
              <div className="px-3 py-2 rounded-lg text-xs" style={{ backgroundColor: '#2d3748', color: '#a0aec0' }}>
                <p><strong>Current Provider:</strong> {llmStatus.provider} {llmStatus.model ? `(${llmStatus.model})` : ''}</p>
                <p><strong>Status:</strong> {llmStatus.configured ? '✓ Configured' : '✗ Not Configured'}</p>
                <p><strong>Cached Summaries:</strong> {llmStatus.cached_summaries}</p>
              </div>
            )}

            {/* AI Insights toggle */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              padding: '10px 12px', borderRadius: 8, backgroundColor: '#1a1f2e', border: '1px solid #3d4557' }}>
              <div>
                <p style={{ color: '#e8eef5', fontSize: '0.85rem', fontWeight: 600, margin: 0 }}>AI Insights</p>
                <p style={{ color: '#7a8ba3', fontSize: '0.75rem', margin: '2px 0 0' }}>
                  Generate per-incident root-cause analysis and remediation hints
                </p>
              </div>
              <button
                onClick={() => handleInsightsToggle(!insightsEnabled)}
                disabled={insightsSaving}
                title={insightsSaving ? 'Saving…' : insightsEnabled ? 'Click to disable' : 'Click to enable'}
                style={{
                  flexShrink: 0,
                  width: 44, height: 24, borderRadius: 12, border: 'none', cursor: insightsSaving ? 'wait' : 'pointer',
                  backgroundColor: insightsEnabled ? '#2563eb' : '#3d4557',
                  position: 'relative', transition: 'background-color 0.2s',
                  opacity: insightsSaving ? 0.6 : 1,
                }}
              >
                <span style={{
                  position: 'absolute', top: 3, width: 18, height: 18, borderRadius: '50%',
                  backgroundColor: '#fff', transition: 'left 0.2s',
                  left: insightsEnabled ? 23 : 3,
                }} />
              </button>
            </div>

            {/* Action Buttons — Test first, then Save */}
            <div style={{ display: 'flex', gap: 8 }}>
              {/* Step 1: Test */}
              <button onClick={handleTestConfig} disabled={testing || (isOllama ? !baseUrl : !apiKey)}
                style={{ flex: 1, padding: '8px 12px', borderRadius: 8, fontWeight: 600, fontSize: '0.85rem',
                  backgroundColor: '#1a1f2e', color: testPassed ? '#10b981' : '#a0aec0',
                  border: testPassed ? '1px solid #10b981' : '1px solid #3d4557',
                  opacity: testing || (isOllama ? !baseUrl : !apiKey) ? 0.5 : 1, cursor: testing || (isOllama ? !baseUrl : !apiKey) ? 'not-allowed' : 'pointer',
                  display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}>
                {testPassed ? <IconCheck size={14} /> : <IconRefresh size={14} className={testing ? 'animate-spin' : ''} />}
                {testing ? 'Testing…' : testPassed ? 'Test Passed' : 'Test Connection'}
              </button>

              {/* Step 2: Save (unlocked after test passes) */}
              <button onClick={handleSaveConfig} disabled={loading || !testPassed}
                title={!testPassed ? 'Test the connection first' : ''}
                style={{ flex: 1, padding: '8px 12px', borderRadius: 8, fontWeight: 600, fontSize: '0.85rem',
                  backgroundColor: testPassed ? '#2563eb' : '#1a1f2e',
                  color: testPassed ? '#ffffff' : '#4a5568',
                  border: testPassed ? 'none' : '1px solid #3d4557',
                  opacity: loading ? 0.7 : 1, cursor: loading || !testPassed ? 'not-allowed' : 'pointer' }}>
                {loading ? 'Saving…' : 'Save Configuration'}
              </button>
            </div>

            <p style={{ color: '#7a8ba3' }} className="text-xs">
              {isOllama
                ? 'Test first to verify Ollama is reachable and the model is pulled, then Save.'
                : 'Test first to validate credentials, then Save to persist them.'}
              {' '}Used for AI-generated incident summaries.
            </p>
          </div>
        </div>
      )}
    </>
  )
}
