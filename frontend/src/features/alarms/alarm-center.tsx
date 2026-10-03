import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import {
  Bell,
  BellOff,
  Check,
  ChevronDown,
  ChevronRight,
  Download,
  Filter,
  MessageSquare,
  Radio,
  RefreshCw,
  Settings2,
  Terminal,
} from 'lucide-react'
import { toast } from 'sonner'
import { useAuthStore } from '@/stores/auth-store'
import { useScenarioStore } from '@/stores/scenario-store'
import { api, apiErrorMessage } from '@/lib/api'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { DateTimePicker } from '@/components/ui/date-time-picker'
import { EmsPage } from '@/features/ems-page'

type Incident = {
  id: string
  condition_key: string
  message: string
  component: string
  network_function: string
  node_id: string
  severity: string
  original_severity: string
  state: 'active' | 'cleared'
  first_seen: number
  last_seen: number
  cleared_at: number | null
  duration_seconds: number
  acknowledged_by: string | null
  acknowledged_at: number | null
  interfaces: string[]
  procedures: string[]
  evidence: string
  recommendation: string
  probable_cause: string
  masked: boolean
  silenced_until: number
  stale: boolean
}
type Rule = Incident & {
  settings: {
    severity: string | null
    masked: boolean
    silenced_until: number
    raise_seconds: number
    clear_seconds: number
  }
  modified_by: string | null
  modified_at: number | null
}
type Result = {
  items: Incident[]
  total: number
  counts: Record<string, number>
  observer: {
    stale: boolean
    succeeded_at: number | null
    error: string | null
  }
}
const severities: Record<string, string> = {
  critical: 'Crítica',
  major: 'Mayor',
  minor: 'Menor',
  warning: 'Advertencia',
}
const colors: Record<string, string> = {
  critical: 'border-red-500/40 bg-red-500/10 text-red-600 dark:text-red-400',
  major:
    'border-orange-500/40 bg-orange-500/10 text-orange-600 dark:text-orange-400',
  minor:
    'border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400',
  warning: 'border-sky-500/40 bg-sky-500/10 text-sky-600 dark:text-sky-400',
}
const selectClass = 'h-9 rounded-md border bg-background px-2 text-xs'
const date = (value: number | null | undefined) =>
  value ? new Date(value * 1000).toLocaleString() : '—'
const duration = (value: number) =>
  value < 60
    ? `${Math.floor(value)} s`
    : value < 3600
      ? `${Math.floor(value / 60)} min`
      : `${(value / 3600).toFixed(1)} h`

