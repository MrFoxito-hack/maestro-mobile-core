"""Explicitly assigned, remotely guarded operational QoE pilot.

Service/auxiliary recovery is not effective policy recovery. Scientific validity
stays inconclusive until the independent policy baseline is verified.
"""
import hashlib
import json
from pathlib import Path
import re

import yaml

from app.core.config import get_settings
from app.laboratory.adapters import Cancelled
from app.laboratory.acceptance import RealPreflightBlocked, require_real_actuator
from app.laboratory.analysis import Dataset, Trial, analyze
from app.laboratory.live import LivePreflight
from app.laboratory.qoe_metrics import p1203_document, score_p1203_locally
from app.laboratory.repository import canonical, stamp

ROOT = Path(__file__).resolve().parents[3]


class QoEClosedLoopAdapter:
    def __init__(self, runtime, *, collector_factory=LivePreflight, session_factory=None, root=None):
        self.runtime = runtime
        self.collector_factory = collector_factory
        self.session_factory = session_factory
        self.root = Path(root) if root else ROOT / 'data/laboratory/runs'
        self.generators = {}
        self.phases = {}
        self.row = None
        self.recovering = False

    def directory(self, context):
        return self.root / context['execution_id']

    def write(self, context, name, document):
        directory = self.directory(context)
        directory.mkdir(parents=True, exist_ok=True)
        temp = directory / (name + '.tmp')
        temp.write_text(canonical(document), encoding='utf-8')
        temp.replace(directory / name)

    def save(self, context):
        self.runtime.save_real(self.row['id'], self.row['token'], context=context)

    def check(self):
        cancelled = self.runtime.heartbeat(self.row['id'], self.row['token'])
        if cancelled and not self.recovering:
            raise Cancelled()

    def remote_owner(self):
        with self.runtime.repository.connect() as db:
            row=self.runtime.fenced(db,self.row['id'],self.row['token'])
            grant=db.execute('SELECT mode FROM lab_assignments WHERE id=?',(row['assignment_id'],)).fetchone()
            if not grant or grant['mode']!='real': raise RealPreflightBlocked('real_assignment_required')
            generation=db.execute("SELECT MAX(id) FROM lab_journal WHERE execution_id=? AND token=? AND event IN ('claimed','recovery_claimed')",(row['id'],row['token'])).fetchone()[0]
        if not generation: raise RealPreflightBlocked('owner_generation_missing')
        return {'execution_id':row['id'],'token':row['token'],'generation':generation}

    def validate(self, context):
        collector = self.collector_factory()
        preflight = collector.collect(context['bindings'], 22_000_001)
        context['preflight'] = preflight
        self.write(context, 'preflight.json', preflight)
        self.write(context, 'preflight-raw.json', collector.raw)
        self.save(context)
        subjects = preflight.get('subjects', {})
        for role in ('observed', 'competing'):
            item = subjects.get(role, {})
            if item.get('unreserved_bytes') is None or item['unreserved_bytes'] <= 22_000_000:
                raise RealPreflightBlocked('insufficient_chf_quota')
            session = item.get('session')
            if not session:
                raise RealPreflightBlocked('active_internet_pdu_required')
            supi = context['bindings'][role]['supi']
            pdu = yaml.safe_load(collector.raw['ue']['subjects'][supi])[session['pdu_session']]
            if pdu.get('s-nssai') != {'sst': 1, 'sd': 1}:
                raise RealPreflightBlocked('internet_slice_not_verified')
        if subjects['observed']['session']['interface'] != 'uesimtun0':
            raise RealPreflightBlocked('observed_tunnel_changed')
        if subjects['competing']['unreserved_bytes'] < 55_000_000:
            raise RealPreflightBlocked('campaign_competing_budget_insufficient')
        if preflight['errors'] or any(c['status'] != 'passed' for c in preflight['checks']):
            raise RealPreflightBlocked('live_preflight_failed')
        from app.laboratory.real_readiness import PCF_INSPECT
        pcf = collector.transport.read('core', PCF_INSPECT, privileged=True)
        self.write(context, 'pcf-mode.json', pcf)
        if pcf.get('status') != 'success' or pcf.get('mode') != 'AUTONOMOUS':
            raise RealPreflightBlocked('pcf_autonomous_mode_required')
        # Check all VMs and fixed auxiliary ports before any real side effect.
        from app.laboratory.qoe_transport import Lab
        settings = get_settings()
        core = Lab(settings, settings.ssh_port, self.check)
        try:
            internet=[]
            for endpoint in ('http://127.0.0.4:9090/pdu-info?page=0&page_size=100','http://127.0.0.15:9091/pdu-info?page=0&page_size=100'):
                data=json.loads(core.run(['curl','--silent','--fail','--max-time','4',endpoint]))
                if len(data.get('items',[]))>=100:raise RealPreflightBlocked('core_session_inventory_truncated')
                internet += [(u['supi'],p) for u in data.get('items',[]) for p in u.get('pdu',[]) if p.get('dnn')=='internet']
            allowed={context['bindings'][r]['supi'] for r in ('observed','competing')}
            if any(supi not in allowed for supi,_ in internet):raise RealPreflightBlocked('unassigned_internet_session_present')
            for role in ('observed','competing'):
                matches=[p for supi,p in internet if supi==context['bindings'][role]['supi']]
                if (len(matches)!=1 or matches[0].get('pdu_state')!='active'
                    or matches[0].get('ipv4')!=subjects[role]['session']['address']):
                    raise RealPreflightBlocked('core_pdu_binding_unverified')
            context['core_sessions_verified']=True
            for port in ('5209', '5210', '18091'):
                if core.run(['ss', '-H', '-lntup', 'sport = :' + port]).strip():
                    raise RealPreflightBlocked('test_port_in_use')
        finally:
            core.client.close()
        self.remote_owner()
        context['admission']={'mode':'real','live_preflight_passed':True,'protocol':'qoe_operational_pilot_v1'}
        self.save(context)
        require_real_actuator(context)
        return {'preflight': 'passed', 'quota_floor_bytes': 22_000_000}

    def prepare(self, context):
        require_real_actuator(context)
        from app.laboratory.qoe_session import session
        ordinal = context['current_run']['ordinal']
        trial = {'ordinal': ordinal, 'tag': 'labqoe-' + self.row['id'][:16] + '-r' + str(ordinal),
                 'case': 'baseline' if 'disabled' in context['current_run']['treatment'] else 'closed-loop',
                 'run': context['current_run'], 'intent_at': stamp()}
        context.setdefault('trials', {})[str(ordinal)] = trial
        self.save(context)  # Preparation may have an unknown outcome after a crash.
        path = self.directory(context) / ('trial-' + str(ordinal))
        path.mkdir(exist_ok=False)
        def checkpoint(result):
            trial['remote'] = json.loads(canonical(result))
            self.save(context)
        generator = (self.session_factory or session)(get_settings(), trial['tag'], path, trial['case'], checkpoint, self.check, self.row['owner'],
                                                    admission=context, remote_owner=self.remote_owner())
        self.generators[(self.row['id'], ordinal)] = generator
        progress = next(generator)
        if progress['phase'] != 'prepared':
            raise RuntimeError('Unexpected preparation phase')
        self.phases[(self.row['id'], ordinal)] = 'prepared'
        media = progress['result'].get('media_sha256')
        for other in context['trials'].values():
            previous = other.get('remote', {}).get('media_sha256')
            if other['ordinal'] != ordinal and previous and media != previous:
                raise ValueError('paired_media_not_identical')
        return {'prepared': True}

    def apply(self, treatment, context):
        require_real_actuator(context)
        allowed = ('controller_disabled', 'controller_enabled', 'controller_disabled_verified', 'controller_enabled_verified')
        if treatment not in allowed:
            raise ValueError('unknown_treatment')
        generator = self.generators[(self.row['id'], context['current_run']['ordinal'])]
        progress = next(generator)
        if progress['phase'] != 'played':
            raise RuntimeError('Playback did not finish')
        self.phases[(self.row['id'], context['current_run']['ordinal'])] = 'played'
        return {'player_finished': True, 'treatment': treatment}

    def compensate(self, context):
        self.recovering = True
        try:
            for trial in context.get('trials', {}).values():
                if trial.get('recovered'): continue
                key = (self.row['id'], trial['ordinal'])
                generator = self.generators.pop(key, None)
                if generator is not None:
                    try:
                        # Normal completion runs the accepted recovery path and persists results.
                        if self.phases.get(key) == 'played':
                            next(generator)
                    except StopIteration:
                        pass
                    finally:
                        generator.close()
                        self.phases.pop(key, None)
                self.recover_remote(trial)
            return {'compensation_attempted': True, 'scope': 'nwdaf_operational_regime'}
        finally:
            self.recovering = False

    def recover_remote(self, trial):
        """Also usable after a worker restart; exact owned paths survive in SQLite."""
        from app.laboratory.qoe_transport import Lab
        settings = get_settings()
        remote = trial.get('remote', {})
        for node, field, port in (('core', 'remote', settings.ssh_port), ('ue', 'uremote', settings.ue_ssh_port)):
            path = remote.get(field)
            if not path:
                continue
            if not re.fullmatch('/home/emsadmin/' + re.escape(trial['tag']) + '-[A-Za-z0-9]{6}', path):
                raise ValueError('invalid_owned_remote_directory')
            host = Lab(settings, port, self.check)
            try:
                host.bind_owner(self.remote_owner(),recovery=True)
                # The cleanup file is created before any route, listener or policy action.
                script = "import pathlib,subprocess,sys; p=pathlib.Path(sys.argv[1])/'cleanup.sh'; subprocess.run(['/bin/sh',str(p)],check=False,timeout=20) if p.exists() else None"
                host.run(['python3', '-c', script, path], sudo=True, timeout=25)
                if node == 'core':
                    host.run(['systemctl', 'start', 'maestro-nwdaf'], sudo=True)
                # Leave cleanup timers armed until verify_recovery observes clean state.
            finally:
                host.client.close()

    def verify_recovery(self, context):
        from app.laboratory.qoe_transport import Lab
        report = {'scope': 'nwdaf_operational_regime_and_owned_auxiliaries',
                  'bit_exact_policy_snapshot': False, 'verified': True, 'trials': [],
                  'policy_recovery_verified': False}
        self.recovering = True
        try:
            for trial in context.get('trials', {}).values():
                if trial.get('recovered'):
                    report['trials'].append({'ordinal':trial['ordinal'],'checks':{'previous_owned_cleanup_verified':True}})
                    continue
                settings = get_settings()
                hosts = {}
                try:
                    for name, port in (('core', settings.ssh_port), ('ue', settings.ue_ssh_port)):
                        hosts[name] = Lab(settings, port, self.check)
                        if context.get('admission'):
                            hosts[name].bind_owner(self.remote_owner(),recovery=True)
                    core, ue = hosts['core'], hosts['ue']
                    remote = trial.get('remote', {})
                    checks = {'nwdaf_active': core.run(['systemctl', 'is-active', 'maestro-nwdaf']).strip() == 'active'}
                    checks['listeners_absent'] = all(not core.run(['ss', '-H', '-lntup', 'sport = :' + p]).strip() for p in ('5209', '5210', '18091'))
                    checks['load_inactive'] = ue.run(['systemctl', 'is-active', trial['tag'] + '-load'], check=False).strip() not in ('active', 'activating', 'deactivating')
                    checks['units_inactive'] = all(core.run(['systemctl', 'is-active', trial['tag'] + '-' + suffix], check=False).strip() not in ('active', 'activating', 'deactivating') for suffix in ('origin', 'iperf', 'capture'))
                    checks['routes_absent'] = all(not core.run(['ip', 'route', 'show', 'exact', s['address'] + '/32']).strip() for s in remote.get('sessions', {}).values())
                    table = remote.get('table')
                    rules = json.loads(ue.run(['ip', '-j', 'rule', 'show']))
                    routes = json.loads(ue.run(['ip', '-j', 'route', 'show', 'table', 'all']))
                    checks['ue_rules_routes_absent'] = table is None or not any(str(x.get('table')) == table for x in rules + routes)
                    from app.laboratory.qoe_session import accounts
                    after = accounts(core)
                    before = remote.get('accounts_before', [])
                    quota = {a['supi']: a['quota_bytes'] for a in after}
                    checks['quota_unchanged'] = bool(before) and all(quota.get(a['supi']) == a['quota_bytes'] for a in before)
                    if not before and not remote:
                        checks['quota_unchanged'] = True  # no remote preparation began
                    if all(checks.values()):
                        for node, suffix in (('core', '-cleanup.timer'), ('core', '-nwdaf-restore.timer'), ('ue', '-route.timer')):
                            hosts[node].run(['systemctl', 'stop', trial['tag'] + suffix], sudo=True, check=False)
                        for node, field in (('core', 'remote'), ('ue', 'uremote')):
                            path = remote.get(field)
                            if path:
                                # Exact run-owned directory only. Validate again before recursive removal.
                                if not re.fullmatch('/home/emsadmin/' + re.escape(trial['tag']) + '-[A-Za-z0-9]{6}', path):
                                    raise ValueError('invalid_owned_remote_directory')
                                hosts[node].run(['python3', '-c', "import pathlib,shutil,sys; p=pathlib.Path(sys.argv[1]); shutil.rmtree(p) if p.exists() else None", path], sudo=True)
                        trial['recovered'] = True
                    report['trials'].append({'ordinal': trial['ordinal'], 'checks': checks})
                    report['verified'] = report['verified'] and all(checks.values())
                finally:
                    for host in hosts.values(): host.client.close()
            if not context.get('trials'):
                report['scope'] = 'no_mutation_attempted'
            elif not context.get('admission'):
                # Auxiliary cleanup cannot release a Core lease when the policy
                # checkpoint and remote writer exclusion remain unverified.
                report['verified'] = False
                report['blocking_reason'] = 'effective_policy_recovery_unverified'
            else:
                report['limitations']=['Recovery is of the operational pilot and its owned auxiliaries; effective policy checkpoint unverified.']
            self.write(context, 'recovery-report.json', report)
            self.save(context)
            if not report['verified']:
                raise RuntimeError('recovery_not_verified')
            return report
        finally:
            self.recovering = False

    def finalize(self, row):
        self.row=row
        with self.runtime.repository.connect() as db:
            current=self.runtime.fenced(db,row['id'],row['token'])
            context=json.loads(current['real_context'])
        if not context.get('admission') or not context.get('trials'):return
        from app.laboratory.qoe_transport import Lab
        self.recovering=True
        try:
            settings=get_settings()
            owner=self.remote_owner()
            for port in (settings.ssh_port,settings.upf_ssh_port,settings.ue_ssh_port):
                host=Lab(settings,port,self.check)
                try:
                    state=json.loads(host._guard({**owner,'operation':'state'}))
                    if state['this_execution'] and state['state']=='released':continue
                    host.bind_owner(owner,recovery=True)
                    host.release_owner()
                finally:host.client.close()
        finally:self.recovering=False

    def verify(self, context):
        ordinal = context['current_run']['ordinal']
        trial = context['trials'][str(ordinal)]
        path = self.directory(context) / ('trial-' + str(ordinal))
        player = json.loads((path / (trial['case'] + '-player.json')).read_text(encoding='utf-8'))
        result = json.loads((path / 'result.json').read_text(encoding='utf-8'))
        trace = player.get('player') or {}
        transfers = [t for t in result.get('transfers', []) if t['phase'] == trial['case']]
        if player.get('status') != 'PLAYED_TO_END' or not transfers or not all(t['verified'] and t['ue_interface'] == 'uesimtun0' for t in transfers):
            raise ValueError('receiver_or_player_not_verified')
        before, after = result.get('receiver_before', {}), result.get('receiver_after', {})
        received = sum(t['bytes'] for t in transfers)
        if (not before or not after or before['boot_id'] != after['boot_id']
                or before['ifindex'] != after['ifindex'] or after['rx_bytes'] - before['rx_bytes'] < received):
            raise ValueError('receiver_counter_continuity_not_verified')
        expected = 'inactive' if trial['case'] == 'baseline' else 'active'
        if result.get('treatment_before') != expected or result.get('treatment_after') != expected:
            raise ValueError('controller_treatment_not_verified')
        raw = (path / (trial['case'] + '-iperf.json')).read_text(encoding='utf-8')
        traffic = json.loads(raw[raw.index('{'):raw.rindex('}') + 1])
        from app.laboratory.traffic_evidence import received_udp_bytes
        competing_received = received_udp_bytes(traffic)
        document = p1203_document(json.loads((path / 'ffprobe.json').read_text(encoding='utf-8')), trace)
        score = score_p1203_locally(document)
        if score['mos'] is None and score.get('reason')=='p1203_dependency_not_installed':
            # Reuse the already-installed pinned research model on the core VM
            # as an offline calculator: no HTTP publication or NF operation.
            from app.laboratory.qoe_transport import Lab
            settings=get_settings()
            host=Lab(settings,settings.ssh_port,self.check)
            try:
                host.bind_owner(self.remote_owner())
                script="""import importlib.util,json,sys
p='/home/emsadmin/maestro-charging/nwdaf/app/engine/service_experience.py'
s=importlib.util.spec_from_file_location('offline_service_experience',p)
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
print(json.dumps(m.estimate(json.loads(sys.argv[1]))))
"""
                score={**json.loads(host.run(['/home/emsadmin/maestro-charging/nwdaf/.venv/bin/python','-B','-c',script,canonical(document)],timeout=30)),
                       'status':'estimated','publication':False,'calculator':'existing_core_vm_research_environment'}
            finally:host.client.close()
        metrics = {'ordinal': ordinal, 'treatment': trial['run']['treatment'],
                   'startup_delay_seconds': trace['startup_seconds'], 'p1203_mos': score['mos'],
                   'p1203_status': score['status'],
                   'received_payload_bytes': sum(t['bytes'] for t in transfers),
                   'competing_received_bytes': competing_received,
                   'receiver_rx_delta_bytes': after['rx_bytes'] - before['rx_bytes'],
                   'rebuffer_count': len(trace['stalls']), 'rebuffer_seconds': sum(s[1] for s in trace['stalls']),
                   'validity_status': 'inconclusive',
                   'exclusion_reason': 'effective_policy_baseline_and_recovery_unverified'}
        trial['metrics'] = metrics
        self.write(context, 'p1203-' + str(ordinal) + '.json', {'input': document, 'score': score, 'metrics': metrics})
        self.save(context)
        return metrics

    def measure(self, context):
        from app.laboratory.research import Research
        from app.models import UserPublic, Role
        user = UserPublic(username=self.row['owner'], role=context.get('owner_role', Role.student),
                          testbed=context.get('owner_testbed', self.row['testbed']))
        research = Research(self.runtime.repository)
        trials = []
        sources = {item['metrics'].get('source', 'live_campaign') for item in context.get('trials', {}).values() if 'metrics' in item}
        if len(sources) != 1:
            raise ValueError('mixed_or_missing_measurement_provenance')
        source = sources.pop()
        for item in context.get('trials', {}).values():
            if 'metrics' not in item: continue
            evidence = research.add_evidence(context['experiment_id'], user, self.row['id'] + '-' + str(item['ordinal']),
                                             'synthetic_qoe' if source == 'synthetic_test' else 'live_qoe',
                                             {'source': source, 'metrics': item['metrics']})
            trials.append(Trial(ordinal=item['ordinal'], block=item['run']['block'],
                                treatment='disabled' if item['case'] == 'baseline' else 'enabled',
                                startup_seconds=item['metrics']['startup_delay_seconds'], execution_status='completed',
                                validity_status=item['metrics']['validity_status'],
                                exclusion_reason=item['metrics'].get('exclusion_reason'), evidence_ids=[evidence['id']]))
        dataset = Dataset(source=source, provenance='NWDAF operational-regime pilot ' + self.row['id'], trials=trials)
        data = research.add_evidence(context['experiment_id'], user, self.row['id'] + '-dataset-' + str(len(trials)), 'dataset', dataset.model_dump())
        research.analyze(context['experiment_id'], data['id'], user, self.row['id'] + '-analysis-' + str(len(trials)))
        self.write(context, 'dataset.json', dataset.model_dump())
        self.write(context, 'analysis.json', analyze(dataset))
        measured=[item['metrics'] for item in context.get('trials',{}).values() if 'metrics' in item]
        self.write(context,'measurements.json',{'source':source,'metrics':measured,
                   'hypothesis_outcome':'inconclusive','protocol':'qoe_operational_pilot_v1'})
        return {'dataset_id': data['id'], 'hypothesis_outcome': 'inconclusive'}

    def perform(self, row, step):
        self.row = row
        with self.runtime.repository.connect() as db:
            current = self.runtime.fenced(db, row['id'], row['token'])
            context = json.loads(current['real_context'])
            outcome = json.loads(current['outcome'])
        context['execution_id'] = row['id']
        if step.get('run'): context['current_run'] = step['run']
        self.write(context, 'descriptor.json', context['descriptor'])
        action = step['action']
        if action in ('prepare', 'apply'):
            outcome['recovery_verified'] = False
            self.runtime.save_real(row['id'], row['token'], outcome=outcome)
        try:
            if action == 'preflight': result = self.validate(context)
            elif action == 'prepare': result = self.prepare(context)
            elif action == 'apply': result = self.apply(step['run']['treatment'], context)
            elif action == 'restore': result = self.compensate(context)
            elif action == 'verify_restored':
                result = self.verify_recovery(context)
                outcome['recovery_verified'] = result['verified']
                outcome['recovery_scope'] = result.get('scope', 'unspecified')
            elif action == 'verify': result = self.verify(context)
            elif action == 'measure': result = self.measure(context)
            else: raise ValueError('Unsupported real adapter action')
            metrics = [t['metrics'] for t in context.get('trials', {}).values() if 'metrics' in t]
            synthetic = any(m.get('source') == 'synthetic_test' for m in metrics)
            outcome.update(network_measurements=bool(metrics) and not synthetic, metrics=metrics or None,
                           source='synthetic_test' if synthetic else 'live_campaign',
                           validity_status='valid' if len(metrics) == 2 and all(m['validity_status'] == 'valid' for m in metrics) else 'inconclusive',
                           hypothesis_outcome='inconclusive' if metrics else 'not_evaluated')
            # Session callbacks may have persisted newer recovery paths/results.
            # Merge those into the current step rather than overwrite them.
            with self.runtime.repository.connect() as db:
                saved = json.loads(self.runtime.fenced(db, row['id'], row['token'])['real_context'])
            for key, trial in context.get('trials', {}).items():
                if key in saved.get('trials', {}) and 'remote' in saved['trials'][key]:
                    trial['remote'] = saved['trials'][key]['remote']
            self.runtime.save_real(row['id'], row['token'], context=context, outcome=outcome)
            return {'action': action, 'run_ordinal': step['run']['ordinal'] if step.get('run') else None,
                    'source': 'live_campaign', 'result': result}
        finally:
            self.export(row)

    def export(self, row):
        directory = self.root / row['id']
        directory.mkdir(parents=True, exist_ok=True)
        with self.runtime.repository.connect() as db:
            current = db.execute('SELECT * FROM lab_executions WHERE id=?', (row['id'],)).fetchone()
            events = [{'id': e['id'], 'event': e['event'], 'cursor': e['cursor'], 'created_at': e['created_at'], 'detail': json.loads(e['detail'])}
                      for e in db.execute('SELECT * FROM lab_journal WHERE execution_id=? ORDER BY id', (row['id'],))]
        (directory / 'execution-events.jsonl').write_text(''.join(canonical(e) + '\n' for e in events), encoding='utf-8')
        (directory / 'execution.json').write_text(canonical(self.runtime.public(current)), encoding='utf-8')
        files = []
        for path in sorted(directory.rglob('*')):
            if path.is_file() and path.name != 'manifest.json' and not path.name.endswith(('.tmp', '.log')):
                raw = path.read_bytes()
                files.append({'path': path.relative_to(directory).as_posix(), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
        (directory / 'manifest.json').write_text(canonical({'schema_version': 1, 'files': files}), encoding='utf-8')
