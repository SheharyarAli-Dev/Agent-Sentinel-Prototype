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

    // Dot color palette for subtle variation
    const dotColors = [
      [148, 163, 184], // slate-400
      [120, 160, 180], // blue-gray
      [100, 170, 170], // teal-ish
      [130, 150, 175], // slate-blue
    ]

    const draw = () => {
      ctx.clearRect(0, 0, width, height)

      // Depth gradient — center slightly lighter with navy-blue tint
      const gradient = ctx.createRadialGradient(
        width / 2,
        height * 0.45,
        0,
        width / 2,
        height * 0.45,
        Math.max(width, height) * 0.7
      )
      gradient.addColorStop(0, 'rgba(12, 20, 35, 0.5)')
      gradient.addColorStop(0.35, 'rgba(9, 15, 28, 0.45)')
      gradient.addColorStop(0.7, 'rgba(7, 11, 20, 0.55)')
      gradient.addColorStop(1, 'rgba(5, 8, 15, 0.7)')
      ctx.fillStyle = gradient
      ctx.fillRect(0, 0, width, height)

      // Subtle blue radial glow in center
      const centerGlow = ctx.createRadialGradient(
        width / 2,
        height * 0.42,
        0,
        width / 2,
        height * 0.42,
        width * 0.28
      )
      centerGlow.addColorStop(0, 'rgba(34, 211, 238, 0.025)')
      centerGlow.addColorStop(0.5, 'rgba(34, 211, 238, 0.012)')
      centerGlow.addColorStop(1, 'rgba(34, 211, 238, 0)')
      ctx.fillStyle = centerGlow
      ctx.fillRect(0, 0, width, height)

      // Continuous edge-to-edge dots grid across the entire canvas plane
      const mouse = mouseRef.current
      for (let x = 0; x <= width + spacing; x += spacing) {
        for (let y = 0; y <= height + spacing; y += spacing) {
          let alpha = 0.15
          let r = baseRadius

          if (mouse.active) {
            const dx = mouse.x - x
            const dy = mouse.y - y
            const dist = Math.sqrt(dx * dx + dy * dy)

            if (dist < effectRadius) {
              const factor = 1 - dist / effectRadius
              alpha = 0.15 + factor * 0.5
              r = baseRadius + factor * 1.4
            }
          }

          // Subtle color variation per dot based on grid position
          const ci = ((x / spacing + y / spacing) | 0) % dotColors.length
          const c = dotColors[ci]

          ctx.beginPath()
          ctx.arc(x, y, r, 0, Math.PI * 2)
          ctx.fillStyle = `rgba(${c[0]}, ${c[1]}, ${c[2]}, ${alpha})`
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
          className="w-full h-full"
          preserveAspectRatio="none"
        >
          {/* Circuit line 1 — teal */}
          <path d="M 0 50 H 50 L 100 15 H 195" stroke="rgba(20, 184, 166, 0.25)" strokeWidth="1.6" />
          <circle cx="195" cy="15" r="4" fill="rgba(34, 211, 238, 0.35)" />

          {/* Circuit line 2 — slate */}
          <path d="M 0 90 H 70 L 130 45 H 245" stroke="rgba(71, 85, 105, 0.3)" strokeWidth="1.6" />
          <circle cx="245" cy="45" r="4" fill="rgba(100, 116, 139, 0.3)" />

          {/* Circuit line 3 — blue */}
          <path d="M 0 135 H 110 L 180 65 H 290 L 320 35 H 405" stroke="rgba(59, 130, 246, 0.2)" strokeWidth="1.6" />
          <circle cx="405" cy="35" r="4" fill="rgba(59, 130, 246, 0.3)" />

          {/* Circuit line 4 — cyan */}
          <path d="M 0 180 H 150 L 200 130 H 360" stroke="rgba(34, 211, 238, 0.22)" strokeWidth="1.6" />
          <circle cx="360" cy="130" r="4" fill="rgba(34, 211, 238, 0.32)" />

          {/* Circuit line 5 — center horizontal, brighter junction */}
          <path d="M 0 260 H 310" stroke="rgba(34, 211, 238, 0.28)" strokeWidth="1.8" />
          <circle cx="310" cy="260" r="4.5" fill="rgba(34, 211, 238, 0.45)" />

          {/* Circuit line 6 — teal */}
          <path d="M 0 340 H 150 L 200 390 H 360" stroke="rgba(20, 184, 166, 0.22)" strokeWidth="1.6" />
          <circle cx="360" cy="390" r="4" fill="rgba(20, 184, 166, 0.3)" />

          {/* Circuit line 7 — blue */}
          <path d="M 0 385 H 110 L 180 455 H 290 L 320 485 H 405" stroke="rgba(59, 130, 246, 0.18)" strokeWidth="1.6" />
          <circle cx="405" cy="485" r="4" fill="rgba(59, 130, 246, 0.28)" />

          {/* Circuit line 8 — slate */}
          <path d="M 0 430 H 70 L 130 475 H 245" stroke="rgba(71, 85, 105, 0.25)" strokeWidth="1.6" />
          <circle cx="245" cy="475" r="4" fill="rgba(100, 116, 139, 0.28)" />

          {/* Circuit line 9 — teal */}
          <path d="M 0 470 H 50 L 100 505 H 195" stroke="rgba(20, 184, 166, 0.22)" strokeWidth="1.6" />
          <circle cx="195" cy="505" r="4" fill="rgba(20, 184, 166, 0.3)" />
        </svg>
      </div>

      {/* ── Right Circuit Trace Lines (Slide in smoothly from extreme right) ─── */}
      <div className="absolute right-0 top-[8%] w-[38%] max-w-[520px] h-[85vh] animate-slide-in-right">
        <svg
          viewBox="0 0 420 520"
          fill="none"
          xmlns="http://www.w3.org/2000/svg"
          className="w-full h-full"
          preserveAspectRatio="none"
        >
          {/* Circuit line 1 — cyan */}
          <path d="M 420 50 H 370 L 320 15 H 225" stroke="rgba(34, 211, 238, 0.22)" strokeWidth="1.6" />
          <circle cx="225" cy="15" r="4" fill="rgba(34, 211, 238, 0.32)" />

          {/* Circuit line 2 — blue */}
          <path d="M 420 90 H 350 L 290 45 H 175" stroke="rgba(59, 130, 246, 0.2)" strokeWidth="1.6" />
          <circle cx="175" cy="45" r="4" fill="rgba(59, 130, 246, 0.3)" />

          {/* Circuit line 3 — slate */}
          <path d="M 420 135 H 310 L 240 65 H 130 L 100 35 H 15" stroke="rgba(71, 85, 105, 0.28)" strokeWidth="1.6" />
          <circle cx="15" cy="35" r="4" fill="rgba(100, 116, 139, 0.3)" />

          {/* Circuit line 4 — teal */}
          <path d="M 420 180 H 270 L 220 130 H 60" stroke="rgba(20, 184, 166, 0.22)" strokeWidth="1.6" />
          <circle cx="60" cy="130" r="4" fill="rgba(20, 184, 166, 0.3)" />

          {/* Circuit line 5 — center horizontal, brighter junction */}
          <path d="M 420 260 H 110" stroke="rgba(34, 211, 238, 0.28)" strokeWidth="1.8" />
          <circle cx="110" cy="260" r="4.5" fill="rgba(34, 211, 238, 0.45)" />

          {/* Circuit line 6 — blue */}
          <path d="M 420 340 H 270 L 220 390 H 60" stroke="rgba(59, 130, 246, 0.18)" strokeWidth="1.6" />
          <circle cx="60" cy="390" r="4" fill="rgba(59, 130, 246, 0.28)" />

          {/* Circuit line 7 — slate */}
          <path d="M 420 385 H 310 L 240 455 H 130 L 100 485 H 15" stroke="rgba(71, 85, 105, 0.25)" strokeWidth="1.6" />
          <circle cx="15" cy="485" r="4" fill="rgba(100, 116, 139, 0.28)" />

          {/* Circuit line 8 — cyan */}
          <path d="M 420 430 H 350 L 290 475 H 175" stroke="rgba(34, 211, 238, 0.22)" strokeWidth="1.6" />
          <circle cx="175" cy="475" r="4" fill="rgba(34, 211, 238, 0.32)" />

          {/* Circuit line 9 — teal */}
          <path d="M 420 470 H 370 L 320 505 H 225" stroke="rgba(20, 184, 166, 0.22)" strokeWidth="1.6" />
          <circle cx="225" cy="505" r="4" fill="rgba(20, 184, 166, 0.3)" />
        </svg>
      </div>

      {/* Watermark sparkle accent icon — teal, restrained */}
      <div className="absolute right-[10%] bottom-[16%] opacity-30 text-teal-500/35">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor">
          <path d="M12 0L14.59 9.41L24 12L14.59 14.59L12 24L9.41 14.59L0 12L9.41 9.41L12 0Z" />
        </svg>
      </div>
    </div>
  )
}
