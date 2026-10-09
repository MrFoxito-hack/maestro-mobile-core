import { describe, expect, it } from 'vitest'
import {
  formatTelcoReport,
  MML_TO_OP,
  parseMmlCommand,
  toMmlSyntax,
} from './mml-formatter'
import type {
  OperationDefinition,
  OperationResult,
  OperationsCatalog,
} from './types'

const subscriber: OperationDefinition = {
  id: 'subscriber.create',
  label: 'ADD 5G-SUB',
  category: 'UDM',
  description: '',
  allowed: true,
  mutating: true,
  parameters: [
    {
      id: 'imsi',
      label: 'IMSI',
      type: 'text',
      required: true,
      pattern: '^(?:imsi-)?\\d{14,15}$',
    },
    { id: 'key', label: 'K', type: 'text' },
    { id: 'sd', label: 'SD', type: 'text', pattern: '^[0-9a-fA-F]{6}$' },
    { id: 'sst', label: 'SST', type: 'number', required: true, minimum: 0, maximum: 255 },
    {
      id: 'apn_dnn',
      label: 'DNN',
      type: 'select',
      options: ['internet', '5g-plus', 'corporate'].map(value => ({ value, label: value })),
    },
  ],
}
const analytics: OperationDefinition = {
  id: 'nwdaf.analytics',
  label: 'DSP NWDAF-ANALYTICS',
  category: 'NWDAF',
  description: '',
  allowed: true,
  mutating: false,
  parameters: [
    {
      id: 'nf',
      label: 'NF',
      type: 'select',
      required: true,
      options: [{ value: 'UPF-02', label: 'UPF-02' }],
    },
    {
      id: 'horizon',
      label: 'HORIZON',
      type: 'select',
      options: [{ value: 30, label: '30' }],
    },
  ],
}
const qos: OperationDefinition = {
  id: 'pcf.qos',
  label: 'MOD PCC-QOS',
  category: 'PCF',
  description: '',
  allowed: true,
  mutating: true,
  parameters: [
    { id: 'five_qi', label: '5QI', type: 'number', default: 9 },
    {
      id: 'mbr_dl_mbps',
      label: 'MBR DL',
      type: 'number',
      integer: false,
      minimum: 0.001,
    },
  ],
}
const catalog: OperationsCatalog = {
  scenario_id: '5g-sa',
  scenario_name: '',
  execution_mode: 'remote',
  native_discovery: { available: true, message: null },
  components: [subscriber, analytics, qos].map((op, index) => ({
    id: ['udm', 'nwdaf', 'pcf'][index],
    label: ['UDM', 'NWDAF', 'PCF'][index],
    unit: '',
    operations: index === 0 ? [op, { ...op, id: 'subscriber.update', label: 'SET 5G-SUB' }, { ...op, id: 'subscriber.delete', label: 'RMV 5G-SUB', parameters: [op.parameters[0]] }] : [op],
  })),
}

