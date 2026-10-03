import { cn } from '@/lib/utils'

type LogoProps = React.ImgHTMLAttributes<HTMLImageElement>

export function Logo({ className, ...props }: LogoProps) {
  return (
    <div className={cn('flex aspect-square size-7 shrink-0 items-center justify-center overflow-hidden rounded-lg', className)}>
      <img
        src='/images/logo_final.png'
        alt='EMS Logo'
        className='size-full object-contain'
        {...props}
      />
    </div>
  )
}
