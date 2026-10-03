import json
import pytest
from fastapi.testclient import TestClient
from jsonschema.exceptions import ValidationError
from pydantic import ValidationError as ModelError
from app.core.contract import Contract, INFO, EVENTS
from app.main import create_app
from app.models import SliceLoad
from tools.fetch_contract import verify


def test_pinned_contract_and_exact_operation_names():
    assert len(verify()) == 4
    c = Contract()
    doc = c.documents[INFO]
    # Official V16.7.0 PDF Annex A.3 p97 and its ZIP both retain this older
    # externalDocs string. Preserve it; provenance is established by artifact hash.
    assert 'V16.5.0' in doc['externalDocs']['description']
    assert doc['info']['version'] == '1.1.1'
    assert 'V16.7.0' in c.documents[EVENTS]['externalDocs']['description']
    names = [p['name'] for p in doc['paths']['/analytics']['get']['parameters']]
    assert 'event-id' in names and 'analytics-id' not in names
    assert '/nnwdaf-eventssubscription/v1' in c.documents[EVENTS]['servers'][0]['url']


def test_pydantic_output_matches_official_schema():
    load = SliceLoad(loadLevelInformation=85, snssais=[{'sst': 1, 'sd': '000001'}])
    c = Contract()
    c.validate('AnalyticsData', {'sliceLoadLevelInfos': [load.model_dump(exclude_none=True)]})
    c.validate('AnalyticsData', {'svcExps': [{'svcExprc': {'mos': 4.2}}]})
    with pytest.raises(ValidationError):
        c.validate('AnalyticsData', {'sliceLoadLevelInfos': [{'loadLevelInformation': '85'}]})
    with pytest.raises(ModelError): SliceLoad(loadLevelInformation=101, snssais=[{'sst': 1}])


def test_minimal_subscription_contract_only():
    Contract().validate('NnwdafEventsSubscription', {
        'eventSubscriptions': [{'event': 'SLICE_LOAD_LEVEL', 'notificationMethod': 'THRESHOLD',
                                'snssais': [{'sst': 1}], 'loadLevelThreshold': 85}],
        'notificationURI': 'http://127.0.0.7:7777/callback',
    }, file=EVENTS)


def test_api_auth_missing_data_and_unsupported_semantics(tmp_path):
    token = 'test-only-token-123456789012345'
    with TestClient(create_app(tmp_path/'test.sqlite3', token)) as client:
        assert client.get('/health').json()['closed_loop_enabled'] is False
        route = '/nnwdaf-analyticsinfo/v1/analytics'
        assert client.get(route).status_code == 401
        headers = {'Authorization': f'Bearer {token}'}
        assert client.get(route, headers=headers, params={'event-id': 'LOAD_LEVEL_INFORMATION',
            'event-filter': json.dumps({'snssais': [{'sst': 1}]})}).status_code == 204
        assert client.get(route, headers=headers, params={'analytics-id': 'SLICE_LOAD_LEVEL'}).status_code == 400
        assert client.post('/management/v1/abnormal-behaviour', headers=headers,
                          json={'history': [], 'value': 1, 'timestamp': 10}).json()['status'] == 'insufficient_data'
