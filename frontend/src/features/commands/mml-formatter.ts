import type {
  OperationDefinition,
  OperationResult,
  OperationsCatalog,
} from './types'

export const MML_TO_OP: Record<
  string,
  { opId: string; defaultTarget?: string }
> = {
  'DSP GNB-STATUS': { opId: 'gnb.status', defaultTarget: 'gnb' },
  'DSP GNB-INFO': { opId: 'gnb.info', defaultTarget: 'gnb' },
  'LST GNB-AMF': { opId: 'gnb.amf-list', defaultTarget: 'gnb' },
  'DSP GNB-AMF-INFO': { opId: 'gnb.amf-info', defaultTarget: 'gnb' },
  'DSP GNB-UECOUNT': { opId: 'gnb.ue-count', defaultTarget: 'gnb' },
  'LST GNB-UE': { opId: 'gnb.ue-list', defaultTarget: 'gnb' },
  'DSP UE-STATUS': { opId: 'ue.status', defaultTarget: 'ue' },
  'DSP UE-INFO': { opId: 'ue.info', defaultTarget: 'ue' },
  'DSP UE-COVERAGE': { opId: 'ue.coverage', defaultTarget: 'ue' },
  'DSP UE-RLS': { opId: 'ue.rls-state', defaultTarget: 'ue' },
  'DSP UE-TIMERS': { opId: 'ue.timers', defaultTarget: 'ue' },
  'LST PDU-SESSION': { opId: 'ue.pdu-list', defaultTarget: 'ue' },
  'RLS PDU-SESSION': { opId: 'ue.pdu-release', defaultTarget: 'ue' },
  'SET UE-DEREGISTER': { opId: 'ue.deregister', defaultTarget: 'ue' },
  'ACT UE-ATTACH': { opId: 'ue.attach', defaultTarget: 'ue' },
  'SET UE-ATTACH': { opId: 'ue.attach', defaultTarget: 'ue' },
  'SET UE-REGISTER': { opId: 'ue.attach', defaultTarget: 'ue' },
  'RST UE': { opId: 'ue.attach', defaultTarget: 'ue' },
  'REC UE': { opId: 'ue.attach', defaultTarget: 'ue' },
  'LST AMF-UE-CONTEXT': { opId: 'amf.ue-info', defaultTarget: 'amf' },
  'LST AMF-GNB-ASSOC': { opId: 'amf.gnb-info', defaultTarget: 'amf' },
  'LST SMF-PDU-SESSION': { opId: 'smf.pdu-info', defaultTarget: 'smf' },
  'LST CHF-ACCOUNT': { opId: 'chf.accounts', defaultTarget: 'chf' },
  'LST CHF-SESSION': { opId: 'chf.sessions', defaultTarget: 'chf' },
  'LST CHF-CDR': { opId: 'chf.cdrs', defaultTarget: 'chf' },
  'ADD 5G-SUB': { opId: 'subscriber.create', defaultTarget: 'udm' },
  'MOD 5G-SUB': { opId: 'subscriber.update', defaultTarget: 'udm' },
  'RMV 5G-SUB': { opId: 'subscriber.delete', defaultTarget: 'udm' },
  'ADD CHF-QUOTA': { opId: 'chf.quota', defaultTarget: 'chf' },
  'MOD CHF-STATE': { opId: 'chf.state', defaultTarget: 'chf' },
  'DSP CHF-BALANCE': { opId: 'chf.balance', defaultTarget: 'chf' },
  'MOD PCC-QOS': { opId: 'pcf.qos', defaultTarget: 'pcf' },
  'DSP NWDAF-ANALYTICS': { opId: 'nwdaf.analytics', defaultTarget: 'nwdaf' },
  'SET NWDAF-MODE': { opId: 'nwdaf.mode', defaultTarget: 'nwdaf' },
  'DSP NF-STATUS': { opId: 'system.status' },
  'LST NF-LOG': { opId: 'system.logs' },
  'CHK NF-ENDPOINT': { opId: 'network.endpoints' },
  'RST NF': { opId: 'system.restart' },
  'DSP SW-VERSION': { opId: 'software.version' },
}