export function AlarmsPage() {
  const client = useQueryClient()
  const canOperate = useAuthStore((state) =>
    ['admin', 'teacher'].includes(state.auth.user?.role ?? '')
  )
  const scenario = useScenarioStore((state) => state.scenario)
  const [view, setView] = useState<'active' | 'history' | 'config'>('active')
  const [auto, setAuto] = useState(true)
  const [notify, setNotify] = useState(false)
  const seen = useRef<Set<string> | null>(null)
  const [advanced, setAdvanced] = useState(false)
  const [search, setSearch] = useState('')
  const [component, setComponent] = useState(() => {
    if (typeof window !== 'undefined') {
      const p = new URLSearchParams(window.location.search)
      return p.get('component') || ''
    }
    return ''
  })
  const [severity, setSeverity] = useState('')
  const [ack, setAck] = useState('')
  const [visibility, setVisibility] = useState('visible')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<string[]>([])
  const [expanded, setExpanded] = useState<string | null>(null)
  const [comment, setComment] = useState<string[] | null>(null)
  const [note, setNote] = useState('')
  const [editing, setEditing] = useState<Rule | null>(null)
  const [exporting, setExporting] = useState(false)
  const params = useMemo(
    () => ({
      view: view === 'history' ? 'history' : 'active',
      search,
      component,
      severity,
      ack,
      visibility,
      ...(start ? { start: new Date(start).getTime() / 1000 } : {}),
      ...(end ? { end: new Date(end).getTime() / 1000 } : {}),
    }),
    [view, search, component, severity, ack, visibility, start, end]
  )
  const query = useQuery({
    queryKey: ['alarm-center', scenario, params, page],
    queryFn: async () =>
      (
        await api.get<Result>(`/alarm-center/${scenario}`, {
          params: { ...params, page, size: 25 },
        })
      ).data,
    refetchInterval: auto ? 5000 : false,
  })
  const ruleQuery = useQuery({
    queryKey: ['alarm-rules', scenario],
    queryFn: async () =>
      (await api.get<Rule[]>(`/alarm-center/${scenario}/rules`)).data,
    refetchInterval: auto ? 15000 : false,
  })
  const items = query.data?.items ?? []
  const nodes = [
    ...new Map(
      (ruleQuery.data ?? []).map((rule) => [
        rule.component,
        rule.network_function,
      ])
    ).entries(),
  ]
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ['alarm-center'] })
    void client.invalidateQueries({ queryKey: ['alarm-rules'] })
    void client.invalidateQueries({ queryKey: ['alarm-events'] })
  }
  const resetSelection = () => {
    setPage(1)
    setSelected([])
    setExpanded(null)
    seen.current = null
  }
  const mutation = useMutation({
    mutationFn: async (payload: {
      ids: string[]
      action: string
      note?: string
    }) => api.post(`/alarm-center/${scenario}/actions`, payload),
    onSuccess: () => {
      refresh()
      setSelected([])
      setComment(null)
      setNote('')
      toast.success('Actualizado')
    },
    onError: (error) =>
      toast.error(apiErrorMessage(error, 'No se pudo actualizar el incidente')),
  })
  const changeView = (next: typeof view) => {
    setView(next)
    setVisibility(next === 'active' ? 'visible' : 'all')
    resetSelection()
  }
  useEffect(() => {
    if (!query.data || view !== 'active') return
    const current = new Set(query.data.items.map((item) => item.id))
    if (seen.current && notify && !query.data.observer.stale) {
      const fresh = query.data.items.filter(
        (item) =>
          !seen.current!.has(item.id) &&
          !item.masked &&
          !item.stale &&
          item.silenced_until <= Date.now() / 1000
      )
      if (fresh.length)
        toast.warning(
          `${fresh.length} nuevo(s) incidente(s) en esta consulta`,
          { description: fresh[0].message }
        )
    }
    seen.current = current
  }, [query.data, notify, view])
  const exportCsv = async () => {
    setExporting(true)
    try {
      const response = await api.get(`/alarm-center/${scenario}/export`, {
        params,
        responseType: 'blob',
      })
      const url = URL.createObjectURL(response.data)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `alarmas-${scenario}-${view}.csv`
      anchor.click()
      URL.revokeObjectURL(url)
    } catch {
      toast.error(
        'No se pudo exportar. Acote los filtros a un máximo de 10000 incidentes.'
      )
    } finally {
      setExporting(false)
    }
  }
  const filteredRules = (ruleQuery.data ?? []).filter(
    (rule) =>
      (!component || rule.component === component) &&
      `${rule.message} ${rule.condition_key}`
        .toLowerCase()
        .includes(search.toLowerCase())
  )
  return (
    <EmsPage title='Alarmas' description=''>
      <div className='overflow-hidden rounded-xl border bg-background'>
        <div className='flex flex-wrap items-center justify-between gap-3 border-b px-4 py-2'>
          <div className='flex flex-wrap items-center gap-2'>
            <Button
              variant='ghost'
              size='icon'
              aria-label='Actualizar alarmas'
              onClick={refresh}
            >
              <RefreshCw
                className={query.isFetching ? 'size-4 animate-spin' : 'size-4'}
              />
            </Button>
            <nav aria-label='Vistas de alarmas' className='flex gap-1'>
              {(
                [
                  ['active', 'Activas'],
                  ['history', 'Historial'],
                  ['config', 'Configuración'],
                ] as const
              ).map(([key, label]) => (
                <Button
                  key={key}
                  size='sm'
                  variant={view === key ? 'secondary' : 'ghost'}
                  onClick={() => changeView(key)}
                >
                  {label}
                </Button>
              ))}
            </nav>
          </div>
          <div
            className='flex flex-wrap gap-2'
            title='Incidentes activos del escenario, incluidos los enmascarados'
          >
            {Object.entries(severities).map(([key, label]) => (
              <button
                key={key}
                aria-pressed={severity === key}
                onClick={() => {
                  setSeverity(severity === key ? '' : key)
                  if (view === 'config') setView('active')
                  resetSelection()
                }}
                className={`rounded border px-2 py-1 text-xs ${colors[key]} ${severity === key ? 'ring-1 ring-current' : ''}`}
              >
                {label}{' '}
                <strong className='ml-2 font-mono'>
                  {query.data?.counts[key] ?? '—'}
                </strong>
              </button>
            ))}
          </div>
        </div>
        <div className='flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3'>
          <div className='flex flex-wrap items-center gap-2'>
            <Input
              aria-label='Buscar alarmas'
              className='h-9 w-60'
              placeholder='Buscar nombre o código…'
              value={search}
              onChange={(event) => {
                setSearch(event.target.value)
                resetSelection()
              }}
            />
            <select
              aria-label='Nodo'
              className={selectClass}
              value={component}
              onChange={(event) => {
                setComponent(event.target.value)
                resetSelection()
              }}
            >
              <option value=''>Todos los nodos</option>
              {nodes.map(([id, label]) => (
                <option key={id} value={id}>
                  {label}
                </option>
              ))}
            </select>
            {view !== 'config' && (
              <Button
                size='sm'
                variant={advanced ? 'secondary' : 'outline'}
                onClick={() => setAdvanced(!advanced)}
              >
                <Filter className='size-3.5' />
                Filtros
              </Button>
            )}
          </div>
          <div className='flex items-center gap-2'>
            <label className='flex items-center gap-2 text-xs'>
              <input
                type='checkbox'
                checked={auto}
                onChange={(event) => setAuto(event.target.checked)}
              />
              Autoactualizar
            </label>
            <Button
              size='icon'
              variant='ghost'
              aria-label={
                notify
                  ? 'Desactivar avisos en pantalla'
                  : 'Activar avisos en pantalla'
              }
              title='Avisos de nuevos incidentes en la consulta visible; no controla la recolección'
              onClick={() => {
                setNotify(!notify)
                seen.current = null
              }}
            >
              {notify ? (
                <Bell className='size-4' />
              ) : (
                <BellOff className='size-4' />
              )}
            </Button>
            {view !== 'config' && (
              <Button
                size='sm'
                variant='outline'
                disabled={exporting || query.isError}
                onClick={() => void exportCsv()}
              >
                <Download className='size-3.5' />
                {exporting ? 'Exportando…' : 'Exportar'}
              </Button>
            )}
          </div>
        </div>
        {advanced && view !== 'config' && (
          <div className='grid gap-3 border-b bg-muted/20 p-4 sm:grid-cols-2 lg:grid-cols-4'>
            <Field title='Reconocimiento'>
              <select
                className={selectClass}
                value={ack}
                onChange={(event) => {
                  setAck(event.target.value)
                  resetSelection()
                }}
              >
                <option value=''>Todos</option>
                <option value='yes'>Reconocidas</option>
                <option value='no'>Sin reconocer</option>
              </select>
            </Field>
            <Field title='Visibilidad'>
              <select
                className={selectClass}
                value={visibility}
                onChange={(event) => {
                  setVisibility(event.target.value)
                  resetSelection()
                }}
              >
                <option value='all'>Todas</option>
                <option value='visible'>No enmascaradas</option>
                <option value='masked'>Enmascaradas</option>
                <option value='silenced'>Silenciadas</option>
              </select>
            </Field>
            <Field title='Primera detección desde'>
              <DateTimePicker
                value={start}
                placeholder='Fecha y hora inicial...'
                onChange={(val) => {
                  setStart(val)
                  resetSelection()
                }}
              />
            </Field>
            <Field title='Primera detección hasta'>
              <DateTimePicker
                value={end}
                placeholder='Fecha y hora final...'
                onChange={(val) => {
                  setEnd(val)
                  resetSelection()
                }}
              />
            </Field>
            <Button
              size='sm'
              variant='ghost'
              onClick={() => {
                setSearch('')
                setComponent('')
                setSeverity('')
                setAck('')
                setVisibility(view === 'active' ? 'visible' : 'all')
                setStart('')
                setEnd('')
                resetSelection()
              }}
            >
              Restablecer filtros
            </Button>
          </div>
        )}
        <div className='flex flex-wrap items-center justify-between gap-2 border-b px-4 py-2 text-[11px] text-muted-foreground'>
          <span role='status'>
            {query.isError
              ? 'No se pudo consultar el historial.'
              : query.isLoading
                ? 'Cargando observaciones…'
                : query.data?.observer.stale
                  ? 'Observación no disponible o desactualizada. No implica recuperación.'
                  : 'Observador conectado'}{' '}
            · Última observación: {date(query.data?.observer.succeeded_at)}
          </span>
          <span>
            {view === 'history'
              ? 'Incluye incidentes activos y recuperados'
              : view === 'config'
                ? 'Reglas por condición y nodo · aplicadas en el EMS'
                : visibility === 'visible'
                  ? 'Enmascaradas excluidas de la tabla'
                  : 'Visibilidad según filtros'}
          </span>
        </div>
        {!!selected.length && view !== 'config' && (
          <div className='flex flex-wrap items-center gap-2 border-b bg-muted/20 px-4 py-2 text-xs'>
            <span>{selected.length} seleccionadas</span>
            <Button
              size='sm'
              variant='outline'
              disabled={!canOperate || mutation.isPending}
              onClick={() =>
                mutation.mutate({ ids: selected, action: 'acknowledge' })
              }
            >
              Reconocer
            </Button>
            <Button
              size='sm'
              variant='outline'
              disabled={!canOperate || mutation.isPending}
              onClick={() =>
                mutation.mutate({ ids: selected, action: 'unacknowledge' })
              }
            >
              Retirar reconocimiento
            </Button>
            <Button
              size='sm'
              variant='outline'
              disabled={!canOperate}
              onClick={() => {
                setComment(selected)
                setNote('')
              }}
            >
              Comentar
            </Button>
          </div>
        )}
        {view === 'config' ? (
          <>
            <div className='max-h-[65vh] overflow-auto'>
              <table className='w-full text-left text-xs'>
                <thead className='sticky top-0 bg-muted'>
                  <tr>
                    {[
                      'Condición / código',
                      'Nodo',
                      'Severidad',
                      'Enmascarada',
                      'Silenciada hasta',
                      'Apertura / recuperación',
                      'Modificada',
                      '',
                    ].map((title, i) => (
                      <th className='px-4 py-3 font-medium' key={i}>
                        {title}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className='divide-y'>
                  {filteredRules.map((rule) => (
                    <tr key={rule.condition_key} className='hover:bg-muted/20'>
                      <td className='px-4 py-3'>
                        {rule.message}
                        <code className='mt-1 block text-[10px] text-muted-foreground'>
                          {rule.condition_key}
                        </code>
                      </td>
                      <td className='px-4'>{rule.network_function}</td>
                      <td className='px-4'>
                        {severities[rule.settings.severity || rule.severity]}
                      </td>
                      <td className='px-4'>
                        {rule.settings.masked ? 'Sí' : 'No'}
                      </td>
                      <td className='px-4 whitespace-nowrap'>
                        {rule.settings.silenced_until > Date.now() / 1000
                          ? date(rule.settings.silenced_until)
                          : '—'}
                      </td>
                      <td className='px-4 whitespace-nowrap'>
                        {rule.settings.raise_seconds} s /{' '}
                        {rule.settings.clear_seconds} s
                      </td>
                      <td className='px-4'>
                        {rule.modified_by ?? 'Predeterminada'}
                        <span className='block whitespace-nowrap text-muted-foreground'>
                          {date(rule.modified_at)}
                        </span>
                      </td>
                      <td className='px-4'>
                        <Button
                          size='sm'
                          variant='ghost'
                          disabled={!canOperate}
                          onClick={() => setEditing(rule)}
                        >
                          <Settings2 className='size-3.5' />
                          Modificar
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {ruleQuery.isError ? (
              <Empty text='No se pudieron cargar las reglas.' />
            ) : (
              !filteredRules.length && (
                <Empty
                  text={
                    ruleQuery.isLoading
                      ? 'Cargando…'
                      : 'Sin condiciones detectadas para estos filtros. Las reglas aparecen al observar una condición por primera vez.'
                  }
                />
              )
            )}
          </>
        ) : (
          <>
            <div className='max-h-[65vh] overflow-auto'>
              <table className='w-full text-left text-xs'>
                <thead className='sticky top-0 z-10 bg-muted'>
                  <tr>
                    <th className='px-3 py-3'>
                      <input
                        type='checkbox'
                        aria-label='Seleccionar página'
                        disabled={!canOperate || !items.length}
                        checked={
                          !!items.length &&
                          items.every((item) => selected.includes(item.id))
                        }
                        onChange={(event) =>
                          setSelected(
                            event.target.checked
                              ? items.map((item) => item.id)
                              : []
                          )
                        }
                      />
                    </th>
                    {[
                      'Severidad',
                      'Alarma / código',
                      'Nodo',
                      'Estado',
                      'Primera detección',
                      ...(view === 'history'
                        ? ['Recuperada', 'Duración']
                        : ['Última detección']),
                      'Reconocimiento',
                      '',
                    ].map((title, i) => (
                      <th
                        key={i}
                        className='px-3 py-3 font-medium whitespace-nowrap'
                      >
                        {title}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className='divide-y'>
                  {items.map((item) => (
                    <Fragment key={item.id}>
                      <tr className='hover:bg-muted/20'>
                        <td className='px-3 py-3'>
                          <input
                            type='checkbox'
                            aria-label={`Seleccionar ${item.condition_key}`}
                            disabled={!canOperate}
                            checked={selected.includes(item.id)}
                            onChange={(event) =>
                              setSelected(
                                event.target.checked
                                  ? [...selected, item.id]
                                  : selected.filter((id) => id !== item.id)
                              )
                            }
                          />
                        </td>
                        <td className='px-3'>
                          <Badge
                            variant='outline'
                            className={colors[item.severity]}
                          >
                            {severities[item.severity] ?? item.severity}
                          </Badge>
                        </td>
                        <td className='min-w-64 px-3 py-3'>
                          <button
                            className='flex items-start gap-2 text-left font-medium'
                            aria-expanded={expanded === item.id}
                            onClick={() =>
                              setExpanded(expanded === item.id ? null : item.id)
                            }
                          >
                            {expanded === item.id ? (
                              <ChevronDown className='size-4 shrink-0' />
                            ) : (
                              <ChevronRight className='size-4 shrink-0' />
                            )}
                            <span>
                              {item.message}
                              <code className='mt-1 block text-[10px] font-normal text-muted-foreground'>
                                {item.condition_key}
                              </code>
                            </span>
                          </button>
                        </td>
                        <td className='px-3'>
                          {item.network_function}
                          <span className='block text-[10px] text-muted-foreground'>
                            {item.node_id}
                          </span>
                        </td>
                        <td className='px-3 whitespace-nowrap'>
                          {item.state === 'cleared' ? 'Recuperada' : 'Activa'}
                          {item.stale && (
                            <span className='block text-amber-600'>
                              Sin datos recientes
                            </span>
                          )}
                          {item.masked && (
                            <span className='block text-muted-foreground'>
                              Enmascarada
                            </span>
                          )}
                          {item.silenced_until > Date.now() / 1000 && (
                            <span className='block text-muted-foreground'>
                              Silenciada
                            </span>
                          )}
                        </td>
                        <td className='px-3 whitespace-nowrap'>
                          {date(item.first_seen)}
                        </td>
                        {view === 'history' ? (
                          <>
                            <td className='px-3 whitespace-nowrap'>
                              {date(item.cleared_at)}
                            </td>
                            <td className='px-3 whitespace-nowrap'>
                              {duration(item.duration_seconds)}
                            </td>
                          </>
                        ) : (
                          <td className='px-3 whitespace-nowrap'>
                            {date(item.last_seen)}
                          </td>
                        )}
                        <td className='px-3'>
                          {item.acknowledged_by ?? 'Pendiente'}
                        </td>
                        <td className='px-3'>
                          <div className='flex'>
                            <Button
                              size='icon'
                              variant='ghost'
                              aria-label={
                                item.acknowledged_by
                                  ? 'Retirar reconocimiento'
                                  : 'Reconocer alarma'
                              }
                              disabled={!canOperate || mutation.isPending}
                              onClick={() =>
                                mutation.mutate({
                                  ids: [item.id],
                                  action: item.acknowledged_by
                                    ? 'unacknowledge'
                                    : 'acknowledge',
                                })
                              }
                            >
                              <Check
                                className={`size-4 ${item.acknowledged_by ? 'text-emerald-500' : ''}`}
                              />
                            </Button>
                            <Button
                              size='icon'
                              variant='ghost'
                              aria-label='Comentar alarma'
                              disabled={!canOperate}
                              onClick={() => {
                                setComment([item.id])
                                setNote('')
                              }}
                            >
                              <MessageSquare className='size-4' />
                            </Button>
                          </div>
                        </td>
                      </tr>
                      {expanded === item.id && (
                        <tr>
                          <td
                            colSpan={view === 'history' ? 10 : 9}
                            className='bg-muted/20 px-6 py-4'
                          >
                            <Detail incident={item} scenario={scenario} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
            {!items.length && (
              <Empty
                text={
                  query.isLoading
                    ? 'Cargando…'
                    : query.isError
                      ? 'No se pudieron obtener los incidentes.'
                      : 'No hay incidentes que coincidan con esta consulta.'
                }
              />
            )}
            <div className='flex items-center justify-between border-t px-4 py-3 text-xs text-muted-foreground'>
              <span>{query.data?.total ?? 0} incidentes · 25 por página</span>
              <div className='flex items-center gap-2'>
                <Button
                  size='sm'
                  variant='outline'
                  disabled={page <= 1}
                  onClick={() => {
                    setPage(page - 1)
                    setSelected([])
                    seen.current = null
                  }}
                >
                  Anterior
                </Button>
                <span>
                  {page} /{' '}
                  {Math.max(1, Math.ceil((query.data?.total ?? 0) / 25))}
                </span>
                <Button
                  size='sm'
                  variant='outline'
                  disabled={page * 25 >= (query.data?.total ?? 0)}
                  onClick={() => {
                    setPage(page + 1)
                    setSelected([])
                    seen.current = null
                  }}
                >
                  Siguiente
                </Button>
              </div>
            </div>
          </>
        )}
      </div>
      <Dialog
        open={!!comment}
        onOpenChange={(open) => {
          if (!open) setComment(null)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Comentario</DialogTitle>
            <DialogDescription>
              {comment?.length} incidente(s) · se conservará en el historial de
              atención.
            </DialogDescription>
          </DialogHeader>
          <textarea
            aria-label='Comentario'
            className='min-h-28 rounded border bg-background p-3 text-sm'
            maxLength={2000}
            value={note}
            onChange={(event) => setNote(event.target.value)}
          />
          <Button
            disabled={!note.trim() || mutation.isPending}
            onClick={() => {
              if (comment)
                mutation.mutate({ ids: comment, action: 'comment', note })
            }}
          >
            Guardar comentario
          </Button>
        </DialogContent>
      </Dialog>
      {editing && (
        <RuleEditor
          key={editing.condition_key}
          rule={editing}
          scenario={scenario}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null)
            refresh()
          }}
        />
      )}
    </EmsPage>
  )
}

function Empty({ text }: { text: string }) {
  return (
    <p className='p-10 text-center text-sm text-muted-foreground'>{text}</p>
  )
}
function Field({
  title,
  children,
}: {
  title: string
  children: React.ReactNode
}) {
  return (
    <label className='flex flex-col gap-1.5 text-xs'>
      <span className='text-muted-foreground'>{title}</span>
      {children}
    </label>
  )
}
function Detail({
  incident,
  scenario,
}: {
  incident: Incident
  scenario: string
}) {
  const events = useQuery({
    queryKey: ['alarm-events', scenario, incident.id],
    queryFn: async () =>
      (
        await api.get<
          { at: number; action: string; actor: string; detail: string }[]
        >(`/alarm-center/${scenario}/events/${incident.id}`)
      ).data,
  })
  const labels: Record<string, string> = {
    opened: 'Apertura',
    cleared: 'Recuperación',
    acknowledge: 'Reconocimiento',
    unacknowledge: 'Retirada de reconocimiento',
    comment: 'Comentario',
    rule_changed: 'Configuración',
  }
  return (
    <div className='space-y-4'>
      <div className='grid gap-4 md:grid-cols-2'>
        <div className='space-y-2'>
          <p>
            <strong>Evidencia</strong>
          </p>
          <p className='font-mono text-xs break-words'>{incident.evidence}</p>
          <p className='text-muted-foreground'>{incident.recommendation}</p>
        </div>
        <div className='grid grid-cols-2 gap-2 text-xs'>
          <span>Causa probable</span>
          <span>{incident.probable_cause}</span>
          <span>Interfaces</span>
          <span>{incident.interfaces.join(', ') || '—'}</span>
          <span>Procedimientos</span>
          <span>{incident.procedures.join(', ') || '—'}</span>
          <span>Severidad de origen</span>
          <span>{severities[incident.original_severity]}</span>
          <span>Reconocida</span>
          <span>{date(incident.acknowledged_at)}</span>
          <span>Identificador</span>
          <code className='break-all'>{incident.id}</code>
        </div>
      </div>
      <div className='flex flex-wrap items-center gap-2 border-t pt-2'>
        <span className='text-[10px] font-semibold tracking-wider text-muted-foreground uppercase'>
          Acciones de Mitigación (FCAPS):
        </span>
        <Link
          to={'/commands' as any}
          search={{ component: incident.component } as any}
        >
          <Button variant='outline' size='sm' className='h-7 gap-1.5 text-xs'>
            <Terminal className='size-3 text-primary' />
            Consola MML ({incident.component.toUpperCase()})
          </Button>
        </Link>
        <Link to='/traces/node'>
          <Button variant='outline' size='sm' className='h-7 gap-1.5 text-xs'>
            <Radio className='size-3 text-sky-500' />
            Capturar Traza PCAP
          </Button>
        </Link>
      </div>
      <details>
        <summary className='cursor-pointer text-xs font-medium'>
          Historial de atención y configuración (últimos 200 eventos)
        </summary>
        <div className='mt-3 max-h-52 space-y-3 overflow-auto'>
          {events.isError ? (
            <p>No se pudo cargar el historial de atención.</p>
          ) : events.isLoading ? (
            <p>Cargando…</p>
          ) : (
            events.data?.map((event, i) => (
              <div key={i} className='border-l-2 pl-3 text-xs'>
                <p>
                  {date(event.at)} · {labels[event.action] ?? event.action} ·{' '}
                  {event.actor}
                </p>
                <p className='mt-1 break-words whitespace-pre-wrap text-muted-foreground'>
                  {event.detail}
                </p>
              </div>
            ))
          )}
        </div>
      </details>
    </div>
  )
}

function RuleEditor({
  rule,
  scenario,
  onClose,
  onSaved,
}: {
  rule: Rule
  scenario: string
  onClose: () => void
  onSaved: () => void
}) {
  const [severity, setSeverity] = useState(rule.settings.severity ?? '')
  const [masked, setMasked] = useState(rule.settings.masked)
  const [silence, setSilence] = useState('keep')
  const [raise, setRaise] = useState(rule.settings.raise_seconds)
  const [clear, setClear] = useState(rule.settings.clear_seconds)
  const [reason, setReason] = useState('')
  const mutation = useMutation({
    mutationFn: async () =>
      api.put(`/alarm-center/${scenario}/rules`, {
        condition_key: rule.condition_key,
        severity: severity || null,
        masked,
        silence_minutes: silence === 'keep' ? null : Number(silence),
        raise_seconds: raise,
        clear_seconds: clear,
        reason,
      }),
    onSuccess: () => {
      toast.success('Regla guardada')
      onSaved()
    },
    onError: (error) =>
      toast.error(apiErrorMessage(error, 'No se pudo guardar la regla')),
  })
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
    >
      <DialogContent className='sm:max-w-xl'>
        <DialogHeader>
          <DialogTitle>Configuración de alarma</DialogTitle>
          <DialogDescription>
            {rule.network_function} · {rule.message}
          </DialogDescription>
        </DialogHeader>
        <code className='text-xs break-all text-muted-foreground'>
          {rule.condition_key}
        </code>
        <div className='grid gap-4 sm:grid-cols-2'>
          <Field title='Severidad aplicada'>
            <select
              className={selectClass}
              value={severity}
              onChange={(event) => setSeverity(event.target.value)}
            >
              <option value=''>Original ({severities[rule.severity]})</option>
              {Object.entries(severities).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </Field>
          <Field title='Silenciar nuevos avisos'>
            <select
              className={selectClass}
              value={silence}
              onChange={(event) => setSilence(event.target.value)}
            >
              <option value='keep'>Conservar configuración</option>
              <option value='0'>Sin silencio</option>
              <option value='15'>15 minutos</option>
              <option value='60'>1 hora</option>
              <option value='480'>8 horas</option>
              <option value='1440'>24 horas</option>
            </select>
          </Field>
          <Field title='Persistencia antes de abrir (s)'>
            <Input
              type='number'
              min={0}
              max={600}
              value={raise}
              onChange={(event) => setRaise(Number(event.target.value))}
            />
          </Field>
          <Field title='Estabilidad antes de recuperar (s)'>
            <Input
              type='number'
              min={0}
              max={600}
              value={clear}
              onChange={(event) => setClear(Number(event.target.value))}
            />
          </Field>
        </div>
        <label className='flex items-center gap-2 text-sm'>
          <input
            type='checkbox'
            checked={masked}
            onChange={(event) => setMasked(event.target.checked)}
          />
          Enmascarar en la lista principal
        </label>
        <p className='text-xs text-muted-foreground'>
          No elimina incidentes ni declara recuperación. Los tiempos se evalúan
          con el muestreo del observador, no son temporizadores del core. El
          silencio afecta a los avisos en pantalla; la recolección continúa.
        </p>
        <Field title='Motivo del cambio'>
          <Input
            maxLength={1000}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
        </Field>
        <div className='flex justify-end gap-2'>
          <Button variant='outline' onClick={onClose}>
            Cancelar
          </Button>
          <Button
            disabled={
              reason.trim().length < 3 ||
              mutation.isPending ||
              !Number.isFinite(raise) ||
              !Number.isFinite(clear) ||
              raise < 0 ||
              clear < 0 ||
              raise > 600 ||
              clear > 600
            }
            onClick={() => mutation.mutate()}
          >
            Guardar
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
