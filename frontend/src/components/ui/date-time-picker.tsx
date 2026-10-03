import * as React from 'react'
import { format, isValid } from 'date-fns'
import { es } from 'date-fns/locale'
import { Calendar as CalendarIcon, Clock, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Calendar } from '@/components/ui/calendar'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'

export type DateTimePickerProps = {
  value?: string
  onChange: (value: string) => void
  placeholder?: string
  className?: string
  disabled?: boolean
}

export function DateTimePicker({
  value,
  onChange,
  placeholder = 'Seleccionar fecha...',
  className,
  disabled = false,
}: DateTimePickerProps) {
  const [open, setOpen] = React.useState(false)

  const parsedDate = React.useMemo(() => {
    if (!value) return undefined
    const d = new Date(value)
    return isValid(d) ? d : undefined
  }, [value])

  const [selectedDate, setSelectedDate] = React.useState<Date | undefined>(parsedDate)
  const [hours, setHours] = React.useState<string>(
    parsedDate ? String(parsedDate.getHours()).padStart(2, '0') : '12'
  )
  const [minutes, setMinutes] = React.useState<string>(
    parsedDate ? String(parsedDate.getMinutes()).padStart(2, '0') : '00'
  )

  React.useEffect(() => {
    if (parsedDate) {
      setSelectedDate(parsedDate)
      setHours(String(parsedDate.getHours()).padStart(2, '0'))
      setMinutes(String(parsedDate.getMinutes()).padStart(2, '0'))
    } else {
      setSelectedDate(undefined)
    }
  }, [parsedDate])

  const applyDateTime = (date: Date | undefined, h: string, m: string) => {
    if (!date) {
      onChange('')
      return
    }
    const newDate = new Date(date)
    const parsedH = Math.min(23, Math.max(0, parseInt(h, 10) || 0))
    const parsedM = Math.min(59, Math.max(0, parseInt(m, 10) || 0))
    newDate.setHours(parsedH, parsedM, 0, 0)
    onChange(format(newDate, "yyyy-MM-dd'T'HH:mm"))
  }

  const handleSelectDay = (day: Date | undefined) => {
    setSelectedDate(day)
    if (day) {
      applyDateTime(day, hours, minutes)
    } else {
      onChange('')
    }
  }

  const handleHourChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value.replace(/\D/g, '').slice(0, 2)
    setHours(val)
    if (selectedDate && val.length > 0) {
      applyDateTime(selectedDate, val, minutes)
    }
  }

  const handleMinuteChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value.replace(/\D/g, '').slice(0, 2)
    setMinutes(val)
    if (selectedDate && val.length > 0) {
      applyDateTime(selectedDate, hours, val)
    }
  }

  const handleClear = (e: React.MouseEvent) => {
    e.stopPropagation()
    setSelectedDate(undefined)
    onChange('')
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type='button'
          disabled={disabled}
          className={cn(
            'group flex h-9 w-full min-w-44 items-center justify-between gap-2 rounded-md border border-input bg-background px-3 py-1.5 text-left text-[13px] shadow-xs transition-colors hover:bg-accent/40 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50 cursor-pointer',
            !parsedDate && 'text-muted-foreground',
            className
          )}
        >
          <div className='flex min-w-0 items-center gap-2'>
            <CalendarIcon className='size-3.5 shrink-0 text-muted-foreground transition-colors group-hover:text-foreground' />
            <span className='truncate'>
              {parsedDate
                ? format(parsedDate, "d 'de' MMM, yyyy HH:mm", { locale: es })
                : placeholder}
            </span>
          </div>

          {parsedDate ? (
            <span
              role='button'
              tabIndex={0}
              aria-label='Limpiar fecha'
              onClick={handleClear}
              onKeyDown={(e) => e.key === 'Enter' && handleClear(e as unknown as React.MouseEvent)}
              className='flex size-4.5 shrink-0 items-center justify-center rounded-sm hover:bg-muted-foreground/20 text-muted-foreground hover:text-foreground'
            >
              <X className='size-3' />
            </span>
          ) : (
            <Clock className='size-3 shrink-0 opacity-40' />
          )}
        </button>
      </PopoverTrigger>

      <PopoverContent
        align='start'
        className='z-50 w-fit p-0 rounded-xl border border-border/80 shadow-2xl backdrop-blur-md bg-popover/98 overflow-hidden'
      >
        <div className='p-2 flex justify-center'>
          <Calendar
            mode='single'
            selected={selectedDate}
            onSelect={handleSelectDay}
            locale={es}
            initialFocus
          />
        </div>

        <div className='flex items-center justify-between border-t border-border/50 px-3 py-2 bg-muted/20 gap-3'>
          <div className='flex items-center gap-1.5'>
            <Clock className='size-3.5 text-muted-foreground' />
            <span className='text-xs text-muted-foreground'>Hora:</span>
            <div className='flex items-center gap-1 font-mono text-xs'>
              <input
                type='text'
                inputMode='numeric'
                maxLength={2}
                value={hours}
                onChange={handleHourChange}
                aria-label='Hora'
                className='h-7 w-8 rounded-md border border-input bg-background text-center text-xs font-medium shadow-2xs focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring'
              />
              <span className='font-bold text-muted-foreground'>:</span>
              <input
                type='text'
                inputMode='numeric'
                maxLength={2}
                value={minutes}
                onChange={handleMinuteChange}
                aria-label='Minuto'
                className='h-7 w-8 rounded-md border border-input bg-background text-center text-xs font-medium shadow-2xs focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring'
              />
            </div>
          </div>

          <div className='flex items-center gap-1'>
            <Button
              type='button'
              variant='ghost'
              size='sm'
              className='h-7 text-xs px-2 text-muted-foreground hover:text-foreground cursor-pointer'
              onClick={() => {
                setSelectedDate(undefined)
                onChange('')
                setOpen(false)
              }}
            >
              Limpiar
            </Button>
            <Button
              type='button'
              size='sm'
              className='h-7 text-xs px-3 font-medium cursor-pointer'
              onClick={() => {
                if (selectedDate) {
                  applyDateTime(selectedDate, hours, minutes)
                }
                setOpen(false)
              }}
            >
              Listo
            </Button>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  )
}
