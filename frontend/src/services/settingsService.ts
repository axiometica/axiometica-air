/**
 * Application Settings Service
 * Manages persistent user settings using localStorage
 */

export interface EnvironmentSettings {
  apiUrl: string
  debugMode: boolean
  envName: 'development' | 'staging' | 'production'
}

export interface UISettings {
  darkMode: boolean
  sidebarCollapsed: boolean
  compactMode: boolean
}

export interface PerformanceSettings {
  metricsRefreshInterval: number // milliseconds
  autoCleanup: boolean
  cacheEnabled: boolean
}

export interface NotificationSettings {
  enableAlerts: boolean
  emailNotifications: boolean
  soundAlerts: boolean
}

export interface SecuritySettings {
  sessionTimeout: number // minutes
  requireMFA: boolean
}

export interface DatabaseSettings {
  retentionDays: number
  autoVacuum: boolean
}

export interface AppSettings {
  environment: EnvironmentSettings
  ui: UISettings
  performance: PerformanceSettings
  notifications: NotificationSettings
  security: SecuritySettings
  database: DatabaseSettings
}

const SETTINGS_KEY = 'agentic-os-settings'

const DEFAULT_SETTINGS: AppSettings = {
  environment: {
    apiUrl: 'http://localhost:8000/api',
    debugMode: false,
    envName: 'development',
  },
  ui: {
    darkMode: true,
    sidebarCollapsed: false,
    compactMode: false,
  },
  performance: {
    metricsRefreshInterval: 30000, // 30 seconds
    autoCleanup: true,
    cacheEnabled: true,
  },
  notifications: {
    enableAlerts: true,
    emailNotifications: false,
    soundAlerts: false,
  },
  security: {
    sessionTimeout: 60, // 60 minutes
    requireMFA: false,
  },
  database: {
    retentionDays: 90,
    autoVacuum: true,
  },
}

class SettingsService {
  loadSettings(): AppSettings {
    try {
      const stored = localStorage.getItem(SETTINGS_KEY)
      if (stored) {
        return JSON.parse(stored)
      }
    } catch (error) {
      console.error('Error loading settings from localStorage:', error)
    }
    return DEFAULT_SETTINGS
  }

  saveSettings(settings: AppSettings): void {
    try {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings))
    } catch (error) {
      console.error('Error saving settings to localStorage:', error)
    }
  }

  getSetting<T extends keyof AppSettings, K extends keyof AppSettings[T]>(
    section: T,
    key: K
  ): AppSettings[T][K] {
    const settings = this.loadSettings()
    return settings[section][key]
  }

  setSetting<T extends keyof AppSettings, K extends keyof AppSettings[T]>(
    section: T,
    key: K,
    value: AppSettings[T][K]
  ): void {
    const settings = this.loadSettings()
    settings[section][key] = value
    this.saveSettings(settings)
  }

  resetToDefaults(): void {
    this.saveSettings(DEFAULT_SETTINGS)
  }

  getDefaults(): AppSettings {
    return DEFAULT_SETTINGS
  }
}

export const settingsService = new SettingsService()
