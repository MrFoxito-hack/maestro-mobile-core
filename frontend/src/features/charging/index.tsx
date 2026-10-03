import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useScenarioStore } from '@/stores/scenario-store'
import { api } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { EmsPage } from '@/features/ems-page'

type Kind = 'accounts' | 'sessions' | 'cdrs' | 'ledger'
type Row = Record<string, unknown>
type Page = { items: Row[]; total: number; limit: number; offset: number }
const labels: Record<Kind, string> = {
  accounts: 'Cuentas',
  sessions: 'Sesiones',
  cdrs: 'CDR',
  ledger: 'Movimientos',
}
const columns: Record<Kind, [string, string][]> = {
  accounts: [
    ['supi', 'Suscriptor'],
    ['enabled', 'Habilitado'],
    ['quota_bytes', 'Cuota (B)'],
    ['consumed_bytes', 'Debitado (B)'],
    ['reserved_bytes', 'Reservado (B)'],
    ['available_bytes', 'Disponible (B)'],
  ],
  sessions: [
    ['supi', 'Suscriptor'],
    ['dnn', 'DNN'],
    ['status', 'Estado'],
    ['observed_bytes', 'Reportado (B)'],
    ['consumed_bytes', 'Debitado (B)'],
    ['reserved_bytes', 'Reservado (B)'],
    ['overrun_bytes', 'Exceso (B)'],
    ['updated_at', 'Actualización'],
  ],
  cdrs: [
    ['supi', 'Suscriptor'],
    ['charging_data_ref', 'Referencia de cobro'],
    ['closed_at', 'Cierre'],
  ],
  ledger: [
    ['id', 'N.º'],
    ['supi', 'Suscriptor'],
    ['operation', 'Operación'],
    ['created_at', 'Fecha'],
  ],
}
function display(value: unknown): string {
  if (value === undefined || value === null) return '—'
  if (typeof value === 'boolean' || value === 0 || value === 1)
    return String(value)
  if (typeof value === 'number') return value.toLocaleString('es-PE')
  return String(value)
}