describe('MML transactions', () => {
  it.each([
    ['001', 1, '000001', 'internet'], ['004', 1, '000001', 'internet'],
    ['002', 2, '000002', '5g-plus'], ['005', 2, '000002', '5g-plus'],
    ['003', 3, '000003', 'corporate'], ['006', 3, '000003', 'corporate'],
  ])('parses complete slicing triplets for UE %s', (suffix, sst, sd, dnn) => {
    for (const verb of ['ADD', 'SET', 'MOD']) {
      const result = parseMmlCommand(`${verb} 5G-SUB: IMSI="999700000000${suffix}", SST=${sst}, SD="${sd}", DNN="${dnn}";`, catalog)
      expect(result).toMatchObject({ success: true, parameters: { sst, sd, apn_dnn: dnn } })
      if (result.success) expect(parseMmlCommand(result.normalizedMml, catalog)).toEqual(result)
    }
    expect(parseMmlCommand(`RMV 5G-SUB: IMSI="999700000000${suffix}";`, catalog).success).toBe(true)
  })
  it.each([
    'SST=1, SD="000003", DNN="corporate"', 'SST=2, SD="000002", DNN="internet"',
    'SST=3, DNN="corporate"', 'SD="000003", DNN="corporate"', 'SST=3, SD="000003"',
    'SST=3, SST=2, SD="000003", DNN="corporate"', 'SST=3, SD="00003", DNN="corporate"',
  ])('rejects incomplete, incompatible or duplicated triplets: %s', (parameters) => {
    expect(parseMmlCommand(`SET 5G-SUB: IMSI="999700000000006", ${parameters};`, catalog).success).toBe(false)
  })
  const smfCatalog: OperationsCatalog = {
    ...catalog,
    components: ['smf', 'smf2'].map((id, index) => ({
      id,
      label: index ? 'SMF-02 (Corporate)' : 'SMF-01 (Internet)',
      unit: index ? 'open5gs-smfd2' : 'open5gs-smfd',
      operations: ['system.status', `${id}.pdu-info`].map((operationId) => ({
        id: operationId, label: '', category: '', description: '',
        allowed: true, mutating: false, parameters: [],
      })),
    })),
  }

  it('selects the corporate SMF explicitly and preserves the target on round trip', () => {
    const result = parseMmlCommand('LST SMF-PDU-SESSION: SMF="SMF-02";', smfCatalog)
    expect(result).toMatchObject({success: true, componentId: 'smf2', operationId: 'smf2.pdu-info'})
    if (result.success) expect(parseMmlCommand(result.normalizedMml, smfCatalog)).toEqual(result)
    expect(parseMmlCommand('DSP NF-STATUS: NF="SMF-02";', smfCatalog)).toMatchObject({
      success: true, componentId: 'smf2', operationId: 'system.status',
    })
    expect(parseMmlCommand('LST SMF-PDU-SESSION;', smfCatalog)).toMatchObject({
      success: true, componentId: 'smf', operationId: 'smf.pdu-info',
    })
  })

  it('rejects ambiguous or nonexistent SMF selectors', () => {
    for (const command of [
      'LST SMF-PDU-SESSION: SMF="SMF-03";',
      'LST SMF-PDU-SESSION: SMF="SMF-02", NF="SMF-01";',
    ]) expect(parseMmlCommand(command, smfCatalog).success).toBe(false)
  })

  it('preserves identifiers and leading zeros while mapping aliases', () => {
    const result = parseMmlCommand(
      'ADD 5G-SUB: IMSI="099700000000007", SST=3, SD="000003", K="00000000000000000000000000000001", DNN="corporate";',
      catalog
    )
    expect(result.success).toBe(true)
    if (!result.success) return
    expect(result.parameters).toMatchObject({
      imsi: '099700000000007',
      sd: '000003',
      sst: 3,
      apn_dnn: 'corporate',
    })
    expect(result.parameters.key).toBe('00000000000000000000000000000001')
    expect(result.normalizedMml).toContain('DNN="corporate"')
    expect(parseMmlCommand(result.normalizedMml, catalog)).toEqual(result)
  })

  it('uses analytics NF as a filter on NWDAF', () => {
    const result = parseMmlCommand(
      'DSP NWDAF-ANALYTICS: NF="UPF-02", HORIZON=30;',
      catalog,
      'nwdaf'
    )
    expect(result).toMatchObject({
      success: true,
      componentId: 'nwdaf',
      parameters: { nf: 'UPF-02', horizon: 30 },
    })
  })

  it('maps 5QI and decimal MBR and round trips form syntax', () => {
    const syntax = toMmlSyntax(qos, 'pcf', 'PCF', {
      five_qi: 9,
      mbr_dl_mbps: 1.5,
    })
    expect(syntax).toContain('5QI=9, MBR_DL=1.5')
    expect(parseMmlCommand(syntax, catalog)).toMatchObject({
      success: true,
      parameters: { five_qi: 9, mbr_dl_mbps: 1.5 },
    })
  })

  it.each([
    'ADD 5G-SUB: SD="000002";',
    'ADD 5G-SUB: IMSI="999700000000007", SST=1.5;',
    'ADD 5G-SUB: IMSI="999700000000007", K="a", KEY="b";',
    'ADD 5G-SUB: IMSI="999700000000007", WHAT=1;',
    'ADD 5G-SUB: IMSI="999700000000007" garbage;',
    'DSP NWDAF-ANALYTICS: NF="UPF-02", HORIZON=16;',
    'MOD PCC-QOS: MBR_DL=NaN;',
  ])('rejects malformed commands: %s', (command) => {
    expect(parseMmlCommand(command, catalog).success).toBe(false)
  })

  it('removes EPC commands', () => {
    expect(
      Object.keys(MML_TO_OP).some((command) => command.includes('MME'))
    ).toBe(false)
  })

  it('formats canonical success and failure without secret material', () => {
    const result: OperationResult = {
      id: 'test-run',
      scenario_id: '5g-sa',
      component_id: 'chf',
      component_label: 'CHF',
      testbed_id: '',
      operation_id: 'chf.balance',
      operation_label: 'DSP CHF-BALANCE',
      category: '',
      mutating: false,
      username: '',
      role: '',
      parameters: {},
      status: 'success',
      source: '',
      output: '',
      data: { supi: 'imsi-999700000000004', reserved_bytes: 32 },
      error: null,
      started_at: '2026-09-28T00:00:00Z',
      completed_at: '2026-09-28T00:00:00Z',
      duration_ms: 0,
    }
    const text = formatTelcoReport(result, 'ADD 5G-SUB: K="sensitive";')
    expect(text).toContain('+++    MAEstro 5G CORE OMC    2026-09-28 00:00:00')
    expect(text).toContain('RETCODE = 0  Operation Succeeded.')
    expect(text).toContain('reserved bytes                      : 32')
    expect(text).not.toContain('sensitive')
    expect(text).not.toContain('RM-REGISTERED')
    expect(
      formatTelcoReport(
        { ...result, status: 'failed', error: 'USER_UNKNOWN' },
        'DSP CHF-BALANCE;'
      )
    ).toContain('RETCODE = 1001  Operation Failed: USER_UNKNOWN.')
  })

  it('formats nested SMF PDU sessions with clean telco fields instead of JSON dumps', () => {
    const pduResult: OperationResult = {
      id: 'smf-pdu-test',
      scenario_id: '5g-sa',
      component_id: 'smf',
      component_label: 'SMF',
      testbed_id: '',
      operation_id: 'smf.pdu-info',
      operation_label: 'Sesiones PDU',
      category: 'SMF',
      mutating: false,
      username: 'operator',
      role: 'admin',
      parameters: {},
      status: 'success',
      source: '',
      output: '',
      data: {
        items: [
          {
            supi: 'imsi-999700000000001',
            ue_activity: 'active',
            pdu: [
              {
                psi: 1,
                dnn: 'internet',
                ipv4: '10.45.0.3',
                snssai: { sst: 1, sd: '000001' },
                qos_flows: [{ qfi: 1, '5qi': 9 }, { qfi: 3, '5qi': 2 }],
                n3: {
                  gnb: { teid: 9, addr: '[10.210.50.10]:2152' },
                  upf: { teid: 41909, addr: '[10.210.50.8]:2152', pdr_id: 2 },
                },
                pdu_state: 'active',
              },
            ],
          },
        ],
      },
      error: null,
      started_at: '2026-09-29T05:31:40Z',
      completed_at: '2026-09-29T05:31:40Z',
      duration_ms: 5,
    }

    const report = formatTelcoReport(pduResult, 'LST SMF-PDU-SESSION: SMF="SMF-01";')
    expect(report).toContain('pdu psi                             : 1')
    expect(report).toContain('pdu dnn                             : internet')
    expect(report).toContain('pdu ipv4                            : 10.45.0.3')
    expect(report).toContain('pdu snssai                          : SST=1, SD=000001')
    expect(report).toContain('pdu qos flows                       : QFI=1 (5QI=9), QFI=3 (5QI=2)')
    expect(report).toContain('pdu n3 gnb                          : [10.210.50.10]:2152 (TEID=9)')
    expect(report).toContain('pdu n3 upf                          : [10.210.50.8]:2152 (TEID=41909, PDR=2)')
    expect(report).toContain('pdu state                           : active')
    expect(report).not.toContain('{"psi":1')
  })
})
