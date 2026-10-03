import { useState, useRef, useEffect } from 'react'
import {
  ChevronLeft,
  Search,
  SquarePen,
  Share2,
  MoreHorizontal,
  Copy,
  Phone,
  CheckSquare,
  Camera,
  Pencil,
  Check,
  Pin,
} from 'lucide-react'
import { cn } from '@/lib/utils'

function useDragToScroll() {
  const scrollRef = useRef<HTMLDivElement>(null)
  const [isDragging, setIsDragging] = useState(false)
  const dragStartRef = useRef<{ y: number; scrollTop: number; moved: boolean }>({
    y: 0,
    scrollTop: 0,
    moved: false,
  })

  const handleMouseDown = (e: React.MouseEvent) => {
    if (e.button !== 0) return
    const target = e.target as HTMLElement
    if (target.closest('input, select, textarea, [role="switch"]')) return

    dragStartRef.current = {
      y: e.clientY,
      scrollTop: scrollRef.current?.scrollTop ?? 0,
      moved: false,
    }
    setIsDragging(true)
  }

  useEffect(() => {
    if (!isDragging) return

    const handleMouseMove = (e: MouseEvent) => {
      if (!scrollRef.current) return
      const deltaY = e.clientY - dragStartRef.current.y
      if (Math.abs(deltaY) > 4) {
        dragStartRef.current.moved = true
      }
      scrollRef.current.scrollTop = dragStartRef.current.scrollTop - deltaY
    }

    const handleMouseUp = () => {
      setIsDragging(false)
    }

    window.addEventListener('mousemove', handleMouseMove)
    window.addEventListener('mouseup', handleMouseUp)
    return () => {
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('mouseup', handleMouseUp)
    }
  }, [isDragging])

  const handleClickCapture = (e: React.MouseEvent) => {
    if (dragStartRef.current.moved) {
      e.stopPropagation()
      e.preventDefault()
      dragStartRef.current.moved = false
    }
  }

  return {
    scrollRef,
    isDragging,
    handleMouseDown,
    handleClickCapture,
  }
}

interface NotesAppProps {
  onClose: () => void
  onOpenPhone?: (code?: string) => void
  onTriggerIslandAlert?: (msg: string) => void
  imsi?: string
}

