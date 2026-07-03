import DashboardMetrics from './DashboardMetrics'
import IncidentTrendChart from './IncidentTrendChart'
import MTTRBreakdownCard from './MTTRBreakdownCard'
import PlatformIntelCard from './PlatformIntelCard'

interface DashboardProps {
  onViewWorkflow: (workflowId: string) => void
  onNavigateToIncidents?: (filter: 'all' | 'active') => void
  onNavigate?: (view: string) => void
  darkMode?: boolean
  incidentFilter?: 'all' | 'active'
}

export default function Dashboard({ onNavigateToIncidents, onNavigate, darkMode }: DashboardProps) {
  return (
    <div className="page-transition-enter space-y-6">
      <DashboardMetrics onMetricClick={onNavigateToIncidents} onNavigate={onNavigate} />

      <PlatformIntelCard onNavigate={onNavigate} darkMode={darkMode} />
      <MTTRBreakdownCard onNavigate={onNavigate} />

      <IncidentTrendChart />
    </div>
  )
}
