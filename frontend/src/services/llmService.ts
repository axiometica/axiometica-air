import axios from 'axios'

export interface LLMConfig {
  provider: string
  api_key: string
  model?: string
}

export interface LLMStatus {
  provider: string
  model?: string
  configured: boolean
  cached_summaries: number
  insights_enabled: boolean
}

export interface SupportedProvider {
  name: string
  models: string[]
  default_model: string
  description: string
}

export const llmService = {
  async getStatus(): Promise<{ data: LLMStatus }> {
    return axios.get('/api/llm/status')
  },

  async setConfig(config: LLMConfig): Promise<{ data: any }> {
    return axios.post('/api/llm/config', config)
  },

  // Pass credentials to test WITHOUT saving (test-before-save flow).
  // If no credentials are supplied, the currently stored config is tested.
  async testConfig(credentials?: Partial<LLMConfig>): Promise<{ data: any }> {
    return axios.post('/api/llm/test', credentials ?? {})
  },

  async getSupportedProviders(): Promise<{ data: { providers: SupportedProvider[] } }> {
    return axios.get('/api/llm/providers')
  },

  async clearCache(): Promise<{ data: any }> {
    return axios.post('/api/llm/clear-cache')
  },

  async setInsightsEnabled(enabled: boolean): Promise<{ data: any }> {
    return axios.patch('/api/llm/insights', { enabled })
  },
}
