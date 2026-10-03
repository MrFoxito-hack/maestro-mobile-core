import { useRef } from 'react'
import { api } from '@/lib/api'

export function useIdempotentPost() {
  const pending = useRef<{ signature: string; key: string } | null>(null)
  return async function post<T>(path: string, body: unknown): Promise<T> {
    const signature = JSON.stringify([path, body])
    if (pending.current?.signature !== signature) {
      pending.current = { signature, key: crypto.randomUUID() }
    }
    const key = pending.current.key
    const response = await api.post<T>(path, body, {
      headers: { 'Idempotency-Key': key },
    })
    if (pending.current?.key === key) pending.current = null
    return response.data
  }
}
