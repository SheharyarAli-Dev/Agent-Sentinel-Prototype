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

    // Dot color palette — subtle cyan, teal, blue, slate variation
    const dotColors = [
      [148, 163, 184], // slate-400
      [100, 170, 170], // teal-ish
      [96, 165, 250],  // blue-400
      [120, 160, 180], // blue-gray
      [34, 211, 238],  // cyan-400 (rare, for selected clusters)
    ]

    const draw = () => {
      ctx.clearRect(0, 0, width, height)

      // Depth gradient — subtle navy-blue variation across page
      const gradient = ctx.createRadialGradient(
        width / 2,
        height * 0.45,
        0,
        width / 2,
        height * 0.45,
        Math.max(width, height) * 0.7
      )
      gradient.addColorStop(0, 'rgba(14, 22, 40, 0.55)')
      gradient.addColorStop(0.3, 'rgba(10, 17, 32, 0.50)')
      gradient.addColorStop(0.6, 'rgba(8, 13, 25, 0.55)')
      gradient.addColorStop(1, 'rgba(5, 8, 16, 0.70)')
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

          // Quieter central reading zone — lower opacity behind hero text
          const cx = width / 2
          const cy = height * 0.38
          const centerDx = (x - cx) / (width * 0.32)
          const centerDy = (y - cy) / (height * 0.22)
          const centerDist = Math.sqrt(centerDx * centerDx + centerDy * centerDy)
          if (centerDist < 1) {
            alpha *= 0.35 + centerDist * 0.65
            r *= 0.7 + centerDist * 0.3
          }

          if (mouse.active) {
            const dx = mouse.x - x
            const dy = mouse.y - y
            const dist = Math.sqrt(dx * dx + dy * dy)

            if (dist < effectRadius) {
              const factor = 1 - dist / effectRadius
              alpha = Math.max(alpha, 0.15 + factor * 0.5)
              r = Math.max(r, baseRadius + factor * 1.4)
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
          {/* Circuit line 1 — secondary teal */}
          <path d="M 0 50 H 50 L 100 15 H 195" stroke="rgba(20, 184, 166, 0.35)" strokeWidth="1.6" />
          <circle cx="195" cy="15" r="4" fill="rgba(34, 211, 238, 0.40)" />

          {/* Circuit line 2 — ambient slate */}
          <path d="M 0 90 H 70 L 130 45 H 245" stroke="rgba(71, 85, 105, 0.22)" strokeWidth="1.6" />
          <circle cx="245" cy="45" r="4" fill="rgba(100, 116, 139, 0.32)" />

          {/* Circuit line 3 — secondary blue */}
          <path d="M 0 135 H 110 L 180 65 H 290 L 320 35 H 405" stroke="rgba(59, 130, 246, 0.32)" strokeWidth="1.6" />
          <circle cx="405" cy="35" r="4" fill="rgba(59, 130, 246, 0.38)" />

          {/* Circuit line 4 — secondary cyan */}
          <path d="M 0 180 H 150 L 200 130 H 360" stroke="rgba(34, 211, 238, 0.35)" strokeWidth="1.6" />
          <circle cx="360" cy="130" r="4" fill="rgba(34, 211, 238, 0.42)" />

          {/* Circuit line 5 — primary governance path, cyan */}
          <path d="M 0 260 H 310" stroke="rgba(34, 211, 238, 0.48)" strokeWidth="1.8" />
          <circle cx="310" cy="260" r="4.5" fill="rgba(34, 211, 238, 0.62)" />

          {/* Circuit line 6 — ambient slate-blue */}
          <path d="M 0 340 H 150 L 200 390 H 360" stroke="rgba(71, 85, 105, 0.22)" strokeWidth="1.6" />
          <circle cx="360" cy="390" r="4" fill="rgba(100, 116, 139, 0.30)" />

          {/* Circuit line 7 — secondary blue */}
          <path d="M 0 385 H 110 L 180 455 H 290 L 320 485 H 405" stroke="rgba(59, 130, 246, 0.30)" strokeWidth="1.6" />
          <circle cx="405" cy="485" r="4" fill="rgba(59, 130, 246, 0.36)" />

          {/* Circuit line 8 — ambient slate */}
          <path d="M 0 430 H 70 L 130 475 H 245" stroke="rgba(71, 85, 105, 0.20)" strokeWidth="1.6" />
          <circle cx="245" cy="475" r="4" fill="rgba(100, 116, 139, 0.30)" />

          {/* Circuit line 9 — secondary teal */}
          <path d="M 0 470 H 50 L 100 505 H 195" stroke="rgba(20, 184, 166, 0.35)" strokeWidth="1.6" />
          <circle cx="195" cy="505" r="4" fill="rgba(20, 184, 166, 0.40)" />
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
          {/* Circuit line 1 — secondary cyan */}
          <path d="M 420 50 H 370 L 320 15 H 225" stroke="rgba(34, 211, 238, 0.35)" strokeWidth="1.6" />
          <circle cx="225" cy="15" r="4" fill="rgba(34, 211, 238, 0.42)" />

          {/* Circuit line 2 — secondary blue */}
          <path d="M 420 90 H 350 L 290 45 H 175" stroke="rgba(59, 130, 246, 0.32)" strokeWidth="1.6" />
          <circle cx="175" cy="45" r="4" fill="rgba(59, 130, 246, 0.38)" />

          {/* Circuit line 3 — ambient slate */}
          <path d="M 420 135 H 310 L 240 65 H 130 L 100 35 H 15" stroke="rgba(71, 85, 105, 0.22)" strokeWidth="1.6" />
          <circle cx="15" cy="35" r="4" fill="rgba(100, 116, 139, 0.32)" />

          {/* Circuit line 4 — secondary teal */}
          <path d="M 420 180 H 270 L 220 130 H 60" stroke="rgba(20, 184, 166, 0.35)" strokeWidth="1.6" />
          <circle cx="60" cy="130" r="4" fill="rgba(20, 184, 166, 0.40)" />

          {/* Circuit line 5 — primary governance path, cyan */}
          <path d="M 420 260 H 110" stroke="rgba(34, 211, 238, 0.48)" strokeWidth="1.8" />
          <circle cx="110" cy="260" r="4.5" fill="rgba(34, 211, 238, 0.62)" />

          {/* Circuit line 6 — ambient slate-blue */}
          <path d="M 420 340 H 270 L 220 390 H 60" stroke="rgba(71, 85, 105, 0.20)" strokeWidth="1.6" />
          <circle cx="60" cy="390" r="4" fill="rgba(100, 116, 139, 0.30)" />

          {/* Circuit line 7 — ambient slate */}
          <path d="M 420 385 H 310 L 240 455 H 130 L 100 485 H 15" stroke="rgba(71, 85, 105, 0.18)" strokeWidth="1.6" />
          <circle cx="15" cy="485" r="4" fill="rgba(100, 116, 139, 0.30)" />

          {/* Circuit line 8 — secondary cyan */}
          <path d="M 420 430 H 350 L 290 475 H 175" stroke="rgba(34, 211, 238, 0.35)" strokeWidth="1.6" />
          <circle cx="175" cy="475" r="4" fill="rgba(34, 211, 238, 0.40)" />

          {/* Circuit line 9 — secondary teal */}
          <path d="M 420 470 H 370 L 320 505 H 225" stroke="rgba(20, 184, 166, 0.35)" strokeWidth="1.6" />
          <circle cx="225" cy="505" r="4" fill="rgba(20, 184, 166, 0.40)" />
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
