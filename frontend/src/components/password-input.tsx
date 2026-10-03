import * as React from 'react'
import { Eye, EyeOff } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from './ui/button'

type PasswordInputProps = Omit<
  React.InputHTMLAttributes<HTMLInputElement>,
  'type'
> & {
  ref?: React.Ref<HTMLInputElement>
}

export function PasswordInput({
  className,
  disabled,
  ref,
  ...props
}: PasswordInputProps) {
  const [showPassword, setShowPassword] = React.useState(false)

  return (
    <div className={cn('relative w-full rounded-md', className)}>
      <input
        type={showPassword ? 'text' : 'password'}
        className='flex h-10 w-full rounded-lg border border-zinc-800 bg-zinc-950/70 px-3 py-2 pr-10 text-sm text-white shadow-xs transition-colors placeholder:text-zinc-600 focus-visible:ring-1 focus-visible:ring-zinc-600 focus-visible:border-zinc-500 focus-visible:outline-hidden disabled:cursor-not-allowed disabled:opacity-50'
        ref={ref}
        disabled={disabled}
        {...props}
      />
      <Button
        type='button'
        size='icon'
        variant='ghost'
        disabled={disabled}
        className='absolute inset-e-1 top-1/2 h-7 w-7 -translate-y-1/2 rounded-md text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/50 cursor-pointer'
        onClick={() => setShowPassword((prev) => !prev)}
      >
        {showPassword ? <Eye size={16} /> : <EyeOff size={16} />}
        <span className='sr-only'>
          {showPassword ? 'Hide password' : 'Show password'}
        </span>
      </Button>
    </div>
  )
}
