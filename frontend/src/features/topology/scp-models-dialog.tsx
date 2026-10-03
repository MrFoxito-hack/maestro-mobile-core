import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

export function ScpModelsDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className='w-[96vw] max-w-[96vw] sm:max-w-[96vw] h-[92vh] max-h-[92vh] flex flex-col p-4 sm:p-6 gap-2 bg-background'>
        <DialogHeader className='shrink-0 pb-1.5 border-b border-border/60 flex flex-row items-center justify-between'>
          <DialogTitle className='text-sm font-semibold tracking-wide flex items-center gap-2'>
            <span>3GPP TS 23.501 — Escenarios de Comunicación SBA</span>
          </DialogTitle>
        </DialogHeader>

        {/* Contenedor Cuadrantes 2x2 en formato Horizontal */}
        <div className='flex-1 min-h-0 relative border border-border bg-card/40 rounded-sm overflow-hidden flex flex-col'>
          {/* Definiciones globales de flechas por color de flujo */}
          <svg className='sr-only' width='0' height='0'>
            <defs>
              {/* Flecha Azul (Service Request) */}
              <marker id='arr-sky' markerWidth='8' markerHeight='8' refX='6' refY='4' orient='auto'>
                <path d='M1,1 L7,4 L1,7 Z' className='fill-sky-500 dark:fill-sky-400' />
              </marker>
              {/* Flecha Verde (Service Response hacia la izquierda) */}
              <marker id='arr-green-rev' markerWidth='8' markerHeight='8' refX='2' refY='4' orient='auto'>
                <path d='M7,1 L1,4 L7,7 Z' className='fill-emerald-500 dark:fill-emerald-400' />
              </marker>
              {/* Flecha Ámbar (Discovery hacia NRF) */}
              <marker id='arr-amber' markerWidth='8' markerHeight='8' refX='6' refY='4' orient='auto'>
                <path d='M1,1 L7,4 L1,7 Z' className='fill-amber-500 dark:fill-amber-400' />
              </marker>
              {/* Flecha Ámbar inversa (NF profile(s) desde NRF) */}
              <marker id='arr-amber-rev' markerWidth='8' markerHeight='8' refX='2' refY='4' orient='auto'>
                <path d='M7,1 L1,4 L7,7 Z' className='fill-amber-500 dark:fill-amber-400' />
              </marker>
              {/* Flechas Violeta/Índigo (Interacción SCP) */}
              <marker id='arr-indigo' markerWidth='8' markerHeight='8' refX='6' refY='4' orient='auto'>
                <path d='M1,1 L7,4 L1,7 Z' className='fill-indigo-500 dark:fill-indigo-400' />
              </marker>
              <marker id='arr-indigo-rev' markerWidth='8' markerHeight='8' refX='2' refY='4' orient='auto'>
                <path d='M7,1 L1,4 L7,7 Z' className='fill-indigo-500 dark:fill-indigo-400' />
              </marker>
            </defs>
          </svg>

          {/* Fila Superior: Modelos A y B */}
          <div className='flex-1 grid grid-cols-2 border-b border-border min-h-0'>
            {/* Panel A */}
            <div className='border-r border-border relative p-2 flex items-center justify-center bg-card/20'>
              <span className='absolute top-2 left-3 font-serif text-lg font-normal text-foreground'>
                A
              </span>
              <svg viewBox='0 0 560 230' className='w-full h-full select-none' preserveAspectRatio='xMidYMid meet'>
                {/* Consumer */}
                <rect x='30' y='12' width='36' height='206' className='stroke-zinc-400 dark:stroke-zinc-600 fill-zinc-100/80 dark:fill-zinc-900/80' strokeWidth='1.8' />
                <text x='48' y='36' textAnchor='middle' className='fill-foreground font-serif text-sm'>C</text>
                <text x='48' y='59' textAnchor='middle' className='fill-foreground font-serif text-sm'>o</text>
                <text x='48' y='82' textAnchor='middle' className='fill-foreground font-serif text-sm'>n</text>
                <text x='48' y='105' textAnchor='middle' className='fill-foreground font-serif text-sm'>s</text>
                <text x='48' y='128' textAnchor='middle' className='fill-foreground font-serif text-sm'>u</text>
                <text x='48' y='151' textAnchor='middle' className='fill-foreground font-serif text-sm'>m</text>
                <text x='48' y='174' textAnchor='middle' className='fill-foreground font-serif text-sm'>e</text>
                <text x='48' y='197' textAnchor='middle' className='fill-foreground font-serif text-sm'>r</text>

                {/* Producer */}
                <rect x='494' y='12' width='36' height='206' className='stroke-zinc-400 dark:stroke-zinc-600 fill-zinc-100/80 dark:fill-zinc-900/80' strokeWidth='1.8' />
                <text x='512' y='36' textAnchor='middle' className='fill-foreground font-serif text-sm'>P</text>
                <text x='512' y='59' textAnchor='middle' className='fill-foreground font-serif text-sm'>r</text>
                <text x='512' y='82' textAnchor='middle' className='fill-foreground font-serif text-sm'>o</text>
                <text x='512' y='105' textAnchor='middle' className='fill-foreground font-serif text-sm'>d</text>
                <text x='512' y='128' textAnchor='middle' className='fill-foreground font-serif text-sm'>u</text>
                <text x='512' y='151' textAnchor='middle' className='fill-foreground font-serif text-sm'>c</text>
                <text x='512' y='174' textAnchor='middle' className='fill-foreground font-serif text-sm'>e</text>
                <text x='512' y='197' textAnchor='middle' className='fill-foreground font-serif text-sm'>r</text>

                {/* Service Request (Azul) */}
                <line x1='66' y1='125' x2='486' y2='125' className='stroke-sky-500 dark:stroke-sky-400' strokeWidth='1.6' markerEnd='url(#arr-sky)' />
                <text x='280' y='117' textAnchor='middle' className='fill-sky-600 dark:fill-sky-300 font-serif text-xs font-medium'>Service Request</text>

                {/* Service Response (Verde) */}
                <line x1='74' y1='158' x2='494' y2='158' className='stroke-emerald-500 dark:stroke-emerald-400' strokeWidth='1.6' markerStart='url(#arr-green-rev)' />
                <text x='280' y='150' textAnchor='middle' className='fill-emerald-600 dark:fill-emerald-300 font-serif text-xs font-medium'>Service Response</text>

                {/* Subsequent Request (Azul) */}
                <line x1='66' y1='191' x2='486' y2='191' className='stroke-sky-500 dark:stroke-sky-400' strokeWidth='1.6' markerEnd='url(#arr-sky)' />
                <text x='280' y='183' textAnchor='middle' className='fill-sky-600 dark:fill-sky-300 font-serif text-xs font-medium'>Subsequent Request</text>
              </svg>
            </div>

            {/* Panel B */}
            <div className='relative p-2 flex items-center justify-center bg-card/20'>
              <span className='absolute top-2 left-3 font-serif text-lg font-normal text-foreground'>
                B
              </span>
              <svg viewBox='0 0 560 230' className='w-full h-full select-none' preserveAspectRatio='xMidYMid meet'>
                {/* Consumer */}
                <rect x='30' y='12' width='36' height='206' className='stroke-zinc-400 dark:stroke-zinc-600 fill-zinc-100/80 dark:fill-zinc-900/80' strokeWidth='1.8' />
                <text x='48' y='36' textAnchor='middle' className='fill-foreground font-serif text-sm'>C</text>
                <text x='48' y='59' textAnchor='middle' className='fill-foreground font-serif text-sm'>o</text>
                <text x='48' y='82' textAnchor='middle' className='fill-foreground font-serif text-sm'>n</text>
                <text x='48' y='105' textAnchor='middle' className='fill-foreground font-serif text-sm'>s</text>
                <text x='48' y='128' textAnchor='middle' className='fill-foreground font-serif text-sm'>u</text>
                <text x='48' y='151' textAnchor='middle' className='fill-foreground font-serif text-sm'>m</text>
                <text x='48' y='174' textAnchor='middle' className='fill-foreground font-serif text-sm'>e</text>
                <text x='48' y='197' textAnchor='middle' className='fill-foreground font-serif text-sm'>r</text>

                {/* NRF (Neutro idéntico a Consumer y Producer) */}
                <rect x='245' y='20' width='70' height='46' className='stroke-zinc-400 dark:stroke-zinc-600 fill-zinc-100/80 dark:fill-zinc-900/80' strokeWidth='1.8' />
                <text x='280' y='48' textAnchor='middle' className='fill-foreground font-serif text-sm font-medium'>NRF</text>

                {/* Producer */}
                <rect x='494' y='12' width='36' height='206' className='stroke-zinc-400 dark:stroke-zinc-600 fill-zinc-100/80 dark:fill-zinc-900/80' strokeWidth='1.8' />
                <text x='512' y='36' textAnchor='middle' className='fill-foreground font-serif text-sm'>P</text>
                <text x='512' y='59' textAnchor='middle' className='fill-foreground font-serif text-sm'>r</text>
                <text x='512' y='82' textAnchor='middle' className='fill-foreground font-serif text-sm'>o</text>
                <text x='512' y='105' textAnchor='middle' className='fill-foreground font-serif text-sm'>d</text>
                <text x='512' y='128' textAnchor='middle' className='fill-foreground font-serif text-sm'>u</text>
                <text x='512' y='151' textAnchor='middle' className='fill-foreground font-serif text-sm'>c</text>
                <text x='512' y='174' textAnchor='middle' className='fill-foreground font-serif text-sm'>e</text>
                <text x='512' y='197' textAnchor='middle' className='fill-foreground font-serif text-sm'>r</text>

                {/* Discovery (Ámbar) */}
                <line x1='66' y1='35' x2='237' y2='35' className='stroke-amber-500 dark:stroke-amber-400' strokeWidth='1.6' markerEnd='url(#arr-amber)' />
                <text x='152' y='28' textAnchor='middle' className='fill-amber-600 dark:fill-amber-300 font-serif text-xs font-medium'>Discovery</text>

                {/* NF profile(s) (Ámbar) */}
                <line x1='74' y1='56' x2='245' y2='56' className='stroke-amber-500 dark:stroke-amber-400' strokeWidth='1.6' markerStart='url(#arr-amber-rev)' />
                <text x='152' y='49' textAnchor='middle' className='fill-amber-600 dark:fill-amber-300 font-serif text-xs font-medium'>NF profile(s)</text>

                {/* Service Request (Azul) */}
                <line x1='66' y1='125' x2='486' y2='125' className='stroke-sky-500 dark:stroke-sky-400' strokeWidth='1.6' markerEnd='url(#arr-sky)' />
                <text x='280' y='117' textAnchor='middle' className='fill-sky-600 dark:fill-sky-300 font-serif text-xs font-medium'>Service Request</text>

                {/* Service Response (Verde) */}
                <line x1='74' y1='158' x2='494' y2='158' className='stroke-emerald-500 dark:stroke-emerald-400' strokeWidth='1.6' markerStart='url(#arr-green-rev)' />
                <text x='280' y='150' textAnchor='middle' className='fill-emerald-600 dark:fill-emerald-300 font-serif text-xs font-medium'>Service Response</text>

                {/* Subsequent Request (Azul) */}
                <line x1='66' y1='191' x2='486' y2='191' className='stroke-sky-500 dark:stroke-sky-400' strokeWidth='1.6' markerEnd='url(#arr-sky)' />
                <text x='280' y='183' textAnchor='middle' className='fill-sky-600 dark:fill-sky-300 font-serif text-xs font-medium'>Subsequent Request</text>
              </svg>
            </div>
          </div>

          {/* Fila Inferior: Modelos C y D */}
          <div className='flex-1 relative min-h-0'>
            <div className='h-full grid grid-cols-2'>
              {/* Panel C */}
              <div className='border-r border-border relative p-2 flex items-center justify-center bg-card/20'>
                <span className='absolute top-2 left-3 font-serif text-lg font-normal text-foreground'>
                  C
                </span>
                <svg viewBox='0 0 560 230' className='w-full h-full select-none' preserveAspectRatio='xMidYMid meet'>
                  {/* Consumer */}
                  <rect x='30' y='12' width='36' height='206' className='stroke-zinc-400 dark:stroke-zinc-600 fill-zinc-100/80 dark:fill-zinc-900/80' strokeWidth='1.8' />
                  <text x='48' y='36' textAnchor='middle' className='fill-foreground font-serif text-sm'>C</text>
                  <text x='48' y='59' textAnchor='middle' className='fill-foreground font-serif text-sm'>o</text>
                  <text x='48' y='82' textAnchor='middle' className='fill-foreground font-serif text-sm'>n</text>
                  <text x='48' y='105' textAnchor='middle' className='fill-foreground font-serif text-sm'>s</text>
                  <text x='48' y='128' textAnchor='middle' className='fill-foreground font-serif text-sm'>u</text>
                  <text x='48' y='151' textAnchor='middle' className='fill-foreground font-serif text-sm'>m</text>
                  <text x='48' y='174' textAnchor='middle' className='fill-foreground font-serif text-sm'>e</text>
                  <text x='48' y='197' textAnchor='middle' className='fill-foreground font-serif text-sm'>r</text>

                  {/* NRF (Neutro) */}
                  <rect x='245' y='12' width='70' height='42' className='stroke-zinc-400 dark:stroke-zinc-600 fill-zinc-100/80 dark:fill-zinc-900/80' strokeWidth='1.8' />
                  <text x='280' y='38' textAnchor='middle' className='fill-foreground font-serif text-sm font-medium'>NRF</text>

                  {/* NRF <-> SCP Enlace bidireccional (Violeta/Índigo) */}
                  <line x1='280' y1='62' x2='280' y2='88' className='stroke-indigo-500 dark:stroke-indigo-400' strokeWidth='1.6' markerStart='url(#arr-indigo-rev)' markerEnd='url(#arr-indigo)' />

                  {/* SCP (Neutro idéntico a NRF, Consumer y Producer) */}
                  <rect x='245' y='96' width='70' height='122' className='stroke-zinc-400 dark:stroke-zinc-600 fill-zinc-100/80 dark:fill-zinc-900/80' strokeWidth='1.8' />
                  <text x='280' y='162' textAnchor='middle' className='fill-foreground font-serif text-sm font-medium'>SCP</text>

                  {/* Producer */}
                  <rect x='494' y='12' width='36' height='206' className='stroke-zinc-400 dark:stroke-zinc-600 fill-zinc-100/80 dark:fill-zinc-900/80' strokeWidth='1.8' />
                  <text x='512' y='36' textAnchor='middle' className='fill-foreground font-serif text-sm'>P</text>
                  <text x='512' y='59' textAnchor='middle' className='fill-foreground font-serif text-sm'>r</text>
                  <text x='512' y='82' textAnchor='middle' className='fill-foreground font-serif text-sm'>o</text>
                  <text x='512' y='105' textAnchor='middle' className='fill-foreground font-serif text-sm'>d</text>
                  <text x='512' y='128' textAnchor='middle' className='fill-foreground font-serif text-sm'>u</text>
                  <text x='512' y='151' textAnchor='middle' className='fill-foreground font-serif text-sm'>c</text>
                  <text x='512' y='174' textAnchor='middle' className='fill-foreground font-serif text-sm'>e</text>
                  <text x='512' y='197' textAnchor='middle' className='fill-foreground font-serif text-sm'>r</text>

                  {/* Discovery (Ámbar) */}
                  <line x1='66' y1='25' x2='237' y2='25' className='stroke-amber-500 dark:stroke-amber-400' strokeWidth='1.6' markerEnd='url(#arr-amber)' />
                  <text x='152' y='18' textAnchor='middle' className='fill-amber-600 dark:fill-amber-300 font-serif text-xs font-medium'>Discovery</text>

                  {/* NF profile(s) (Ámbar) */}
                  <line x1='74' y1='47' x2='245' y2='47' className='stroke-amber-500 dark:stroke-amber-400' strokeWidth='1.6' markerStart='url(#arr-amber-rev)' />
                  <text x='152' y='40' textAnchor='middle' className='fill-amber-600 dark:fill-amber-300 font-serif text-xs font-medium'>NF profile(s)</text>

                  {/* Service Request: Consumer -> SCP (Azul) */}
                  <line x1='66' y1='115' x2='237' y2='115' className='stroke-sky-500 dark:stroke-sky-400' strokeWidth='1.6' markerEnd='url(#arr-sky)' />
                  <text x='152' y='107' textAnchor='middle' className='fill-sky-600 dark:fill-sky-300 font-serif text-xs font-medium'>Service Request</text>

                  {/* Service Request: SCP -> Producer (Azul) */}
                  <line x1='315' y1='115' x2='486' y2='115' className='stroke-sky-500 dark:stroke-sky-400' strokeWidth='1.6' markerEnd='url(#arr-sky)' />
                  <text x='405' y='107' textAnchor='middle' className='fill-sky-600 dark:fill-sky-300 font-serif text-xs font-medium'>Service Request</text>

                  {/* Response: Producer -> SCP (Verde) */}
                  <line x1='323' y1='152' x2='494' y2='152' className='stroke-emerald-500 dark:stroke-emerald-400' strokeWidth='1.6' markerStart='url(#arr-green-rev)' />
                  <text x='405' y='144' textAnchor='middle' className='fill-emerald-600 dark:fill-emerald-300 font-serif text-xs font-medium'>Response</text>

                  {/* Response: SCP -> Consumer (Verde) */}
                  <line x1='74' y1='152' x2='245' y2='152' className='stroke-emerald-500 dark:stroke-emerald-400' strokeWidth='1.6' markerStart='url(#arr-green-rev)' />
                  <text x='152' y='144' textAnchor='middle' className='fill-emerald-600 dark:fill-emerald-300 font-serif text-xs font-medium'>Response</text>

                  {/* Subsequent Request: Consumer -> SCP (Azul) */}
                  <line x1='66' y1='190' x2='237' y2='190' className='stroke-sky-500 dark:stroke-sky-400' strokeWidth='1.6' markerEnd='url(#arr-sky)' />
                  <text x='152' y='182' textAnchor='middle' className='fill-sky-600 dark:fill-sky-300 font-serif text-xs font-medium'>Subsequent Request</text>

                  {/* Subsequent Request: SCP -> Producer (Azul) */}
                  <line x1='315' y1='190' x2='486' y2='190' className='stroke-sky-500 dark:stroke-sky-400' strokeWidth='1.6' markerEnd='url(#arr-sky)' />
                  <text x='405' y='182' textAnchor='middle' className='fill-sky-600 dark:fill-sky-300 font-serif text-xs font-medium'>Subsequent Request</text>
                </svg>
              </div>

              {/* Panel D */}
              <div className='relative p-2 flex items-center justify-center bg-card/20'>
                <span className='absolute top-2 left-3 font-serif text-lg font-normal text-foreground'>
                  D
                </span>
                <svg viewBox='0 0 560 230' className='w-full h-full select-none' preserveAspectRatio='xMidYMid meet'>
                  {/* Consumer */}
                  <rect x='30' y='12' width='36' height='206' className='stroke-zinc-400 dark:stroke-zinc-600 fill-zinc-100/80 dark:fill-zinc-900/80' strokeWidth='1.8' />
                  <text x='48' y='36' textAnchor='middle' className='fill-foreground font-serif text-sm'>C</text>
                  <text x='48' y='59' textAnchor='middle' className='fill-foreground font-serif text-sm'>o</text>
                  <text x='48' y='82' textAnchor='middle' className='fill-foreground font-serif text-sm'>n</text>
                  <text x='48' y='105' textAnchor='middle' className='fill-foreground font-serif text-sm'>s</text>
                  <text x='48' y='128' textAnchor='middle' className='fill-foreground font-serif text-sm'>u</text>
                  <text x='48' y='151' textAnchor='middle' className='fill-foreground font-serif text-sm'>m</text>
                  <text x='48' y='174' textAnchor='middle' className='fill-foreground font-serif text-sm'>e</text>
                  <text x='48' y='197' textAnchor='middle' className='fill-foreground font-serif text-sm'>r</text>

                  {/* NRF (Neutro) */}
                  <rect x='245' y='12' width='70' height='42' className='stroke-zinc-400 dark:stroke-zinc-600 fill-zinc-100/80 dark:fill-zinc-900/80' strokeWidth='1.8' />
                  <text x='280' y='38' textAnchor='middle' className='fill-foreground font-serif text-sm font-medium'>NRF</text>

                  {/* NRF <-> SCP Enlace bidireccional (Violeta/Índigo) */}
                  <line x1='280' y1='62' x2='280' y2='88' className='stroke-indigo-500 dark:stroke-indigo-400' strokeWidth='1.6' markerStart='url(#arr-indigo-rev)' markerEnd='url(#arr-indigo)' />

                  {/* SCP (Neutro) */}
                  <rect x='245' y='96' width='70' height='122' className='stroke-zinc-400 dark:stroke-zinc-600 fill-zinc-100/80 dark:fill-zinc-900/80' strokeWidth='1.8' />
                  <text x='280' y='162' textAnchor='middle' className='fill-foreground font-serif text-sm font-medium'>SCP</text>

                  {/* Producer */}
                  <rect x='494' y='12' width='36' height='206' className='stroke-zinc-400 dark:stroke-zinc-600 fill-zinc-100/80 dark:fill-zinc-900/80' strokeWidth='1.8' />
                  <text x='512' y='36' textAnchor='middle' className='fill-foreground font-serif text-sm'>P</text>
                  <text x='512' y='59' textAnchor='middle' className='fill-foreground font-serif text-sm'>r</text>
                  <text x='512' y='82' textAnchor='middle' className='fill-foreground font-serif text-sm'>o</text>
                  <text x='512' y='105' textAnchor='middle' className='fill-foreground font-serif text-sm'>d</text>
                  <text x='512' y='128' textAnchor='middle' className='fill-foreground font-serif text-sm'>u</text>
                  <text x='512' y='151' textAnchor='middle' className='fill-foreground font-serif text-sm'>c</text>
                  <text x='512' y='174' textAnchor='middle' className='fill-foreground font-serif text-sm'>e</text>
                  <text x='512' y='197' textAnchor='middle' className='fill-foreground font-serif text-sm'>r</text>

                  {/* Service Request + parameters: Consumer -> SCP (Azul) */}
                  <line x1='66' y1='115' x2='237' y2='115' className='stroke-sky-500 dark:stroke-sky-400' strokeWidth='1.6' markerEnd='url(#arr-sky)' />
                  <text x='152' y='99' textAnchor='middle' className='fill-sky-600 dark:fill-sky-300 font-serif text-xs font-medium'>Service Request</text>
                  <text x='152' y='110' textAnchor='middle' className='fill-sky-600 dark:fill-sky-300 font-serif text-xs font-medium'>+ parameters</text>

                  {/* Service Request: SCP -> Producer (Azul) */}
                  <line x1='315' y1='115' x2='486' y2='115' className='stroke-sky-500 dark:stroke-sky-400' strokeWidth='1.6' markerEnd='url(#arr-sky)' />
                  <text x='405' y='107' textAnchor='middle' className='fill-sky-600 dark:fill-sky-300 font-serif text-xs font-medium'>Service Request</text>

                  {/* Response: Producer -> SCP (Verde) */}
                  <line x1='323' y1='152' x2='494' y2='152' className='stroke-emerald-500 dark:stroke-emerald-400' strokeWidth='1.6' markerStart='url(#arr-green-rev)' />
                  <text x='405' y='144' textAnchor='middle' className='fill-emerald-600 dark:fill-emerald-300 font-serif text-xs font-medium'>Response</text>

                  {/* Response: SCP -> Consumer (Verde) */}
                  <line x1='74' y1='152' x2='245' y2='152' className='stroke-emerald-500 dark:stroke-emerald-400' strokeWidth='1.6' markerStart='url(#arr-green-rev)' />
                  <text x='152' y='144' textAnchor='middle' className='fill-emerald-600 dark:fill-emerald-300 font-serif text-xs font-medium'>Response</text>

                  {/* Subsequent Request: Consumer -> SCP (Azul) */}
                  <line x1='66' y1='190' x2='237' y2='190' className='stroke-sky-500 dark:stroke-sky-400' strokeWidth='1.6' markerEnd='url(#arr-sky)' />
                  <text x='152' y='182' textAnchor='middle' className='fill-sky-600 dark:fill-sky-300 font-serif text-xs font-medium'>Subsequent Request</text>

                  {/* Subsequent Request: SCP -> Producer (Azul) */}
                  <line x1='315' y1='190' x2='486' y2='190' className='stroke-sky-500 dark:stroke-sky-400' strokeWidth='1.6' markerEnd='url(#arr-sky)' />
                  <text x='405' y='182' textAnchor='middle' className='fill-sky-600 dark:fill-sky-300 font-serif text-xs font-medium'>Subsequent Request</text>
                </svg>
              </div>
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