export function NotesApp({
  onClose,
  onOpenPhone,
  onTriggerIslandAlert,
  imsi = '999700000000001',
}: NotesAppProps) {
  const listDrag = useDragToScroll()
  const detailDrag = useDragToScroll()
  const [currentView, setCurrentView] = useState<'detail' | 'list'>('detail')
  const [searchQuery, setSearchQuery] = useState('')
  const [copiedCode, setCopiedCode] = useState<string | null>(null)

  const cleanImsi =
    /[\u2022*]/.test(imsi) || !imsi || imsi.includes('•')
      ? '999700000000001'
      : imsi.replace('imsi-', '')

  const codes = [
    {
      code: cleanImsi,
      label: 'SIM (IMSI)',
      description: '',
      category: 'Identidad SIM',
      isInfoOnly: true,
    },
    {
      code: '*1#',
      altCode: '*99#',
      description: 'Ver mi número móvil (MSISDN)',
      category: 'Identidad SIM',
    },
    {
      code: '*#06#',
      description: 'Ver IMEI, EID y código de barras',
      category: 'Identidad SIM',
    },
    {
      code: '*777#',
      description: 'Ver saldo y consumo de datos (CHF)',
      category: 'Tarificación',
    },
    {
      code: '*3001#12345#*',
      description: 'Field Test (banda n78, 5QI, IP y celda)',
      category: 'Diagnóstico 5G',
    },
  ]

  const handleCopy = (code: string, e?: React.MouseEvent) => {
    e?.stopPropagation()
    void navigator.clipboard.writeText(code)
    setCopiedCode(code)
    onTriggerIslandAlert?.(`✓ ${code} copiado al portapapeles`)
    setTimeout(() => setCopiedCode(null), 2000)
  }

  const handleDial = (code: string, e?: React.MouseEvent) => {
    e?.stopPropagation()
    onOpenPhone?.(code)
    onTriggerIslandAlert?.(`Marcando ${code}…`)
  }

  // --- SUBVIEW 1: NOTES LIST (Authentic iOS Notes Folder View) ---
  if (currentView === 'list') {
    return (
      <div className='flex h-full flex-col justify-between bg-black text-white relative animate-in fade-in duration-150 select-none'>
        {/* Navigation Bar */}
        <div className='flex items-center justify-between px-4 pt-2 shrink-0'>
          <button
            type='button'
            onClick={onClose}
            className='flex items-center text-[15px] font-normal text-[#e5a00d] hover:opacity-80 transition cursor-pointer -ml-1'
          >
            <ChevronLeft className='size-5 -mr-0.5' />
            Inicio
          </button>
          <button
            type='button'
            onClick={() => onTriggerIslandAlert?.('Opciones de carpeta Notas')}
            className='flex size-8 items-center justify-center rounded-full text-[#e5a00d] hover:bg-white/10 transition cursor-pointer'
          >
            <MoreHorizontal className='size-5' />
          </button>
        </div>

        {/* Header Large Title */}
        <div className='px-4 pt-1 pb-2'>
          <h1 className='text-3xl font-bold tracking-tight text-white'>Notas</h1>
        </div>

        {/* iOS Search Bar */}
        <div className='px-4 pb-3'>
          <div className='flex items-center gap-2 rounded-xl bg-[#1c1c1e] px-3 py-1.5 text-xs text-[#8e8e93] ring-1 ring-white/5'>
            <Search className='size-3.5 text-[#8e8e93]' />
            <input
              type='text'
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder='Buscar'
              className='bg-transparent text-xs text-white placeholder-[#8e8e93] outline-none flex-1 font-sans'
            />
          </div>
        </div>

        {/* Notes Cards Container */}
        <div
          ref={listDrag.scrollRef}
          onMouseDown={listDrag.handleMouseDown}
          onClickCapture={listDrag.handleClickCapture}
          className={cn(
            'flex-1 min-h-0 overflow-y-auto px-4 space-y-3 [&::-webkit-scrollbar]:hidden [scrollbar-width:none]',
            listDrag.isDragging ? 'cursor-grabbing select-none' : 'cursor-grab'
          )}
        >
          <div className='px-1 text-[11px] font-semibold text-[#8e8e93] uppercase tracking-wider flex items-center gap-1.5'>
            <Pin className='size-3 text-[#e5a00d]' />
            Fijadas
          </div>

          {/* Note Card 1: Códigos 5G */}
          <button
            type='button'
            onClick={() => setCurrentView('detail')}
            className='w-full text-left rounded-2xl bg-[#1c1c1e] p-3.5 ring-1 ring-white/5 hover:bg-[#2c2c2e]/60 transition active:scale-[0.99] cursor-pointer'
          >
            <div className='flex items-center justify-between'>
              <h2 className='text-[15px] font-semibold text-white'>Códigos 5G del Terminal</h2>
              <span className='size-2 rounded-full bg-[#e5a00d]' />
            </div>
            <div className='flex items-center gap-2 mt-1 text-[12px] text-[#8e8e93] font-sans'>
              <span className='text-white/80 font-medium'>14:15</span>
              <span className='truncate text-white/50'>
                *1# Mi número · *777# Saldo CHF · *#06# IMEI...
              </span>
            </div>
          </button>

          <div className='px-1 pt-2 text-[11px] font-semibold text-[#8e8e93] uppercase tracking-wider'>
            Notas del Testbed
          </div>

          {/* Note Card 2: Arquitectura */}
          <div className='rounded-2xl bg-[#1c1c1e] p-3.5 ring-1 ring-white/5 opacity-80'>
            <h2 className='text-[15px] font-semibold text-white'>Arquitectura Open5GS SA</h2>
            <div className='flex items-center gap-2 mt-1 text-[12px] text-[#8e8e93] font-sans'>
              <span className='text-white/80 font-medium'>Ayer</span>
              <span className='truncate text-white/50'>
                3GPP Rel-16 SBA · AMF, SMF, UPF, PCF, CHF, NWDAF
              </span>
            </div>
          </div>
        </div>

        {/* Bottom Toolbar (Authentic Apple iOS Notes Bottom Bar) */}
        <div className='flex items-center justify-between bg-black px-5 py-2.5 shrink-0 select-none'>
          <div className='w-8' />
          <span className='text-[11px] font-normal text-white/60 font-sans'>2 notas</span>
          <button
            type='button'
            onClick={() => setCurrentView('detail')}
            className='flex size-9 items-center justify-center rounded-full text-[#e5a00d] hover:bg-white/10 active:scale-95 transition cursor-pointer'
            title='Nueva nota'
          >
            <SquarePen className='size-5' />
          </button>
        </div>
      </div>
    )
  }

  // --- SUBVIEW 2: NOTE DETAIL VIEW (The Note itself) ---
  return (
    <div className='flex h-full flex-col justify-between bg-black text-white relative animate-in fade-in duration-150 select-text'>
      {/* Navigation Bar */}
      <div className='flex items-center justify-between px-4 pt-2 shrink-0 border-b border-white/5 select-none'>
        <button
          type='button'
          onClick={() => setCurrentView('list')}
          className='flex items-center text-[15px] font-normal text-[#e5a00d] hover:opacity-80 transition cursor-pointer -ml-1'
        >
          <ChevronLeft className='size-5 -mr-0.5' />
          Notas
        </button>

        <div className='flex items-center gap-3'>
          <button
            type='button'
            onClick={() => {
              const fullText = codes
                .map((c) =>
                  c.description
                    ? `${c.code}: ${c.description}`
                    : `${'label' in c && c.label ? `${c.label}: ` : ''}${c.code}`
                )
                .join('\n')
              void navigator.clipboard.writeText(fullText)
              onTriggerIslandAlert?.('✓ Lista de códigos copiada')
            }}
            className='text-[#e5a00d] hover:opacity-80 transition cursor-pointer'
            title='Copiar todos los códigos'
          >
            <Share2 className='size-4' />
          </button>
          <button
            type='button'
            onClick={() => setCurrentView('list')}
            className='text-[15px] font-semibold text-[#e5a00d] hover:opacity-80 transition cursor-pointer'
          >
            Listo
          </button>
        </div>
      </div>

      {/* Note Body (Scrollable with Drag-to-Scroll) */}
      <div
        ref={detailDrag.scrollRef}
        onMouseDown={detailDrag.handleMouseDown}
        onClickCapture={detailDrag.handleClickCapture}
        className={cn(
          'flex-1 min-h-0 overflow-y-auto px-5 py-3 space-y-4 [&::-webkit-scrollbar]:hidden [scrollbar-width:none] selection:bg-[#e5a00d]/40 selection:text-white',
          detailDrag.isDragging ? 'cursor-grabbing select-none' : 'cursor-grab select-text'
        )}
      >
        {/* Date Timestamp Header */}
        <div className='text-center'>
          <span className='text-[11px] text-[#8e8e93] font-medium'>
            27 de septiembre de 2026, 14:15
          </span>
        </div>

        {/* Note Title */}
        <div className='pb-1'>
          <h1 className='text-2xl font-bold tracking-tight text-white font-sans'>
            Códigos 5G del Terminal
          </h1>
        </div>

        {/* Short Codes List (Very concise descriptions as requested) */}
        <div className='space-y-2.5 pt-1'>
          {codes.map((item) => (
            <div
              key={item.code}
              className='group flex items-center justify-between rounded-xl bg-[#1c1c1e] p-2.5 border border-white/5 hover:border-white/10 transition select-text'
            >
              <div className='flex flex-col min-w-0 pr-2 select-text'>
                <div className='flex items-center gap-1.5'>
                  {'label' in item && item.label && (
                    <span className='text-[10px] text-teal-400 font-semibold uppercase tracking-wider font-sans'>
                      {item.label}:
                    </span>
                  )}
                  <span className='font-mono font-bold text-amber-400 text-[13px] tracking-tight selection:bg-[#e5a00d] selection:text-black'>
                    {item.code}
                  </span>
                  {'altCode' in item && item.altCode && (
                    <span className='text-[10px] text-white/40 font-mono'>
                      o {item.altCode}
                    </span>
                  )}
                </div>
                {item.description ? (
                  <p className='text-[11.5px] text-white/80 font-normal leading-tight mt-0.5 select-text'>
                    {item.description}
                  </p>
                ) : null}
              </div>

              {/* Action Buttons: Copy & Dial */}
              <div className='flex items-center gap-1 shrink-0 select-none'>
                {/* Copy Button */}
                <button
                  type='button'
                  onClick={(e) => handleCopy(item.code, e)}
                  className='flex size-7 items-center justify-center rounded-lg text-white/50 hover:bg-white/10 hover:text-white transition active:scale-90 cursor-pointer'
                  title='Copiar código'
                >
                  {copiedCode === item.code ? (
                    <Check className='size-3.5 text-emerald-400' />
                  ) : (
                    <Copy className='size-3.5' />
                  )}
                </button>

                {/* Dial / Execute Button (Only for dialable codes, not for static SIM IMSI) */}
                {!( 'isInfoOnly' in item && item.isInfoOnly ) && (
                  <button
                    type='button'
                    onClick={(e) => handleDial(item.code, e)}
                    className='flex size-7 items-center justify-center rounded-lg bg-[#30d158]/15 text-[#30d158] hover:bg-[#30d158]/25 transition active:scale-90 cursor-pointer'
                    title='Marcar en el teléfono'
                  >
                    <Phone className='size-3.5 fill-current' />
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Bottom Toolbar (Authentic Apple iOS Notes Bottom Bar) */}
      <div className='flex items-center justify-between bg-black px-5 py-2.5 shrink-0 select-none'>
        <button
          type='button'
          onClick={() => onTriggerIslandAlert?.('Lista de verificación activada')}
          className='text-[#e5a00d] hover:opacity-80 transition cursor-pointer'
          title='Checklist'
        >
          <CheckSquare className='size-5' />
        </button>
        <button
          type='button'
          onClick={() => onTriggerIslandAlert?.('Cámara de notas')}
          className='text-[#e5a00d] hover:opacity-80 transition cursor-pointer'
          title='Cámara'
        >
          <Camera className='size-5' />
        </button>
        <button
          type='button'
          onClick={() => onTriggerIslandAlert?.('Dibujo a mano alzada')}
          className='text-[#e5a00d] hover:opacity-80 transition cursor-pointer'
          title='Marcador'
        >
          <Pencil className='size-5' />
        </button>
        <button
          type='button'
          onClick={() => setCurrentView('list')}
          className='text-[#e5a00d] hover:opacity-80 transition cursor-pointer'
          title='Ver todas las notas'
        >
          <SquarePen className='size-5' />
        </button>
      </div>
    </div>
  )
}
