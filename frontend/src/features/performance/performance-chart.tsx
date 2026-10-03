import { useState } from 'react'
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { KpiQueryResult } from './types'

export type ChartMode = 'line' | 'bar' | 'table'
const COLORS = [
  '#0284c7',
  '#059669',
  '#d97706',
  '#7c3aed',
  '#dc2626',
  '#0891b2',
  '#65a30d',
  '#64748b',
]
const formatTime = (v: unknown) =>
  new Date(String(v)).toLocaleString('es-PE', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })

export function PerformanceChart({
  result,
  mode = 'line',
}: {
  result?: KpiQueryResult
  mode?: ChartMode
}) {
  const [selectedUnit, setSelectedUnit] = useState('')
  const units = [...new Set((result?.series ?? []).map((s) => s.unit))]
  const unit = units.includes(selectedUnit) ? selectedUnit : units[0]
  const series = (result?.series ?? []).filter(
    (s) => mode === 'table' || s.unit === unit
  )
  // Internal IDs are never used as user-facing labels or object paths in the chart.
  const rowsByTime = new Map<string, Record<string, string | number>>()
  series.forEach((s, index) =>
    s.points.forEach((point) => {
      const row = rowsByTime.get(point.timestamp) ?? {
        timestamp: point.timestamp,
      }
      row[`s${index}`] = point.value
      rowsByTime.set(point.timestamp, row)
    })
  )
  const rows = [...rowsByTime.values()].sort((a, b) =>
    String(a.timestamp).localeCompare(String(b.timestamp))
  )
  if (!series.length || !rows.length) {
    return (
      <div className='flex h-full min-h-80 items-center justify-center text-sm text-muted-foreground'>
        Sin muestras para esta selección e intervalo.
      </div>
    )
  }
  if (mode === 'table') {
    return (
      <div className='h-full overflow-auto rounded border'>
        <table className='w-full border-collapse text-xs'>
          <thead className='sticky top-0 bg-card'>
            <tr>
              <th className='border-b p-3 text-left font-medium whitespace-nowrap'>
                Fecha y hora
              </th>
              {series.map((s) => (
                <th
                  key={s.id}
                  className='min-w-40 border-b p-3 text-right font-medium'
                >
                  {s.label} ({s.unit})
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.timestamp} className='border-b hover:bg-muted/40'>
                <td className='p-3 whitespace-nowrap text-muted-foreground'>
                  {formatTime(row.timestamp)}
                </td>
                {series.map((s, index) => (
                  <td key={s.id} className='p-3 text-right tabular-nums'>
                    {typeof row[`s${index}`] === 'number'
                      ? Number(row[`s${index}`]).toLocaleString('es-PE', {
                          maximumFractionDigits: 3,
                        })
                      : '—'}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }
  return (
    <div className='flex h-full min-h-80 flex-col'>
      {units.length > 1 && (
        <div className='mb-3 flex flex-wrap items-center gap-1 text-xs'>
          <span className='mr-1 text-muted-foreground'>Unidad</span>
          {units.map((u) => (
            <button
              key={u}
              aria-pressed={u === unit}
              onClick={() => setSelectedUnit(u)}
              className={`rounded px-2 py-1 ${u === unit ? 'bg-accent font-medium' : 'text-muted-foreground hover:bg-muted'}`}
            >
              {u || 'Sin unidad'}
            </button>
          ))}
        </div>
      )}
      <div className='min-h-0 flex-1'>
        <ResponsiveContainer width='100%' height='100%'>
          <ComposedChart
            data={rows}
            margin={{ top: 12, right: 16, left: 0, bottom: 10 }}
          >
            <CartesianGrid
              stroke='var(--border)'
              strokeDasharray='3 3'
              vertical={false}
            />
            <XAxis
              dataKey='timestamp'
              tickFormatter={formatTime}
              minTickGap={50}
              fontSize={10}
              stroke='var(--muted-foreground)'
            />
            <YAxis
              fontSize={10}
              width={64}
              stroke='var(--muted-foreground)'
              domain={unit === '%' ? [0, 100] : ['auto', 'auto']}
              label={{
                value: unit,
                angle: -90,
                position: 'insideLeft',
                fill: 'var(--muted-foreground)',
              }}
            />
            <Tooltip
              labelFormatter={formatTime}
              contentStyle={{
                background: 'var(--popover)',
                border: '1px solid var(--border)',
                borderRadius: 6,
                fontSize: 12,
              }}
            />
            <Legend
              verticalAlign='top'
              align='left'
              iconType='plainline'
              wrapperStyle={{ fontSize: 11, paddingBottom: 16 }}
            />
            {series.map((s, index) =>
              mode === 'bar' ? (
                <Bar
                  key={s.id}
                  dataKey={`s${index}`}
                  name={s.label}
                  fill={COLORS[index % COLORS.length]}
                  isAnimationActive={false}
                />
              ) : (
                <Line
                  key={s.id}
                  dataKey={`s${index}`}
                  name={s.label}
                  stroke={COLORS[index % COLORS.length]}
                  type='linear'
                  strokeWidth={1.5}
                  dot={rows.length < 30}
                  connectNulls={false}
                  isAnimationActive={false}
                />
              )
            )}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
