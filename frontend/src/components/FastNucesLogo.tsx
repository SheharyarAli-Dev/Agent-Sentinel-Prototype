/**
 * src/components/FastNucesLogo.tsx
 * ────────────────────────────────
 * FAST NUCES logo matching the top left of the screenshot:
 *  - Circular seal/emblem icon
 *  - Text "FAST NUCES" in bold clean uppercase lettering
 */
import React from 'react'

export const FastNucesLogo: React.FC = () => {
  return (
    <div className="flex items-center gap-2.5 select-none">
      {/* Circular Emblem Seal */}
      <div className="w-8 h-8 rounded-full bg-gradient-to-tr from-[#0284C7] via-[#0EA5E9] to-[#38BDF8] p-0.5 shadow-sm flex items-center justify-center">
        <div className="w-full h-full rounded-full bg-white flex items-center justify-center relative overflow-hidden">
          {/* Globe / Crest Inner Graphic */}
          <svg viewBox="0 0 32 32" className="w-6 h-6 text-[#0284C7]" fill="none" stroke="currentColor">
            <circle cx="16" cy="16" r="12" strokeWidth="1.8" />
            <ellipse cx="16" cy="16" rx="12" ry="5" strokeWidth="1.2" />
            <path d="M16 4 V28" strokeWidth="1.2" />
            <path d="M4 16 H28" strokeWidth="1.2" />
          </svg>
        </div>
      </div>

      {/* FAST NUCES Brand Text */}
      <span className="text-base font-extrabold tracking-tight text-[#0F0F0F] font-sans">
        FAST NUCES
      </span>
    </div>
  )
}