export const MML_SUGGESTIONS = [
  'ADD 5G-SUB: IMSI="imsi-999700000000006", SST=1, SD="000002", DNN="corporate";',
  'MOD 5G-SUB: IMSI="imsi-999700000000006", SD="000002", DNN="corporate";',
  'RMV 5G-SUB: IMSI="imsi-999700000000006";',
  'ADD CHF-QUOTA: IMSI="imsi-999700000000004", QUOTA_MB=50;',
  'MOD CHF-STATE: IMSI="imsi-999700000000004", STATE="ACTIVE";',
  'DSP CHF-BALANCE: IMSI="imsi-999700000000004";',
  'MOD PCC-QOS: IMSI="imsi-999700000000004", 5QI=9, MBR_DL=20, MBR_UL=20;',
  'DSP NWDAF-ANALYTICS: NF="UPF-01", HORIZON=15;',
  'SET NWDAF-MODE: MODE="MANUAL";',
  'ACT UE-ATTACH;',
  'DSP UE-STATUS;',
  'LST AMF-UE-CONTEXT;',
  'LST AMF-GNB-ASSOC;',
  'LST SMF-PDU-SESSION;',
  'LST SMF-PDU-SESSION: SMF="SMF-02";',
  'DSP NF-STATUS: NF="AMF";',
  'DSP NF-STATUS: NF="SMF";',
  'DSP NF-STATUS: NF="SMF-02";',
  'DSP NF-STATUS: NF="UPF-01";',
  'DSP NF-STATUS: NF="UPF-02";',
  'LST NF-LOG: NF="AMF", LINES=100;',
  'CHK NF-ENDPOINT: NF="AMF";',
  'RST NF: NF="AMF";',
  'DSP SW-VERSION: NF="AMF";',
]

const PARAMETER_NAMES: Record<string, string> = {
  key: 'K',
  apn_dnn: 'DNN',
  five_qi: '5QI',
  mbr_dl_mbps: 'MBR_DL',
  mbr_ul_mbps: 'MBR_UL',
}

export function redactMml(command: string): string {
  return command.replace(
    /\b(K|KEY|OPC)\s*=\s*(?:"[^"]*"|'[^']*'|[^,;\s]+)/gi,
    '$1="[REDACTED]"'
  )
}

export function toMmlSyntax(
  operation: OperationDefinition,
  componentId: string,
  componentLabel: string,
  parameters: Record<string, unknown>
): string {
  const paramPairs: string[] = []
  for (const [k, v] of Object.entries(parameters)) {
    const name = PARAMETER_NAMES[k] ?? k.toUpperCase()
    if (v !== undefined && v !== '' && v !== null) {
      if (typeof v === 'number') {
        paramPairs.push(`${name}=${v}`)
      } else {
        paramPairs.push(`${name}=${JSON.stringify(String(v))}`)
      }
    }
  }

  const codeMap: Record<string, string> = {
    'gnb.status': 'DSP GNB-STATUS',
    'gnb.info': 'DSP GNB-INFO',
    'gnb.amf-list': 'LST GNB-AMF',
    'gnb.amf-info': 'DSP GNB-AMF-INFO',
    'gnb.ue-count': 'DSP GNB-UECOUNT',
    'gnb.ue-list': 'LST GNB-UE',
    'ue.status': 'DSP UE-STATUS',
    'ue.info': 'DSP UE-INFO',
    'ue.coverage': 'DSP UE-COVERAGE',
    'ue.rls-state': 'DSP UE-RLS',
    'ue.timers': 'DSP UE-TIMERS',
    'ue.pdu-list': 'LST PDU-SESSION',
    'ue.pdu-release': 'RLS PDU-SESSION',
    'ue.deregister': 'SET UE-DEREGISTER',
    'ue.attach': 'ACT UE-ATTACH',
    'amf.ue-info': 'LST AMF-UE-CONTEXT',
    'amf.gnb-info': 'LST AMF-GNB-ASSOC',
    'smf.pdu-info': 'LST SMF-PDU-SESSION',
    'smf2.pdu-info': 'LST SMF-PDU-SESSION',
    'chf.accounts': 'LST CHF-ACCOUNT',
    'chf.sessions': 'LST CHF-SESSION',
    'chf.cdrs': 'LST CHF-CDR',
    ...Object.fromEntries(
      Object.entries(MML_TO_OP)
        .filter(([, mapping]) =>
          ['subscriber.', 'chf.', 'pcf.', 'nwdaf.'].some((prefix) =>
            mapping.opId.startsWith(prefix)
          )
        )
        .map(([command, mapping]) => [mapping.opId, command])
    ),
    'system.status': `DSP NF-STATUS`,
    'system.logs': `LST NF-LOG`,
    'network.endpoints': `CHK NF-ENDPOINT`,
    'system.restart': `RST NF`,
    'software.version': `DSP SW-VERSION`,
  }

  const baseCode =
    codeMap[operation.id] ??
    `${operation.mutating ? 'SET' : 'DSP'} ${componentId.toUpperCase()}-${operation.id.replace('.', '-').toUpperCase()}`

  if (
    operation.id.startsWith('system.') ||
    operation.id.startsWith('network.') ||
    operation.id.startsWith('software.')
  ) {
    paramPairs.unshift(`NF="${componentLabel}"`)
  }

  if (operation.id === 'smf.pdu-info' || operation.id === 'smf2.pdu-info') {
    paramPairs.unshift(`SMF="${componentId === 'smf2' ? 'SMF-02' : 'SMF-01'}"`)
  }
  const paramsString = paramPairs.length ? `: ${paramPairs.join(', ')}` : ':'
  return `%%${baseCode}${paramsString};%%`
}

