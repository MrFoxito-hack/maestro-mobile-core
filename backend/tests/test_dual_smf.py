import asyncio
import pytest

from app.services.execution import RemoteExecutionAdapter, ExecutionError, _validate_info_endpoint
from app.services.operations import operations_for
from app.services.scenarios import CATALOG
from app.services.trace_analysis import _parse_sbi_call, ADDRESS_TO_NF


def test_separate_smf_inventory_and_operation():
    components = {c['id']: c for c in CATALOG['5g-sa']['components']}
    assert components['upf']['depends_on'] == ['smf']
    assert components['upf2']['depends_on'] == ['smf2']
    assert components['smf2']['unit'] == 'open5gs-smfd2'
    assert any(op.id == 'smf2.pdu-info' for op in operations_for(components['smf2']))
    assert not any(op.id == 'smf.pdu-info' for op in operations_for(components['smf2']))
    assert 'smf2' not in {c['id'] for c in CATALOG['4g-epc']['components']}


def test_smf2_remote_info_does_not_query_smf1(monkeypatch):
    adapter = object.__new__(RemoteExecutionAdapter)
    commands = []
    async def run(command, **kwargs):
        commands.append(command)
        return '{"items":[]}'
    monkeypatch.setattr(adapter, '_run', run)
    result = asyncio.run(adapter.native_operation('open5gs-info', 'smf2', {'endpoint': 'pdu-info'}))
    assert result['data'] == {'items': []}
    assert '127.0.0.15:9091/pdu-info' in commands[0]
    assert '127.0.0.4' not in commands[0]
    with pytest.raises(ExecutionError):
        _validate_info_endpoint('smf2', 'ue-info')


def test_dual_smf_trace_attribution():
    assert ADDRESS_TO_NF['127.0.0.15'] == 'SMF-02'
    assert ADDRESS_TO_NF['127.0.0.16'] == 'BSF'
    row = {'http2.headers.path': '/nsmf-pdusession/v1/sm-contexts', 'http2.headers.method': 'POST'}
    event = _parse_sbi_call(row, '127.0.0.1', '127.0.0.15')
    assert event[:2] == ('AMF', 'SMF-02')
    row['http2.headers.path'] = '/nnssf-nsselection/v2/network-slice-information'
    event = _parse_sbi_call(row, '127.0.0.1', '127.0.0.14')
    assert event[:2] == ('AMF', 'NSSF')
    assert event[3] == 'N22 / SBI'
