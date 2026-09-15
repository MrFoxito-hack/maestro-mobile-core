from pathlib import Path

import jsonschema
import yaml
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT4

from conftest import ROOT, charging_request


def validator(type_name):
    root = Path(__file__).resolve().parents[2] / '.work' / 'charging-contract'
    registry = Registry()
    for name in ('TS32291_Nchf_ConvergedCharging.yaml', 'TS29571_CommonData.yaml'):
        path = root / name
        assert path.exists(), 'Run python tools/fetch_contract.py before contract tests'
        document = yaml.safe_load(path.read_text(encoding='utf-8'))
        registry = registry.with_resource('https://contract.invalid/' + name,
                                         Resource.from_contents(document, default_specification=DRAFT4))
    schema = {'$ref': 'https://contract.invalid/TS32291_Nchf_ConvergedCharging.yaml#/components/schemas/' + type_name}
    return jsonschema.Draft4Validator(schema, registry=registry, format_checker=jsonschema.FormatChecker())


def test_request_and_actual_responses_match_official_release16(client, account):
    payload = charging_request(1)
    validator('ChargingDataRequest').validate(payload)
    result = client.post(ROOT, json=payload)
    assert result.status_code == 201
    validator('ChargingDataResponse').validate(result.json())
    path = result.headers['location']
    payload = charging_request(2, used=500)
    validator('ChargingDataRequest').validate(payload)
    result = client.post(path + '/update', json=payload)
    assert result.status_code == 200
    validator('ChargingDataResponse').validate(result.json())
    payload = charging_request(3, used=500, requested=0)
    validator('ChargingDataRequest').validate(payload)
    result = client.post(path + '/release', json=payload)
    assert result.status_code == 204 and result.content == b''
