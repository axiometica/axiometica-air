import { useState, useEffect } from 'react'

interface AnimatedNumberProps {
  value: number
  duration?: number
  decimals?: number
  prefix?: string
  suffix?: string
}

/**
 * Component that animates a number from 0 to the target value
 * Creates a smooth count-up effect for metrics displays
 */
export default function AnimatedNumber({
  value,
  duration = 1000,
  decimals = 0,
  prefix = '',
  suffix = '',
}: AnimatedNumberProps) {
  const [displayValue, setDisplayValue] = useState(0)

  useEffect(() => {
    // Reset and start animation when value changes
    setDisplayValue(0)

    const startTime = Date.now()
    const targetValue = value

    const animate = () => {
      const elapsed = Date.now() - startTime
      const progress = Math.min(elapsed / duration, 1)

      // Easing function for smooth animation
      const easedProgress = progress < 0.5
        ? 2 * progress * progress
        : -1 + (4 - 2 * progress) * progress

      const currentValue = Math.floor(targetValue * easedProgress)
      setDisplayValue(currentValue)

      if (progress < 1) {
        requestAnimationFrame(animate)
      } else {
        setDisplayValue(targetValue)
      }
    }

    const frameId = requestAnimationFrame(animate)

    return () => cancelAnimationFrame(frameId)
  }, [value, duration])

  const formattedValue = displayValue.toFixed(decimals)

  return (
    <>
      {prefix}
      <span className="animate-count-up">{formattedValue}</span>
      {suffix}
    </>
  )
}
