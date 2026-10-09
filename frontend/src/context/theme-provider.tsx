import { createContext, useContext, useEffect, useState } from 'react'
import { setCookie } from '@/lib/cookies'

export type Theme = 'dark'
export type ResolvedTheme = 'dark'

const DEFAULT_THEME: Theme = 'dark'
const THEME_COOKIE_NAME = 'vite-ui-theme'
const THEME_COOKIE_MAX_AGE = 60 * 60 * 24 * 365 // 1 year

type ThemeProviderProps = {
  children: React.ReactNode
  defaultTheme?: Theme
  storageKey?: string
}

type ThemeProviderState = {
  defaultTheme: Theme
  resolvedTheme: ResolvedTheme
  theme: Theme
  setTheme: (theme?: string) => void
  resetTheme: () => void
}

const initialState: ThemeProviderState = {
  defaultTheme: DEFAULT_THEME,
  resolvedTheme: 'dark',
  theme: DEFAULT_THEME,
  setTheme: () => null,
  resetTheme: () => null,
}

const ThemeContext = createContext<ThemeProviderState>(initialState)

export function ThemeProvider({
  children,
  defaultTheme = DEFAULT_THEME,
  storageKey = THEME_COOKIE_NAME,
  ...props
}: ThemeProviderProps) {
  const [theme, _setTheme] = useState<Theme>('dark')

  useEffect(() => {
    const root = window.document.documentElement
    root.classList.remove('light')
    root.classList.add('dark')
    setCookie(storageKey, 'dark', THEME_COOKIE_MAX_AGE)

    const metaThemeColor = document.querySelector("meta[name='theme-color']")
    if (metaThemeColor) {
      metaThemeColor.setAttribute('content', '#020817')
    }
  }, [storageKey])

  const setTheme = (_newTheme?: string) => {
    setCookie(storageKey, 'dark', THEME_COOKIE_MAX_AGE)
    _setTheme('dark')
    const root = window.document.documentElement
    root.classList.remove('light')
    root.classList.add('dark')
  }

  const resetTheme = () => {
    setCookie(storageKey, 'dark', THEME_COOKIE_MAX_AGE)
    _setTheme('dark')
    const root = window.document.documentElement
    root.classList.remove('light')
    root.classList.add('dark')
  }

  const contextValue = {
    defaultTheme,
    resolvedTheme: 'dark' as const,
    resetTheme,
    theme,
    setTheme,
  }

  return (
    <ThemeContext value={contextValue} {...props}>
      {children}
    </ThemeContext>
  )
}

// eslint-disable-next-line react-refresh/only-export-components
export const useTheme = () => {
  const context = useContext(ThemeContext)

  if (!context) throw new Error('useTheme must be used within a ThemeProvider')

  return context
}