export function ChargingPage() {
  const scenario = useScenarioStore((s) => s.scenario)
  const [kind, setKind] = useState<Kind>('accounts')
  const [search, setSearch] = useState('')
  const [supi, setSupi] = useState('')
  const [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState<Row | null>(null)
  const enabled = scenario === '5g-sa'
  const status = useQuery({
    queryKey: ['charging-status', scenario],
    enabled,
    retry: false,
    queryFn: async () =>
      (await api.get<{ connected: boolean }>('/charging/status')).data,
  })
  const query = useQuery({
    queryKey: ['charging', scenario, kind, supi, offset],
    enabled,
    retry: false,
    queryFn: async () =>
      (
        await api.get<Page>(`/charging/${kind}`, {
          params: { limit: 25, offset, ...(supi ? { supi } : {}) },
        })
      ).data,
  })
  const error = query.isError || status.isError
  return (
    <EmsPage title='Tarificación 5G'>
      <div className='flex flex-wrap items-center justify-between gap-3 border-b pb-3'>
        <div className='flex gap-1' role='tablist' aria-label='Tarificación'>
          {(Object.keys(labels) as Kind[]).map((tab) => (
            <Button
              key={tab}
              role='tab'
              aria-selected={kind === tab}
              size='sm'
              variant={kind === tab ? 'secondary' : 'ghost'}
              onClick={() => {
                setKind(tab)
                setOffset(0)
                setSelected(null)
              }}
            >
              {labels[tab]}
            </Button>
          ))}
        </div>
        <div className='flex items-center gap-3 text-xs text-muted-foreground'>
          <span>CHF · Perfil experimental · Solo lectura</span>
          <Button
            size='sm'
            variant='outline'
            disabled={!enabled || query.isFetching || status.isFetching}
            onClick={() => {
              setSelected(null)
              void status.refetch()
              void query.refetch()
            }}
          >
            Actualizar
          </Button>
        </div>
      </div>
      {!enabled ? (
        <p className='py-8 text-sm text-muted-foreground'>
          Esta integración Nchf corresponde al escenario 5G SA, no a 4G EPC.
        </p>
      ) : (
        <>
          <form
            className='my-4 flex flex-wrap items-center gap-2'
            onSubmit={(event) => {
              event.preventDefault()
              setSupi(search.trim())
              setOffset(0)
              setSelected(null)
            }}
          >
            <Input
              className='max-w-xs'
              aria-label='Filtrar por SUPI'
              placeholder='SUPI exacto: imsi-…'
              pattern='imsi-[0-9]{5,15}'
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
            <Button type='submit' size='sm' variant='outline'>
              Filtrar
            </Button>
            {supi && (
              <Button
                type='button'
                size='sm'
                variant='ghost'
                onClick={() => {
                  setSearch('')
                  setSupi('')
                  setOffset(0)
                  setSelected(null)
                }}
              >
                Limpiar
              </Button>
            )}
            <span className='ml-auto text-xs text-muted-foreground'>
              {error
                ? 'Sin conexión verificada'
                : status.data?.connected
                  ? 'Gestión conectada'
                  : 'Comprobando conexión'}
            </span>
          </form>
          {error ? (
            <div role='alert' className='rounded-md border p-6 text-sm'>
              No se pudo consultar el CHF. Comprueba la conexión de gestión y
              tus permisos. No se muestran datos simulados ni resultados
              anteriores.
            </div>
          ) : query.isPending ? (
            <p role='status' className='p-6 text-sm'>
              Consultando CHF…
            </p>
          ) : (
            <>
              <div className='overflow-x-auto rounded-md border'>
                <table className='w-full text-left text-sm'>
                  <thead className='bg-muted/50 text-xs text-muted-foreground'>
                    <tr>
                      {columns[kind].map(([key, label]) => (
                        <th key={key} className='px-4 py-3 font-medium'>
                          {label}
                        </th>
                      ))}
                      <th className='px-4 py-3 font-medium'>Detalle</th>
                    </tr>
                  </thead>
                  <tbody>
                    {query.data?.items.map((row, index) => (
                      <tr
                        key={String(
                          row.charging_data_ref ?? row.id ?? row.supi ?? index
                        )}
                        className='border-t'
                      >
                        {columns[kind].map(([key]) => (
                          <td
                            key={key}
                            className='px-4 py-3 font-mono text-xs whitespace-nowrap'
                          >
                            {key === 'enabled'
                              ? row[key]
                                ? 'Sí'
                                : 'No'
                              : display(row[key])}
                          </td>
                        ))}
                        <td className='px-4 py-2'>
                          <Button
                            size='sm'
                            variant='ghost'
                            onClick={() => setSelected(row)}
                          >
                            Ver
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {query.data?.items.length === 0 && (
                  <p className='p-8 text-center text-sm text-muted-foreground'>
                    Sin registros en el CHF conectado para esta consulta.
                  </p>
                )}
              </div>
              <div className='mt-3 flex items-center justify-between text-xs text-muted-foreground'>
                <span>
                  {query.data?.total ?? 0} registros · Valores exactos en bytes
                </span>
                <div className='flex items-center gap-2'>
                  <Button
                    size='sm'
                    variant='ghost'
                    disabled={offset === 0}
                    onClick={() => {
                      setOffset(offset - 25)
                      setSelected(null)
                    }}
                  >
                    Anterior
                  </Button>
                  <span>Página {offset / 25 + 1}</span>
                  <Button
                    size='sm'
                    variant='ghost'
                    disabled={offset + 25 >= (query.data?.total ?? 0)}
                    onClick={() => {
                      setOffset(offset + 25)
                      setSelected(null)
                    }}
                  >
                    Siguiente
                  </Button>
                </div>
              </div>
              {selected && (
                <section
                  className='mt-4 rounded-md border p-4'
                  aria-label='Detalle del registro'
                >
                  <div className='mb-3 flex items-center justify-between'>
                    <span className='text-sm font-medium'>Registro CHF</span>
                    <Button
                      size='sm'
                      variant='ghost'
                      onClick={() => setSelected(null)}
                    >
                      Cerrar
                    </Button>
                  </div>
                  <pre className='max-h-96 overflow-auto text-xs break-all whitespace-pre-wrap'>
                    {JSON.stringify(selected, null, 2)}
                  </pre>
                </section>
              )}
            </>
          )}
          <p className='mt-4 text-xs text-muted-foreground'>
            Los CDR son registros educativos del perfil implementado; la
            conexión de gestión no certifica conformidad completa 3GPP ni
            integridad extremo a extremo.
          </p>
        </>
      )}
    </EmsPage>
  )
}
