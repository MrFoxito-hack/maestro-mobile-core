import { ReactNode } from 'react'

type AuthLayoutProps = {
  children: ReactNode
}

export function AuthLayout({ children }: AuthLayoutProps) {
  return (
    <div className='relative min-h-screen w-full flex items-center justify-center lg:justify-start lg:pl-24 xl:pl-32 overflow-hidden bg-black text-foreground select-none'>
      {/* Background wallpaper with balanced dark treatment so details and characters are clearly visible */}
      <div
        className='absolute inset-0 bg-cover bg-[center_right] pointer-events-none filter brightness-[0.65] contrast-[1.06] saturate-[0.83]'
        style={{ backgroundImage: "url('/images/login-wallpaper.png')" }}
      />

      {/* Smooth gradient on the left to ground the form without dimming the characters on the right */}
      <div className='absolute inset-0 pointer-events-none bg-gradient-to-r from-black/85 via-black/40 to-transparent lg:w-[50%]' />
      <div className='absolute inset-0 pointer-events-none bg-black/25 lg:hidden' />

      {/* Minimalist Floating Glass Card */}
      <div className='relative z-10 w-full max-w-[340px] mx-4 p-6 sm:p-7 rounded-2xl bg-black/65 backdrop-blur-2xl border border-white/10 shadow-[0_25px_60px_rgba(0,0,0,0.95)]'>
        {children}
      </div>
    </div>
  )
}

