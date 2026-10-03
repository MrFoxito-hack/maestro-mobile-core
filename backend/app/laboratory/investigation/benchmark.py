"""Opt-in local benchmark. Run only after acquisition, never from the model.

python -m app.laboratory.investigation.benchmark --experiment ID --dataset ID --username docente
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import statistics
import subprocess
import threading
import time
import uuid
import httpx
import psutil
from app.db import connection
from app.models import UserPublic
from app.laboratory.storage import repository
from app.laboratory.repository import stamp
from .model_client import MODEL, URL, ModelClient
from .service import Investigations, InvestigationCreate, SuggestRequest


def gpu():
    raw = subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total,memory.used,utilization.gpu,driver_version',
                          '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=3,
                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if raw.returncode: return None
    name, total, used, utilization, driver = [v.strip() for v in raw.stdout.splitlines()[0].split(',')]
    return {'name': name, 'total_mib': int(total), 'used_mib': int(used), 'gpu_percent': int(utilization), 'driver': driver}


def model_processes():
    values = {}
    for process in psutil.process_iter(['pid', 'name', 'cpu_times', 'memory_info']):
        try:
            if process.info['name'].lower().startswith('ollama'):
                cpu = process.info['cpu_times']
                values[str(process.pid)] = {'cpu_seconds': cpu.user+cpu.system,
                                           'rss_bytes': process.info['memory_info'].rss}
        except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
            pass
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiment', required=True)
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--username', required=True)
    parser.add_argument('--repetitions', type=int, default=3, choices=range(1, 6))
    args = parser.parse_args()
    store = repository(); store.initialize()
    with connection() as db:
        row = db.execute('SELECT username,role,testbed FROM users WHERE enabled=1 AND username=?', (args.username,)).fetchone()
        if not row: raise ValueError('Unknown active user')
        user = UserPublic(**dict(row))
    report = {'started_at': stamp(), 'model': MODEL, 'kind': 'development_benchmark',
              'hardware_before': gpu(), 'runs': [], 'samples': [], 'timer_lag_seconds': []}
    stop = threading.Event()
    def sample():
        psutil.cpu_percent()
        while not stop.is_set():
            memory = psutil.virtual_memory()
            report['samples'].append({'time': time.time(), 'gpu': gpu(), 'cpu_percent': psutil.cpu_percent(),
                                      'ram_available_bytes': memory.available, 'ram_used_bytes': memory.used,
                                      'model_processes': model_processes()})
            stop.wait(.5)
    sampling = threading.Thread(target=sample, daemon=True); sampling.start()
    # Warm only inside the same durable exclusion as a normal request. This
    # developer benchmark allows model loading longer than the UI's 10 s call.
    class WarmClient(ModelClient):
        warmed = False
        async def generate(self, context, question):
            if not self.warmed:
                started = time.perf_counter()
                async with httpx.AsyncClient(base_url=URL, verify=False, trust_env=False, timeout=60) as client:
                    response = await client.post('/api/generate', json={'model': MODEL, 'prompt': '',
                        'stream': False, 'keep_alive': '5m', 'options': {'num_ctx': 4096}})
                    response.raise_for_status()
                report['preload_seconds'] = time.perf_counter()-started
                self.warmed = True
            return await super().generate(context, question)
    service = Investigations(store, client=WarmClient(), enabled=True)
    key = 'benchmark-'+uuid.uuid4().hex
    inv = service.create(args.experiment, user, key, InvestigationCreate(dataset_id=args.dataset))
    report['investigation_id'] = inv['id']
    async def run():
        finished = asyncio.Event()
        async def ticker():
            while not finished.is_set():
                before = time.perf_counter()
                await asyncio.sleep(.05)
                report['timer_lag_seconds'].append(max(0, time.perf_counter()-before-.05))
        timer = asyncio.create_task(ticker())
        try:
            await asyncio.sleep(2)  # recorded idle-with-runtime baseline
            for index in range(args.repetitions):
                started = time.perf_counter()
                result = await service.suggest(inv['id'], user, key+'-'+str(index), SuggestRequest())
                report['runs'].append({'wall_seconds': time.perf_counter()-started, **result})
                if result.get('reason') == 'timeout': break  # preserve cooldown; no automatic replay
        finally:
            finished.set(); await timer
    try:
        asyncio.run(run())
    finally:
        stop.set(); sampling.join(timeout=5)
        report['finished_at'] = stamp()
        samples = report['samples']
        report['summary'] = {'valid_proposals': sum(r['status']=='completed' for r in report['runs']),
            'sampled_peak_vram_mib': max((s['gpu']['used_mib'] for s in samples if s['gpu']), default=None),
            'host_cpu_mean_percent': statistics.mean([s['cpu_percent'] for s in samples]) if samples else None,
            'host_cpu_peak_percent': max((s['cpu_percent'] for s in samples), default=None),
            'host_ram_available_min_bytes': min((s['ram_available_bytes'] for s in samples), default=None),
            'event_loop_max_lag_seconds': max(report['timer_lag_seconds'], default=None)}
        if len(samples) > 1:
            before, after = samples[0], samples[-1]
            delta = sum(max(0, v['cpu_seconds']-before['model_processes'].get(pid, {}).get('cpu_seconds', v['cpu_seconds']))
                        for pid, v in after['model_processes'].items())
            report['summary']['ollama_cpu_seconds_delta'] = delta
            report['summary']['ollama_cpu_mean_percent_of_host'] = 100*delta/(after['time']-before['time'])/psutil.cpu_count()
            report['summary']['ollama_peak_rss_bytes'] = max(sum(p['rss_bytes'] for p in s['model_processes'].values()) for s in samples)
        output = Path(__file__).resolve().parents[4]/'data/laboratory/benchmarks'/key
        output.mkdir(parents=True)
        (output/'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
        print(json.dumps({'path': str(output), 'summary': report['summary']}))

if __name__ == '__main__': main()
