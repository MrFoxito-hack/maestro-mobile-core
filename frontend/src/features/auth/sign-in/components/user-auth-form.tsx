import { useState } from 'react'
import { z } from 'zod'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { useNavigate, useRouter } from '@tanstack/react-router'
import { Loader2, LogIn } from 'lucide-react'
import { toast } from 'sonner'
import { useAuthStore } from '@/stores/auth-store'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form'
import { Input } from '@/components/ui/input'
import { PasswordInput } from '@/components/password-input'

const formSchema = z.object({
  username: z.string().min(1, 'Ingrese su usuario.'),
  password: z.string().min(1, 'Ingrese su contraseña.'),
})

function getSafeRedirect(redirect?: string): string {
  if (!redirect) return '/'
  let clean = redirect
  try {
    if (clean.startsWith('http://') || clean.startsWith('https://')) {
      const url = new URL(clean)
      clean = url.pathname + url.search
    }
  } catch {
    return '/'
  }
  if (clean.includes('/sign-in') || clean.startsWith('/(auth)')) {
    return '/'
  }
  if (!clean.startsWith('/')) {
    return `/${clean}`
  }
  return clean
}

interface UserAuthFormProps extends React.HTMLAttributes<HTMLFormElement> {
  redirectTo?: string
}

export function UserAuthForm({
  className,
  redirectTo,
  ...props
}: UserAuthFormProps) {
  const [isLoading, setIsLoading] = useState(false)
  const navigate = useNavigate()
  const router = useRouter()
  const { auth } = useAuthStore()

  const form = useForm<z.infer<typeof formSchema>>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      username: '',
      password: '',
    },
  })

  async function onSubmit(data: z.infer<typeof formSchema>) {
    setIsLoading(true)
    try {
      const response = await api.post('/auth/login', data)
      auth.setUser(response.data.user)
      auth.setAccessToken(response.data.access_token)
      toast.success(`Bienvenido, ${response.data.user.username}`)

      const destination = getSafeRedirect(redirectTo)
      await router.invalidate()
      await navigate({ to: destination as any, replace: true })
    } catch {
      toast.error('Usuario o contraseña incorrectos')
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <Form {...form}>
      <form
        onSubmit={form.handleSubmit(onSubmit)}
        className={cn('space-y-4', className)}
        {...props}
      >
        <FormField
          control={form.control}
          name='username'
          render={({ field }) => (
            <FormItem className='space-y-1.5'>
              <FormLabel className='text-xs font-medium text-zinc-400'>
                Usuario
              </FormLabel>
              <FormControl>
                <Input
                  placeholder='Usuario'
                  autoComplete='username'
                  className='h-10 bg-zinc-950/70 border-zinc-800 text-white placeholder:text-zinc-600 focus-visible:border-zinc-500 focus-visible:ring-zinc-700/50 rounded-lg'
                  {...field}
                />
              </FormControl>
              <FormMessage className='text-xs' />
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name='password'
          render={({ field }) => (
            <FormItem className='relative space-y-1.5'>
              <FormLabel className='text-xs font-medium text-zinc-400'>
                Contraseña
              </FormLabel>
              <FormControl>
                <PasswordInput
                  placeholder='Contraseña'
                  autoComplete='current-password'
                  className='bg-zinc-950/70 border-zinc-800 text-white placeholder:text-zinc-600 focus-visible:border-zinc-500 focus-visible:ring-zinc-700/50 rounded-lg'
                  {...field}
                />
              </FormControl>
              <FormMessage className='text-xs' />
            </FormItem>
          )}
        />

        <Button
          type='submit'
          className='w-full h-10 mt-3 bg-white hover:bg-zinc-200 text-black font-semibold rounded-lg text-sm gap-2 transition-all cursor-pointer shadow-lg active:scale-[0.99]'
          disabled={isLoading}
        >
          {isLoading ? (
            <Loader2 className='size-4 animate-spin' />
          ) : (
            <LogIn className='size-4' />
          )}
          Ingresar
        </Button>
      </form>
    </Form>
  )
}

