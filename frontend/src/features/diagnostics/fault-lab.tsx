import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { RotateCcw } from 'lucide-react'
import { toast } from 'sonner'
import { useScenarioStore } from '@/stores/scenario-store'
import { api, type Experiment } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { EmsPage } from '@/features/ems-page'

export function FaultLab() {
  const scenario = useScenarioStore((state) => state.scenario)
  const queryClient = useQueryClient()

  const experiments = useQuery({
    queryKey: ['experiments', scenario],
    queryFn: async () =>
      (await api.get<Experiment[]>(`/experiments/catalog/${scenario}`)).data,
    refetchInterval: 3000,
  })

  const [injectingId, setInjectingId] = useState<string | null>(null)

  const handleExperimentToggle = async (exp: Experiment) => {
    setInjectingId(exp.id)
    const isInjected = exp.state?.status === 'injected'
    const action = isInjected ? 'recover' : 'inject'
    try {
      const resp = await api.post(
        `/experiments/${exp.id}/${action}?scenario_id=${scenario}`
      )
      if (action === 'inject') {
        toast.error(`Falla inyectada: ${exp.title}`, {
          description:
            resp.data.state?.message || 'Condición de falla activada.',
        })
      } else {
        toast.success(`Servicio restablecido: ${exp.title}`, {
          description: resp.data.state?.message || 'Estado nominal recuperado.',
        })
      }
      await Promise.all([
        experiments.refetch(),
        queryClient.invalidateQueries({ queryKey: ['status'] }),
        queryClient.invalidateQueries({ queryKey: ['alarm-center'] }),
        queryClient.invalidateQueries({ queryKey: ['runtime'] }),
        queryClient.invalidateQueries({ queryKey: ['metrics'] }),
      ])
    } catch {
      toast.error(`Error al ejecutar acción sobre ${exp.id}`)
    } finally {
      setInjectingId(null)
    }
  }

  return (
    <div className='rounded-xl border border-border/60 bg-card overflow-hidden'>
      <div className='border-b border-border/40 px-4 py-3'>
        <h2 className='text-xs font-semibold tracking-tight text-foreground uppercase'>
          Laboratorio de Inyección de Fallas
        </h2>
      </div>
      <div className='overflow-x-auto'>
        <table className='w-full text-left text-sm'>
          <thead className='border-b border-border/40 bg-muted/20 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground'>
            <tr>
              <th className='px-4 py-2.5 font-medium'>Prueba</th>
              <th className='px-4 py-2.5 font-medium'>Interfaces</th>
              <th className='px-4 py-2.5 font-medium'>Estado</th>
              <th className='px-4 py-2.5 font-medium'>Tiempo</th>
              <th className='px-4 py-2.5 text-right font-medium'>Acción</th>
            </tr>
          </thead>
          <tbody className='divide-y divide-border/30'>
            {experiments.data?.map((exp) => {
              const isInjected = exp.state?.status === 'injected'
              const isWorking = injectingId === exp.id
              return (
                <tr
                  key={exp.id}
                  className={`transition-colors ${
                    isInjected
                      ? 'bg-red-500/5 hover:bg-red-500/10'
                      : 'hover:bg-muted/10'
                  }`}
                >
                  <td className='px-4 py-2.5'>
                    <div className='font-medium text-foreground text-xs'>
                      {exp.title}
                    </div>
                    <div className='text-[10px] text-muted-foreground font-mono truncate max-w-md'>
                      {exp.expected_detection}
                    </div>
                  </td>
                  <td className='px-4 py-2.5 whitespace-nowrap'>
                    <div className='flex items-center gap-1 flex-wrap'>
                      {exp.interfaces?.length ? (
                        exp.interfaces.map((iface) => (
                          <span
                            key={iface}
                            className='font-mono text-[9px] rounded bg-muted/60 px-1.5 py-0.5 text-muted-foreground border border-border/50'
                          >
                            {iface}
                          </span>
                        ))
                      ) : (
                        <span className='text-muted-foreground text-xs'>—</span>
                      )}
                    </div>
                  </td>
                  <td className='px-4 py-2.5 whitespace-nowrap'>
                    {isInjected ? (
                      <span className='inline-flex items-center gap-1.5 rounded-full border border-red-500/30 bg-red-500/10 px-2 py-0.5 text-[11px] font-medium text-red-400'>
                        <span className='size-1.5 rounded-full bg-red-500 animate-ping' />
                        Falla activa
                      </span>
                    ) : (
                      <span className='inline-flex items-center gap-1.5 rounded-full border border-border/40 bg-muted/30 px-2 py-0.5 text-[11px] text-muted-foreground'>
                        <span className='size-1.5 rounded-full bg-emerald-500/80' />
                        Nominal
                      </span>
                    )}
                  </td>
                  <td className='px-4 py-2.5 font-mono text-xs whitespace-nowrap text-muted-foreground'>
                    {isInjected
                      ? `${exp.state?.elapsed_seconds ?? 0} s`
                      : '—'}
                  </td>
                  <td className='px-4 py-2.5 text-right whitespace-nowrap'>
                    <Button
                      size='sm'
                      variant={isInjected ? 'destructive' : 'outline'}
                      className='h-7 text-xs px-3 font-medium min-w-24'
                      disabled={injectingId !== null}
                      onClick={() => handleExperimentToggle(exp)}
                    >
                      {isWorking ? (
                        isInjected ? 'Restaurando…' : 'Inyectando…'
                      ) : isInjected ? (
                        <>
                          <RotateCcw className='size-3 mr-1' />
                          Restaurar
                        </>
                      ) : (
                        'Inyectar'
                      )}
                    </Button>
                  </td>
                </tr>
              )
            })}
            {(experiments.isLoading ||
              experiments.isError ||
              !experiments.data?.length) && (
              <tr>
                <td
                  colSpan={5}
                  className='px-4 py-6 text-center text-xs text-muted-foreground'
                >
                  {experiments.isLoading
                    ? 'Cargando catálogo de fallas…'
                    : experiments.isError
                      ? 'No se pudo cargar el catálogo de fallas.'
                      : 'No hay pruebas registradas para este escenario.'}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export function FaultLabPage() {
  return (
    <EmsPage title='Laboratorio de Inyección de Fallas'>
      <FaultLab />
    </EmsPage>
  )
}
