/**
 * hooks/useWebSocket.ts
 * ─────────────────────
 * Custom React hook for the Risk Gatekeeper live event feed WebSocket.
 *
 * Features:
 *  - Auto-connects to the backend /ws endpoint on mount
 *  - Auto-reconnects with exponential backoff on disconnect
 *  - Returns connection status and the stream of received messages
 *  - Provides a manual disconnect function for cleanup
 */
import { useEffect, useRef, useState, useCallback } from 'react'
import type { EvaluateResponse } from '../lib/api'

// ── Types ──────────────────────────────────────────────────────────────────────

export type WsStatus = 'connecting' | 'connected' | 'disconnected' | 'error'

export interface NewDecisionMessage {
  type: 'new_decision'
  event: EvaluateResponse['event']
  decision: EvaluateResponse['decision']
}

export interface HumanDecisionMessage {
  type: 'human_decision'
  event_id: number
  decision: EvaluateResponse['decision']
}

export interface HumanUnblockMessage {
  type: 'human_unblock'
  event_id: number
  decision: EvaluateResponse['decision']
}

export type WsMessage = NewDecisionMessage | HumanDecisionMessage | HumanUnblockMessage

// ── Hook ───────────────────────────────────────────────────────────────────────

const WS_URL = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws`
const MAX_RECONNECT_DELAY_MS = 10_000
const BASE_RECONNECT_DELAY_MS = 1_000

export function useWebSocket() {
  const [status, setStatus] = useState<WsStatus>('connecting')
  const [messages, setMessages] = useState<WsMessage[]>([])
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const reconnectAttemptRef = useRef(0)
  const isMountedRef = useRef(true)

  const connect = useCallback(() => {
    if (!isMountedRef.current) return

    setStatus('connecting')
    const ws = new WebSocket(WS_URL)
    wsRef.current = ws

    ws.onopen = () => {
      if (!isMountedRef.current) return
      setStatus('connected')
      reconnectAttemptRef.current = 0
    }

    ws.onmessage = (event) => {
      if (!isMountedRef.current) return
      try {
        const msg = JSON.parse(event.data) as WsMessage
        setMessages((prev) => [msg, ...prev].slice(0, 200)) // keep last 200 messages
      } catch {
        // Ignore malformed messages
      }
    }

    ws.onclose = () => {
      if (!isMountedRef.current) return
      setStatus('disconnected')
      wsRef.current = null

      // Exponential backoff reconnect
      const attempt = reconnectAttemptRef.current
      const delay = Math.min(
        BASE_RECONNECT_DELAY_MS * Math.pow(2, attempt),
        MAX_RECONNECT_DELAY_MS,
      )
      reconnectAttemptRef.current += 1
      reconnectTimeoutRef.current = setTimeout(connect, delay)
    }

    ws.onerror = () => {
      if (!isMountedRef.current) return
      setStatus('error')
      ws.close()
    }
  }, [])

  useEffect(() => {
    isMountedRef.current = true
    connect()

    return () => {
      isMountedRef.current = false
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current)
      }
      if (wsRef.current) {
        wsRef.current.close()
        wsRef.current = null
      }
    }
  }, [connect])

  const disconnect = useCallback(() => {
    isMountedRef.current = false
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current)
    }
    if (wsRef.current) {
      wsRef.current.close()
      wsRef.current = null
    }
    setStatus('disconnected')
  }, [])

  const clearMessages = useCallback(() => setMessages([]), [])

  return { status, messages, disconnect, clearMessages }
}
