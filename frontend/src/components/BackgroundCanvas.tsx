/**
 * src/components/BackgroundCanvas.tsx
 * ────────────────────────────────────
 * Interactive background component:
 *  1. Dots Grid Canvas: Fills 100vw x 100vh continuously in fixed position behind all content.
 *     Mouse movement dynamically increases opacity & radius of dots near pointer position.
 *  2. Circuit Trace Lines (Left & Right): Anchored flush to the absolute left & right screen edges.
 *     Smooth 1.6s slide-in transition on page load (translateX(-100%) -> translateX(0) & translateX(100%) -> translateX(0)).
 */
import React, { useEffect, useRef } from 'react'

export const BackgroundCanvas: React.FC = () => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const mouseRef = useRef<{ x: number; y: number; active: boolean }>({
    x: -1000,
    y: -1000,
    active: false,
  })

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    let animationFrameId: number
    let width = (canvas.width = window.innerWidth)
    let height = (canvas.height = window.innerHeight)

    const handleResize = () => {
      if (!canvas) return
      width = canvas.width = window.innerWidth
      height = canvas.height = window.innerHeight
    }

    const handleMouseMove = (e: MouseEvent) => {
      mouseRef.current.x = e.clientX
      mouseRef.current.y = e.clientY
      mouseRef.current.active = true
    }

    const handleMouseLeave = () => {
      mouseRef.current.active = false
    }

    window.addEventListener('resize', handleResize)
    window.addEventListener('mousemove', handleMouseMove)
    window.addEventListener('mouseleave', handleMouseLeave)

    const spacing = 24
    const baseRadius = 1.2
    const effectRadius = 160

    const draw = () => {
      ctx.clearRect(0, 0, width, height)

      // Ambient warm radial gradient
      const gradient = ctx.createRadialGradient(
        width / 2,
        height / 2,
        60,
        width / 2,
        height / 2,
        Math.max(width, height) / 1.05
      )
      gradient.addColorStop(0, 'rgba(255, 255, 255, 0.5)')
      gradient.addColorStop(0.5, 'rgba(249, 248, 243, 0.3)')
      gradient.addColorStop(1, 'rgba(240, 238, 228, 0.7)')
      ctx.fillStyle = gradient
      ctx.fillRect(0, 0, width, height)

      // Continuous edge-to-edge dots grid across the entire canvas plane
      const mouse = mouseRef.current
      for (let x = 0; x <= width + spacing; x += spacing) {
        for (let y = 0; y <= height + spacing; y += spacing) {
          let alpha = 0.28
          let r = baseRadius

          if (mouse.active) {
            const dx = mouse.x - x
            const dy = mouse.y - y
            const dist = Math.sqrt(dx * dx + dy * dy)

            if (dist < effectRadius) {
              const factor = 1 - dist / effectRadius
              alpha = 0.28 + factor * 0.68
              r = baseRadius + factor * 1.6
            }
          }

          ctx.beginPath()
          ctx.arc(x, y, r, 0, Math.PI * 2)
          ctx.fillStyle = `rgba(30, 30, 30, ${alpha})`
          ctx.fill()
        }
      }

      animationFrameId = requestAnimationFrame(draw)
    }

    draw()

    return () => {
      window.removeEventListener('resize', handleResize)
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('mouseleave', handleMouseLeave)
      cancelAnimationFrame(animationFrameId)
    }
  }, [])

  return (
    <div className="fixed inset-0 pointer-events-none z-0 overflow-hidden select-none">
      {/* ── Full Screen Dots Canvas ────────────────────────────────────────── */}
      <canvas ref={canvasRef} className="absolute inset-0 w-full h-full" />

      {/* ── Left Circuit Trace Lines (Slide in smoothly from extreme left) ──── */}
      <div className="absolute left-0 top-[8%] w-[38%] max-w-[520px] h-[85vh] animate-slide-in-left">
        <svg
          viewBox="0 0 420 520"
          fill="none"
          xmlns="http://www.w3.org/2000/svg"
          className="w-full h-full text-[#6E6F73]"
          preserveAspectRatio="none"
        >
          {/* Circuit line 1 */}
          <path d="M 0 50 H 50 L 100 15 H 195" stroke="currentColor" strokeWidth="1.6" />
          <circle cx="195" cy="15" r="4" fill="currentColor" />

          {/* Circuit line 2 */}
          <path d="M 0 90 H 70 L 130 45 H 245" stroke="currentColor" strokeWidth="1.6" />
          <circle cx="245" cy="45" r="4" fill="currentColor" />

          {/* Circuit line 3 */}
          <path d="M 0 135 H 110 L 180 65 H 290 L 320 35 H 405" stroke="currentColor" strokeWidth="1.6" />
          <circle cx="405" cy="35" r="4" fill="currentColor" />

          {/* Circuit line 4 */}
          <path d="M 0 180 H 150 L 200 130 H 360" stroke="currentColor" strokeWidth="1.6" />
          <circle cx="360" cy="130" r="4" fill="currentColor" />

          {/* Circuit line 5 (center horizontal line) */}
          <path d="M 0 260 H 310" stroke="currentColor" strokeWidth="1.8" />
          <circle cx="310" cy="260" r="4.5" fill="currentColor" />

          {/* Circuit line 6 */}
          <path d="M 0 340 H 150 L 200 390 H 360" stroke="currentColor" strokeWidth="1.6" />
          <circle cx="360" cy="390" r="4" fill="currentColor" />

          {/* Circuit line 7 */}
          <path d="M 0 385 H 110 L 180 455 H 290 L 320 485 H 405" stroke="currentColor" strokeWidth="1.6" />
          <circle cx="405" cy="485" r="4" fill="currentColor" />

          {/* Circuit line 8 */}
          <path d="M 0 430 H 70 L 130 475 H 245" stroke="currentColor" strokeWidth="1.6" />
          <circle cx="245" cy="475" r="4" fill="currentColor" />

          {/* Circuit line 9 */}
          <path d="M 0 470 H 50 L 100 505 H 195" stroke="currentColor" strokeWidth="1.6" />
          <circle cx="195" cy="505" r="4" fill="currentColor" />
        </svg>
      </div>

      {/* ── Right Circuit Trace Lines (Slide in smoothly from extreme right) ─── */}
      <div className="absolute right-0 top-[8%] w-[38%] max-w-[520px] h-[85vh] animate-slide-in-right">
        <svg
          viewBox="0 0 420 520"
          fill="none"
          xmlns="http://www.w3.org/2000/svg"
          className="w-full h-full text-[#6E6F73]"
          preserveAspectRatio="none"
        >
          {/* Circuit line 1 */}
          <path d="M 420 50 H 370 L 320 15 H 225" stroke="currentColor" strokeWidth="1.6" />
          <circle cx="225" cy="15" r="4" fill="currentColor" />

          {/* Circuit line 2 */}
          <path d="M 420 90 H 350 L 290 45 H 175" stroke="currentColor" strokeWidth="1.6" />
          <circle cx="175" cy="45" r="4" fill="currentColor" />

          {/* Circuit line 3 */}
          <path d="M 420 135 H 310 L 240 65 H 130 L 100 35 H 15" stroke="currentColor" strokeWidth="1.6" />
          <circle cx="15" cy="35" r="4" fill="currentColor" />

          {/* Circuit line 4 */}
          <path d="M 420 180 H 270 L 220 130 H 60" stroke="currentColor" strokeWidth="1.6" />
          <circle cx="60" cy="130" r="4" fill="currentColor" />

          {/* Circuit line 5 (center horizontal line) */}
          <path d="M 420 260 H 110" stroke="currentColor" strokeWidth="1.8" />
          <circle cx="110" cy="260" r="4.5" fill="currentColor" />

          {/* Circuit line 6 */}
          <path d="M 420 340 H 270 L 220 390 H 60" stroke="currentColor" strokeWidth="1.6" />
          <circle cx="60" cy="390" r="4" fill="currentColor" />

          {/* Circuit line 7 */}
          <path d="M 420 385 H 310 L 240 455 H 130 L 100 485 H 15" stroke="currentColor" strokeWidth="1.6" />
          <circle cx="15" cy="485" r="4" fill="currentColor" />

          {/* Circuit line 8 */}
          <path d="M 420 430 H 350 L 290 475 H 175" stroke="currentColor" strokeWidth="1.6" />
          <circle cx="175" cy="475" r="4" fill="currentColor" />

          {/* Circuit line 9 */}
          <path d="M 420 470 H 370 L 320 505 H 225" stroke="currentColor" strokeWidth="1.6" />
          <circle cx="225" cy="505" r="4" fill="currentColor" />
        </svg>
      </div>

      {/* Watermark sparkle accent icon */}
      <div className="absolute right-[10%] bottom-[16%] opacity-25 text-neutral-600 animate-pulse-subtle">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor">
          <path d="M12 0L14.59 9.41L24 12L14.59 14.59L12 24L9.41 14.59L0 12L9.41 9.41L12 0Z" />
        </svg>
      </div>
    </div>
  )
}
