from datetime import datetime, timezone
import json
import numpy as np
from fastapi.testclient import TestClient
from app.main import create_app
from tests.test_engines import av_input


def test_forecast_publication_retains_slice_and_expires(tmp_path):
    token = 'publication-test-token-1234567890'
    with TestClient(create_app(tmp_path/'nwdaf.sqlite3', token)) as client:
        headers = {'Authorization': 'Bearer '+token}
        now = datetime.now(timezone.utc).timestamp()
        values = (40 + .1*np.arange(60) + 5*np.sin(2*np.pi*np.arange(60)/12)).tolist()
        body = {'snssai': {'sst': 1, 'sd': '000001'}, 'values': values,
                'timestamps': (now-5-np.arange(59,-1,-1)*300).tolist(),
                'source': 'controlled-test-fixture', 'capacity_definition': 'fixture-only utilization'}
        response = client.post('/management/v1/slice-forecast', headers=headers, json=body)
        assert response.status_code == 200, response.text
        assert len(response.json()['points']) == 2
        evidence = client.get('/management/v1/predictions', headers=headers)
        assert evidence.status_code == 200
        assert evidence.json()['items'][0]['evidence']['history'][-1]['value'] == values[-1]
        assert evidence.json()['items'][0]['fresh'] is True
        assert client.get('/management/v1/predictions').status_code == 401
        params = {'event-id':'LOAD_LEVEL_INFORMATION', 'event-filter':json.dumps({'snssais':[body['snssai']]})}
        route = '/nnwdaf-analyticsinfo/v1/analytics'
        response = client.get(route, headers=headers, params=params)
        assert response.status_code == 200, response.text
        assert response.json()['sliceLoadLevelInfos'][0]['loadLevelInformation'] == round(values[-1])
        other = {**params, 'event-filter':json.dumps({'snssais':[{'sst':1,'sd':'000002'}]})}
        assert client.get(route, headers=headers, params=other).status_code == 204
        # Historical data is legitimate research input but not a fresh SBI report.
        body['timestamps'] = [x-86400 for x in body['timestamps']]
        assert client.post('/management/v1/slice-forecast', headers=headers, json=body).status_code == 200
        assert client.get(route, headers=headers, params=params).status_code == 204


def test_bad_filter_and_unsupported_requirements_are_not_ignored(tmp_path):
    token = 'filter-test-token-123456789012345'
    with TestClient(create_app(tmp_path/'nwdaf.sqlite3', token)) as client:
        headers = {'Authorization': 'Bearer '+token}
        for filt in ['null','[]','{"snssais":null}','not-json']:
            assert client.get('/nnwdaf-analyticsinfo/v1/analytics', headers=headers,
                params={'event-id':'LOAD_LEVEL_INFORMATION','event-filter':filt}).status_code == 400


def test_targeted_anomaly_and_experience_publication(tmp_path):
    token = 'targeted-analytics-token-1234567890'
    with TestClient(create_app(tmp_path/'targeted.db', token)) as client:
        headers = {'Authorization': 'Bearer '+token}
        now = datetime.now(timezone.utc).timestamp()-1
        supi = 'imsi-999700000000001'
        abnormal = {'supi':supi, 'metric':'service_access_rate', 'source':'controlled-fixture',
                    'history':[[now-i*60, 10] for i in range(60,0,-1)], 'value':100, 'timestamp':now}
        path = '/management/v1/publish/abnormal-behaviour'
        response = client.post(path, headers=headers, json=abnormal)
        assert response.status_code == 200, response.text
        params = {'event-id':'ABNORMAL_BEHAVIOUR', 'tgt-ue':json.dumps({'supis':[supi]})}
        route = '/nnwdaf-analyticsinfo/v1/analytics'
        response = client.get(route, headers=headers, params=params)
        assert response.status_code == 200, response.text
        assert response.json()['abnorBehavrs'][0]['excep']['excepId'] == 'TOO_FREQUENT_SERVICE_ACCESS'
        abnormal['value'] = 10
        assert client.post(path, headers=headers, json=abnormal).status_code == 200
        assert client.get(route, headers=headers, params=params).status_code == 204
        experience = {'supi':supi, 'app_id':'stream5g', 'timestamp':now, 'document':av_input()}
        response = client.post('/management/v1/publish/service-experience', headers=headers, json=experience)
        assert response.status_code == 200, response.text
        params = {'event-id':'SERVICE_EXPERIENCE', 'tgt-ue':json.dumps({'supis':[supi]}),
                  'event-filter':json.dumps({'appIds':['stream5g']})}
        response = client.get(route, headers=headers, params=params)
        assert response.status_code == 200, response.text
        assert 1 <= response.json()['svcExps'][0]['svcExprc']['mos'] <= 5
        params['tgt-ue'] = json.dumps({'supis':['imsi-999700000000002']})
        assert client.get(route, headers=headers, params=params).status_code == 204
