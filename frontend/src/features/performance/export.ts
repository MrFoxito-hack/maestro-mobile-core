import type { KpiQueryResult } from './types'

export function exportPerformance(result: KpiQueryResult, format: 'csv' | 'json') {
  const csv = (value: string | number) =>
    `"${String(value).replace(/"/g, '""')}"`
  const content = format === 'json' ? JSON.stringify(result, null, 2) : [
    ['timestamp', 'object_id', 'counter_id', 'value', 'unit', 'aggregation', 'quality'].map(csv).join(','),
    ...result.series.flatMap((series) => series.points.map((point) =>
      [point.timestamp, series.object_id, series.counter_id, point.value, series.unit,
        result.aggregation, series.quality].map(csv).join(','))),
  ].join('\r\n')
  const url = URL.createObjectURL(new Blob([content], {
    type: format === 'json' ? 'application/json' : 'text/csv;charset=utf-8',
  }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `maestro-performance.${format}`
  anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
