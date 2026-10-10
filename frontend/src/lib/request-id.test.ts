import { afterEach, expect, it, vi } from 'vitest'
import { newRequestId } from './request-id'

afterEach(() => vi.unstubAllGlobals())

it('uses native UUID generation when available', () => {
  const id = '01234567-89ab-4cde-8123-456789abcdef'
  vi.stubGlobal('crypto', { randomUUID: () => id })
  expect(newRequestId()).toBe(id)
})

it('generates a UUIDv4 from secure random bytes on HTTP origins', () => {
  const getRandomValues = vi.fn((bytes: Uint8Array) => bytes.fill(255))
  vi.stubGlobal('crypto', { getRandomValues })
  expect(newRequestId()).toBe('ffffffff-ffff-4fff-bfff-ffffffffffff')
  expect(getRandomValues).toHaveBeenCalledOnce()
})