export type ParseMmlResult =
  | {
      success: true
      componentId: string
      componentLabel: string
      operationId: string
      parameters: Record<string, unknown>
      normalizedMml: string
    }
  | {
      success: false
      error: string
    }

export function parseMmlCommand(
  rawInput: string,
  catalog?: OperationsCatalog,
  selectedComponentId?: string
): ParseMmlResult {
  if (!catalog || !catalog.components.length) {
    return { success: false, error: 'Catálogo de operaciones no disponible.' }
  }

  let clean = rawInput.trim()
  if (!clean) {
    return { success: false, error: 'El comando no puede estar vacío.' }
  }

  // Quitar %% inicial y final si existen
  if (clean.startsWith('%%')) clean = clean.slice(2).trim()
  if (clean.endsWith('%%')) clean = clean.slice(0, -2).trim()
  // Quitar punto y coma final
  if (clean.endsWith(';')) clean = clean.slice(0, -1).trim()

  // Separar comando y parámetros por ':'
  const colonIndex = clean.indexOf(':')
  let commandStr: string
  let paramsStr = ''

  if (colonIndex >= 0) {
    commandStr = clean.slice(0, colonIndex).trim().toUpperCase()
    paramsStr = clean.slice(colonIndex + 1).trim()
  } else {
    commandStr = clean.trim().toUpperCase()
  }

  // Parsear parámetros KEY=VAL
  const rawParams: Record<string, string> = {}
  if (paramsStr) {
    const regex = /([A-Za-z0-9_]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^,;\s]+))/g
    let match
    let consumed = 0
    while ((match = regex.exec(paramsStr)) !== null) {
      const separator = paramsStr.slice(consumed, match.index).trim()
      if (separator !== (consumed ? ',' : '')) {
        return { success: false, error: 'Sintaxis de parámetros no válida.' }
      }
      consumed = regex.lastIndex
      const inputKey = match[1].toLowerCase()
      const key =
        Object.entries(PARAMETER_NAMES).find(
          ([, alias]) => alias.toLowerCase() === inputKey
        )?.[0] ?? (inputKey === 'node' ? 'node_name' : inputKey)
      if (Object.prototype.hasOwnProperty.call(rawParams, key)) {
        return { success: false, error: `Parámetro duplicado: ${key}.` }
      }
      const valStr = match[2] ?? match[3] ?? match[4]
      rawParams[key] = valStr
    }
    if (paramsStr.slice(consumed).trim()) {
      return { success: false, error: 'Sintaxis de parámetros no válida.' }
    }
  }

  // Buscar en MML_TO_OP
  const mapping = MML_TO_OP[commandStr]
  if (!mapping) {
    return {
      success: false,
      error: `Comando MML "${commandStr}" no reconocido. Ejemplos válidos: DSP GNB-STATUS;, LST NF-LOG: NF="AMF", LINES=50;`,
    }
  }

  const { opId, defaultTarget } = mapping
  const isSmfPdu = opId === 'smf.pdu-info'
  if (isSmfPdu && rawParams['smf'] && rawParams['nf']) {
    return { success: false, error: 'Use solo un selector: SMF o NF.' }
  }

  // Determinar componente destino
  let targetCompId = defaultTarget ?? selectedComponentId
  if (isSmfPdu && selectedComponentId === 'smf2') targetCompId = 'smf2'
  if (opId.startsWith('subscriber.') && selectedComponentId === 'udr')
    targetCompId = 'udr'
  const selector = isSmfPdu ? rawParams['smf'] ?? rawParams['nf'] : rawParams['nf']
  if (selector && opId !== 'nwdaf.analytics') {
    const nfQuery = String(selector).toLowerCase()
    const found = catalog.components.find((c) => {
      const lowerId = c.id.toLowerCase()
      const lowerLabel = c.label.toLowerCase()
      const cleanLabel = lowerLabel.replace(/\s*\([^)]*\)/g, '').trim()
      return (
        lowerId === nfQuery ||
        lowerLabel === nfQuery ||
        cleanLabel === nfQuery ||
        lowerLabel.startsWith(nfQuery + ' ') ||
        lowerLabel.startsWith(nfQuery + '(')
      )
    })
    if (found) {
      targetCompId = found.id
    } else {
      return {
        success: false,
        error: 'La NF indicada no existe en este escenario.',
      }
    }
  }

  if (!targetCompId) {
    return {
      success: false,
      error: `El comando "${commandStr}" requiere especificar la función de red mediante el parámetro NF="nombre" (ej. NF="AMF").`,
    }
  }

  const component = catalog.components.find((c) => c.id === targetCompId)
  if (!component) {
    return {
      success: false,
      error: `Función de red "${targetCompId}" no encontrada en el escenario actual.`,
    }
  }

  const resolvedOpId = isSmfPdu && component.id === 'smf2' ? 'smf2.pdu-info' : opId
  const operation = component.operations.find((op) => op.id === resolvedOpId)
  if (!operation) {
    return {
      success: false,
      error: `La operación "${opId}" no está soportada para el nodo "${component.label}".`,
    }
  }

  // Mapear parámetros a los esperados por el backend
  const permitted = new Set([
    'nf',
    ...(isSmfPdu ? ['smf'] : []),
    ...operation.parameters.map((p) => p.id.toLowerCase()),
  ])
  if (permitted.has('node_name')) permitted.add('node')
  const unknown = Object.keys(rawParams).filter((key) => !permitted.has(key))
  if (unknown.length)
    return {
      success: false,
      error: `Parámetros no soportados: ${unknown.join(', ')}.`,
    }
  const cleanParams: Record<string, unknown> = {}
  for (const p of operation.parameters) {
    if (rawParams[p.id.toLowerCase()] !== undefined) {
      const raw = rawParams[p.id.toLowerCase()]
      const numeric =
        p.type === 'number' ||
        p.options?.some((option) => typeof option.value === 'number')
      cleanParams[p.id] = numeric ? Number(raw) : raw
    } else if (rawParams['node'] !== undefined && p.id === 'node_name') {
      cleanParams['node_name'] = rawParams['node']
    } else if (p.default !== undefined) {
      cleanParams[p.id] = p.default
    }
    const value = cleanParams[p.id]
    if (p.required && (value === undefined || value === ''))
      return { success: false, error: `Falta el parámetro ${p.label}.` }
    if (value === undefined) continue
    if (
      p.pattern &&
      (typeof value !== 'string' || !new RegExp(p.pattern).test(value))
    )
      return { success: false, error: `Formato inválido para ${p.label}.` }
    if (
      typeof value === 'number' &&
      (!Number.isFinite(value) ||
        (p.type === 'number' &&
          p.integer !== false &&
          !Number.isInteger(value)) ||
        (p.minimum !== undefined && value < p.minimum) ||
        (p.maximum !== undefined && value > p.maximum))
    )
      return { success: false, error: `Valor fuera de rango para ${p.label}.` }
    if (
      p.options?.length &&
      !p.options.some((option) => option.value === value)
    )
      return { success: false, error: `Valor no permitido para ${p.label}.` }
  }

  const normalized = toMmlSyntax(
    operation,
    component.id,
    component.label,
    cleanParams
  )

  return {
    success: true,
    componentId: component.id,
    componentLabel: component.label,
    operationId: operation.id,
    parameters: cleanParams,
    normalizedMml: normalized,
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function formatTelcoValue(key: string, value: unknown): string | null {
  if (value === null || value === undefined) return 'Unavailable'

  // S-NSSAI: { sst: 1, sd: '000001' }
  if (
    key === 'snssai' &&
    typeof value === 'object' &&
    value !== null &&
    'sst' in value
  ) {
    const s = value as { sst: number; sd?: string }
    return s.sd ? `SST=${s.sst}, SD=${s.sd}` : `SST=${s.sst}`
  }

  // QoS Flows: [ { qfi: 1, 5qi: 9 }, ... ]
  if (
    key === 'qos_flows' &&
    Array.isArray(value) &&
    value.every((v) => typeof v === 'object' && v !== null && ('qfi' in v || '5qi' in v))
  ) {
    return value
      .map((q: any) => `QFI=${q.qfi ?? '?'} (5QI=${q['5qi'] ?? q.five_qi ?? '?'})`)
      .join(', ')
  }

  // N3 Endpoints: { gnb: { teid, addr }, upf: { teid, addr, pdr_id } }
  if (
    (key === 'gnb' || key === 'upf') &&
    typeof value === 'object' &&
    value !== null &&
    'addr' in value &&
    'teid' in value
  ) {
    const ep = value as { addr: string; teid: number; pdr_id?: number }
    return ep.pdr_id !== undefined
      ? `${ep.addr} (TEID=${ep.teid}, PDR=${ep.pdr_id})`
      : `${ep.addr} (TEID=${ep.teid})`
  }

  // Primitive array: [1, 2, 3] or ['a', 'b']
  if (Array.isArray(value) && value.every((x) => typeof x !== 'object' || x === null)) {
    return value.length > 0
      ? value.map((x) => String(x ?? 'Unavailable')).join(', ')
      : 'None'
  }

  return null
}

function formatFields(value: unknown, prefix = ''): string[] {
  if (!isRecord(value))
    return [
      `${(prefix || 'Value').padEnd(35)} : ${JSON.stringify(value) ?? 'Unavailable'}`,
    ]
  return Object.entries(value).flatMap(([rawKey, item]) => {
    const key = rawKey === 'pdu_state' ? 'state' : rawKey
    const cleanKey = key.replace(/_/g, ' ')
    const label = prefix ? `${prefix} ${cleanKey}` : cleanKey

    const telcoFormatted = formatTelcoValue(key, item)
    if (telcoFormatted !== null) {
      return [`${label.padEnd(35)} : ${telcoFormatted}`]
    }

    if (isRecord(item)) {
      return formatFields(item, label)
    }

    if (Array.isArray(item) && item.some(isRecord)) {
      return item.flatMap((elem, idx) => {
        const itemLabel = item.length > 1 ? `${label} [${idx + 1}]` : label
        if (isRecord(elem)) {
          return formatFields(elem, itemLabel)
        }
        return [`${itemLabel.padEnd(35)} : ${String(elem)}`]
      })
    }

    const display =
      item === null || item === undefined
        ? 'Unavailable'
        : typeof item === 'object'
          ? JSON.stringify(item)
          : String(item)
    return [`${label.padEnd(35)} : ${display}`]
  })
}

export function formatTelcoReport(
  result: OperationResult,
  mmlCommand: string
): string {
  const timestamp = new Date(result.started_at)
    .toISOString()
    .replace('T', ' ')
    .slice(0, 19)
  const succeeded = result.status === 'success'
  const separator = '-'.repeat(70)
  let data: unknown = result.data
  if (data === null && result.output) {
    try {
      data = JSON.parse(result.output) as unknown
    } catch {
      // keep raw text
    }
  }
  const items = Array.isArray(data)
    ? data
    : isRecord(data) && Array.isArray(data.items)
      ? data.items
      : null
  const body = items
    ? items.flatMap((item, index) => [
        ...(index ? [separator] : []),
        ...formatFields(item),
      ])
    : data !== null && data !== undefined
      ? formatFields(data)
      : [result.output.trim() || '(Sin salida de texto)']
  const original = mmlCommand.trim().replace(/^%%|%%$/g, '')
  return [
    `+++    MAEstro 5G CORE OMC    ${timestamp}`,
    `O&M    #${result.id}`,
    `%%${redactMml(original)}%%`,
    succeeded
      ? 'RETCODE = 0  Operation Succeeded.'
      : `RETCODE = 1001  Operation Failed: ${result.error ?? result.output ?? 'Error desconocido'}.`,
    '',
    result.operation_label,
    separator,
    ...body,
    separator,
    `(Number of results = ${succeeded ? (items?.length ?? 1) : 0})`,
    '',
    '---    END',
  ].join('\n')
}
