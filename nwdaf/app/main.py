"""Phase-1 service: local research API + read-only SBI analytics retrieval.

Subscription delivery and automatic PCF actuation are deliberately not advertised
until tested with the native consumer. Binding defaults to loopback only.
"""
from contextlib import asynccontextmanager
import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import secrets

from fastapi import FastAPI, Request, Depends
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from app.core.contract import Contract, EVENTS
from app.core.database import Database
from app.core.decision_ledger import DecisionLedger
from app.models import (SliceRequest, SliceLoad, AnomalyRequest, Snssai, Subscription,
                        TargetedAnomalyRequest, ExperienceRequest, DecisionEvent)
from app.subscriptions import Subscriptions, BASE
from app.ingestion.scheduler import IngestionScheduler, SliceCapacityMap, AnomalyWatch
from app.engine.slice_load import forecast
from app.engine.abnormal_behaviour import detect
from app.engine.service_experience import estimate

log = logging.getLogger(__name__)


def create_app(database_path=None, token=None, callback_uris=None, pm_path=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.contract = Contract()
        app.state.db = Database(database_path or os.getenv('NWDAF_DATABASE', 'data/nwdaf.sqlite3'))
        app.state.ledger = DecisionLedger(app.state.db)
        app.state.live_observations = {}
        app.state.token = token or os.environ['NWDAF_TOKEN']
        if len(app.state.token) < 24:
            raise RuntimeError('NWDAF_TOKEN must contain at least 24 characters')
        allowed = callback_uris if callback_uris is not None else json.loads(os.getenv('NWDAF_CALLBACK_URIS', '[]'))
        if not isinstance(allowed, (list, tuple)) or not all(isinstance(uri, str) for uri in allowed):
            raise ValueError('NWDAF_CALLBACK_URIS must be a JSON array of exact callback URIs')
        app.state.subscriptions = Subscriptions(app.state.db, app.state.contract, allowed)
        worker = asyncio.create_task(app.state.subscriptions.run())
        app.state.notification_worker = worker
        # Periodic PM ingestion scheduler
        resolved_pm = pm_path or os.getenv('NWDAF_PM_DATABASE')
        scheduler = IngestionScheduler(
            app.state.db, app.state.contract, resolved_pm,
            slice_maps=[SliceCapacityMap(**entry) for entry in
                        json.loads(os.getenv('NWDAF_SLICE_MAPS', '[]'))],
            anomaly_watches=[AnomalyWatch(**entry) for entry in
                             json.loads(os.getenv('NWDAF_ANOMALY_WATCHES', '[]'))],
            cycle_seconds=int(os.getenv('NWDAF_INGESTION_CYCLE', '300')),
        )
        app.state.ingestion = scheduler
        ingestion_task = asyncio.create_task(scheduler.run())
        app.state.ingestion_worker = ingestion_task
        log.info("NWDAF started: notifications=%s ingestion=%s",
                 'active', 'active' if resolved_pm else 'no-source')
        try:
            yield
        finally:
            ingestion_task.cancel()
            worker.cancel()
            for task in (ingestion_task, worker):
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    app = FastAPI(title='MAEstro NWDAF research service', version='0.1.0', lifespan=lifespan)

    @app.exception_handler(ValueError)
    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return JSONResponse({'status': 400, 'title': 'Invalid analytics input', 'cause': 'INVALID_REQUEST'},
                            status_code=400, media_type='application/problem+json')

    async def authorized(request: Request):
        supplied = request.headers.get('authorization', '')
        if not secrets.compare_digest(supplied, 'Bearer ' + request.app.state.token):
            from fastapi import HTTPException
            raise HTTPException(401, 'Authentication required')

    @app.get('/health')
    def health():
        degraded = app.state.notification_worker.done() or app.state.ingestion_worker.done()
        if degraded:
            return JSONResponse({'status': 'degraded',
                                 'notification_worker': 'stopped' if app.state.notification_worker.done() else 'active',
                                 'ingestion_worker': 'stopped' if app.state.ingestion_worker.done() else 'active'},
                                status_code=503)
        return {'status': 'ready', 'contract': 'TS 29.520 V16.7.0',
                'profile': 'phase-1-research', 'closed_loop_enabled': os.getenv('NWDAF_CLOSED_LOOP_ENABLED') == '1',
                'ingestion': app.state.ingestion.status,
                'slice_maps': [{'object_id': item.object_id, 'snssai': item.snssai}
                               for item in app.state.ingestion.slice_maps]}

    @app.exception_handler(LookupError)
    async def missing(request, exc):
        return JSONResponse({'status': 404, 'title': 'Subscription not found'}, status_code=404)

    @app.exception_handler(OverflowError)
    async def capacity(request, exc):
        return JSONResponse({'status': 429, 'title': 'Subscription capacity reached'}, status_code=429)

    @app.post(BASE, dependencies=[Depends(authorized)], status_code=201)
    def subscribe(body: Subscription):
        identifier, representation = app.state.subscriptions.save(body)
        return JSONResponse(representation, status_code=201, headers={'Location': BASE+'/'+identifier})

    @app.put(BASE+'/{identifier}', dependencies=[Depends(authorized)])
    def replace_subscription(identifier: str, body: Subscription):
        return app.state.subscriptions.save(body, identifier)[1]

    @app.delete(BASE+'/{identifier}', dependencies=[Depends(authorized)], status_code=204)
    def unsubscribe(identifier: str):
        app.state.subscriptions.delete(identifier)
        return Response(status_code=204)

    @app.post('/management/v1/slice-forecast', dependencies=[Depends(authorized)])
    async def slice_forecast(body: SliceRequest):
        # 30-minute source buckets cannot support a 15-minute forecast without
        # interpolation. Do not invent that extra temporal resolution.
        horizons = (900, 1800) if body.interval_seconds == 300 else (1800,)
        result = await run_in_threadpool(forecast, body.values, body.timestamps,
                                        interval_seconds=body.interval_seconds,
                                        period=body.period, horizons=horizons)
        snssai = body.snssai.model_dump(exclude_none=True)
        result['history'] = [{'timestamp': t, 'value': v} for t, v in zip(body.timestamps, body.values)]
        # SBI below exposes latest *observed* load. Prediction bands and horizons
        # stay in research evidence until analytics reporting requirements are implemented.
        load = SliceLoad(loadLevelInformation=round(body.values[-1]), snssais=[body.snssai])
        payload = {'timeStampGen': datetime.now(timezone.utc).isoformat(),
                   'expiry': datetime.fromtimestamp(body.timestamps[-1]+300, timezone.utc).isoformat(),
                   'sliceLoadLevelInfos': [load.model_dump(exclude_none=True)]}
        app.state.contract.validate('AnalyticsData', payload)
        age = datetime.now(timezone.utc).timestamp()-body.timestamps[-1]
        result['input_hash'] = app.state.db.record('LOAD_LEVEL_INFORMATION',
            json.dumps(snssai, sort_keys=True), body.model_dump(), result,
            payload if 0 <= age <= 300 else None)
        return result

    @app.post('/management/v1/pm-observations', dependencies=[Depends(authorized)])
    def live_pm(body: dict):
        from app.ingestion.live_pm import observation
        now = datetime.now(timezone.utc)
        mapping, evidence = observation(body, app.state.ingestion.slice_maps, now.timestamp())
        target = json.dumps(mapping.snssai, sort_keys=True)
        previous = app.state.live_observations.get(target)
        stamp = body['after']['timestamp']
        if previous and stamp <= previous[0]:
            raise ValueError('Out-of-order observation')
        payload = {'timeStampGen': now.isoformat(),
                   'expiry': datetime.fromtimestamp(stamp+5, timezone.utc).isoformat(),
                   'sliceLoadLevelInfos': [{'snssais': [mapping.snssai],
                       'loadLevelInformation': round(min(100, evidence['observed_percentage_unclipped']))}]}
        app.state.contract.validate('AnalyticsData', payload)
        digest = app.state.db.record('LOAD_LEVEL_INFORMATION', target, body, evidence, payload)
        app.state.live_observations[target] = (stamp, payload)
        return {'input_hash': digest, **evidence}

    @app.post('/management/v1/upf-diagnostics', dependencies=[Depends(authorized)])
    def upf_diagnostics(body: dict):
        from app.engine.upf_diagnostics import assess
        # Capacity/SLOs are operator configuration, never publisher-provided values.
        slos = json.loads(os.getenv('NWDAF_SERVICE_SLOS', '{}'))
        result = assess(body, slos, datetime.now(timezone.utc).timestamp())
        result['input_hash'] = app.state.db.record('UPF_SERVICE_DIAGNOSTICS',
            json.dumps(body['snssai'],sort_keys=True),body,result)
        return result

    @app.post('/management/v1/abnormal-behaviour', dependencies=[Depends(authorized)])
    async def abnormal(body: AnomalyRequest):
        result = detect(body.history, body.value, body.timestamp, scale_floor=body.scale_floor)
        app.state.db.record('ABNORMAL_BEHAVIOUR', 'research', body.model_dump(), result)
        return result

    @app.post('/management/v1/service-experience', dependencies=[Depends(authorized)])
    async def experience(body: dict):
        try:
            result = await run_in_threadpool(estimate, body)
        except ImportError:
            return JSONResponse({'status': 503, 'title': 'P.1203 research dependency unavailable'}, status_code=503)
        app.state.db.record('SERVICE_EXPERIENCE', 'research', body, result)
        return result

    @app.get('/nnwdaf-analyticsinfo/v1/analytics', dependencies=[Depends(authorized)])
    def analytics(request: Request):
        params = request.query_params
        if set(params) - {'event-id', 'event-filter', 'tgt-ue'} or any(len(params.getlist(key)) != 1 for key in params):
            # Do not silently ignore unsupported ana-req/tgt-ue/filter semantics.
            raise ValueError('Unsupported query requirements')
        event = params.get('event-id')
        if event not in ('LOAD_LEVEL_INFORMATION', 'ABNORMAL_BEHAVIOUR', 'SERVICE_EXPERIENCE'):
            return JSONResponse({'status': 400, 'cause': 'UNSUPPORTED_ANALYTICS',
                                 'title': 'Analytics ID not exposed in this phase'}, status_code=400)
        filt = json.loads(params.get('event-filter', '{}'))
        if not isinstance(filt, dict): raise ValueError('Invalid filter')
        if event == 'LOAD_LEVEL_INFORMATION':
            if 'tgt-ue' in params or set(filt) != {'snssais'} or not isinstance(filt['snssais'], list) or len(filt['snssais']) != 1:
                raise ValueError('Exactly one slice target is required in this profile')
            snssai = Snssai.model_validate(filt['snssais'][0]).model_dump(exclude_none=True)
            app.state.contract.validate('EventFilter', filt)
            target = json.dumps(snssai, sort_keys=True)
        else:
            ue = json.loads(params.get('tgt-ue', '{}'))
            if not isinstance(ue, dict) or set(ue) != {'supis'} or not isinstance(ue['supis'], list) or len(ue['supis']) != 1:
                raise ValueError('Exactly one SUPI is required')
            app.state.contract.validate('TargetUeInformation', ue, file=EVENTS)
            target = ue['supis'][0]
            if event == 'SERVICE_EXPERIENCE':
                if set(filt) != {'appIds'} or not isinstance(filt['appIds'], list) or len(filt['appIds']) != 1:
                    raise ValueError('Exactly one application is required')
                app.state.contract.validate('EventFilter', filt)
                target = json.dumps([target, filt['appIds'][0]])
            elif filt:
                raise ValueError('Unsupported abnormal-behaviour filter')
        live = app.state.live_observations.get(target) if event == 'LOAD_LEVEL_INFORMATION' else None
        result = live[1] if live and datetime.fromisoformat(live[1]['expiry']) > datetime.now(timezone.utc) else app.state.db.latest(event, target)
        if result is None:
            return Response(status_code=204)
        if datetime.fromisoformat(result['expiry']) <= datetime.now(timezone.utc):
            return Response(status_code=204)
        app.state.contract.validate('AnalyticsData', result)
        return result

    def publish(event, target, inputs, evidence, timestamp, report):
        now = datetime.now(timezone.utc)
        payload = {'timeStampGen': now.isoformat(),
                   'expiry': datetime.fromtimestamp(timestamp+300, timezone.utc).isoformat(), **report}
        app.state.contract.validate('AnalyticsData', payload)
        app.state.db.record(event, target, inputs, evidence,
                            payload if report and 0 <= now.timestamp()-timestamp <= 300 else None)

    @app.post('/management/v1/publish/abnormal-behaviour', dependencies=[Depends(authorized)])
    async def publish_abnormal(body: TargetedAnomalyRequest):
        result = detect(body.history, body.value, body.timestamp, scale_floor=body.scale_floor)
        report = {}
        # Statistical excursions are not proof of attacks. Only positive rate
        # excursions support these exception types; no confidence is fabricated.
        if result.get('anomaly') and result.get('score', 0) > 0:
            exception = 'TOO_FREQUENT_SERVICE_ACCESS' if body.metric == 'service_access_rate' else 'UNEXPECTED_LARGE_RATE_FLOW'
            report = {'abnorBehavrs': [{'supis': [body.supi], 'excep': {'excepId': exception}}]}
        publish('ABNORMAL_BEHAVIOUR', body.supi, body.model_dump(), result, body.timestamp, report)
        return result

    @app.post('/management/v1/publish/service-experience', dependencies=[Depends(authorized)])
    async def publish_experience(body: ExperienceRequest):
        try:
            result = await run_in_threadpool(estimate, body.document)
        except ImportError:
            return JSONResponse({'status': 503, 'title': 'P.1203 research dependency unavailable'}, status_code=503)
        report = {'svcExps': [{'supis': [body.supi], 'appId': body.app_id, 'svcExprc': {'mos': result['mos']}}]}
        publish('SERVICE_EXPERIENCE', json.dumps([body.supi, body.app_id]), body.model_dump(), result, body.timestamp, report)
        return result

    @app.get('/management/v1/predictions', dependencies=[Depends(authorized)])
    def predictions():
        # Research evidence is separate from normative SBI observation fields.
        from app.forecast_view import predictions as prediction_view
        items = prediction_view(app.state.db)
        return {'items':items, 'closed_loop_enabled':os.getenv('NWDAF_CLOSED_LOOP_ENABLED') == '1'}

    @app.post('/management/v1/closed-loop/events', dependencies=[Depends(authorized)], status_code=201)
    def record_decision(body: DecisionEvent):
        return {'id':app.state.ledger.append(body)}

    @app.get('/management/v1/closed-loop/history', dependencies=[Depends(authorized)])
    def decision_history():
        return app.state.ledger.list()

    return app


app = create_app()
