/**
 * Professional Icon System
 * Tabler Icons - optimized for enterprise incident management dashboard
 */
import {
  IconMenu,
  IconX,
  IconLayoutDashboard,
  IconAlertTriangle,
  IconCircleCheck,
  IconClock,
  IconTrendingUp,
  IconActivity,
  IconChartBar,
  IconUsers,
  IconSettings,
  IconCheck,
  IconAlertOctagon,
  IconBook,
  IconShieldCheck,
  IconNetwork,
  IconPlugConnected,
  IconRadar,
  IconClipboardList,
  IconScale,
  IconServer,
  IconBolt,
  IconBrain,
  IconTags,
} from './icons'

export interface IconProps {
  className?: string
  size?: number
  strokeWidth?: number
}

// Navigation & UI Icons
export const MenuIcon = ({ className = 'w-6 h-6', size = 24, strokeWidth = 2 }: IconProps) => (
  <IconMenu size={size} strokeWidth={strokeWidth} className={className} />
)

export const XIcon = ({ className = 'w-6 h-6', size = 24, strokeWidth = 2 }: IconProps) => (
  <IconX size={size} strokeWidth={strokeWidth} className={className} />
)

export const DashboardIcon = ({ className = 'w-5 h-5', size = 20, strokeWidth = 2 }: IconProps) => (
  <IconLayoutDashboard size={size} strokeWidth={strokeWidth} className={className} />
)

export const AlertIcon = ({ className = 'w-5 h-5', size = 20, strokeWidth = 2 }: IconProps) => (
  <IconAlertTriangle size={size} strokeWidth={strokeWidth} className={className} />
)

export const CheckCircleIcon = ({ className = 'w-5 h-5', size = 20, strokeWidth = 2 }: IconProps) => (
  <IconCircleCheck size={size} strokeWidth={strokeWidth} className={className} />
)

export const ClockIcon = ({ className = 'w-5 h-5', size = 20, strokeWidth = 2 }: IconProps) => (
  <IconClock size={size} strokeWidth={strokeWidth} className={className} />
)

export const TrendingUpIcon = ({ className = 'w-5 h-5', size = 20, strokeWidth = 2 }: IconProps) => (
  <IconTrendingUp size={size} strokeWidth={strokeWidth} className={className} />
)

// Sidebar Navigation Icons
export const IncidentsIcon = ({ className = 'w-5 h-5', size = 20, strokeWidth = 2 }: IconProps) => (
  <IconAlertTriangle size={size} strokeWidth={strokeWidth} className={className} />
)

export const ActivityIcon = ({ className = 'w-5 h-5', size = 20, strokeWidth = 2 }: IconProps) => (
  <IconActivity size={size} strokeWidth={strokeWidth} className={className} />
)

export const AnalyticsIcon = ({ className = 'w-5 h-5', size = 20, strokeWidth = 2 }: IconProps) => (
  <IconChartBar size={size} strokeWidth={strokeWidth} className={className} />
)

export const TeamIcon = ({ className = 'w-5 h-5', size = 20, strokeWidth = 2 }: IconProps) => (
  <IconUsers size={size} strokeWidth={strokeWidth} className={className} />
)

export const SettingsIcon = ({ className = 'w-5 h-5', size = 20, strokeWidth = 2 }: IconProps) => (
  <IconSettings size={size} strokeWidth={strokeWidth} className={className} />
)

export const RunbookIcon = ({ className = 'w-5 h-5', size = 20, strokeWidth = 2 }: IconProps) => (
  <IconBook size={size} strokeWidth={strokeWidth} className={className} />
)

export const ShieldCheckIcon = ({ className = 'w-5 h-5', size = 20, strokeWidth = 2 }: IconProps) => (
  <IconShieldCheck size={size} strokeWidth={strokeWidth} className={className} />
)

export const CMDBIcon = ({ className = 'w-5 h-5', size = 20, strokeWidth = 2 }: IconProps) => (
  <IconNetwork size={size} strokeWidth={strokeWidth} className={className} />
)

export const MonitorIcon = ({ className = 'w-5 h-5', size = 20, strokeWidth = 2 }: IconProps) => (
  <IconRadar size={size} strokeWidth={strokeWidth} className={className} />
)

export const ConnectorIcon = ({ className = 'w-5 h-5', size = 20, strokeWidth = 2 }: IconProps) => (
  <IconPlugConnected size={size} strokeWidth={strokeWidth} className={className} />
)

export const ChangeIcon = ({ className = 'w-5 h-5', size = 20, strokeWidth = 2 }: IconProps) => (
  <IconClipboardList size={size} strokeWidth={strokeWidth} className={className} />
)

export const PoliciesIcon = ({ className = 'w-5 h-5', size = 20, strokeWidth = 2 }: IconProps) => (
  <IconScale size={size} strokeWidth={strokeWidth} className={className} />
)

export const AdminIcon = ({ className = 'w-5 h-5', size = 20, strokeWidth = 2 }: IconProps) => (
  <IconServer size={size} strokeWidth={strokeWidth} className={className} />
)

export const UsersNavIcon = ({ className = 'w-5 h-5', size = 20, strokeWidth = 2 }: IconProps) => (
  <IconUsers size={size} strokeWidth={strokeWidth} className={className} />
)

export const StormIcon = ({ className = 'w-5 h-5', size = 20, strokeWidth = 2 }: IconProps) => (
  <IconBolt size={size} strokeWidth={strokeWidth} className={className} />
)

export const PlatformIntelIcon = ({ className = 'w-5 h-5', size = 20, strokeWidth = 2 }: IconProps) => (
  <IconBrain size={size} strokeWidth={strokeWidth} className={className} />
)

export const EventTypesIcon = ({ className = 'w-5 h-5', size = 20, strokeWidth = 2 }: IconProps) => (
  <IconTags size={size} strokeWidth={strokeWidth} className={className} />
)

// Status Icons
export const CheckIcon = ({ className = 'w-5 h-5', size = 20, strokeWidth = 2 }: IconProps) => (
  <IconCheck size={size} strokeWidth={strokeWidth} className={className} />
)

export const ExclamationIcon = ({ className = 'w-5 h-5', size = 20, strokeWidth = 2 }: IconProps) => (
  <IconAlertOctagon size={size} strokeWidth={strokeWidth} className={className} />
)

// Metric Icons with background
export const MetricIconWrapper = ({
  children,
  bgColor = 'bg-info-500',
  className = 'w-12 h-12',
}: {
  children: React.ReactNode
  bgColor?: string
  className?: string
}) => (
  <div className={`${className} ${bgColor} rounded-lg flex items-center justify-center text-white shadow-lg`}>
    {children}
  </div>
)
