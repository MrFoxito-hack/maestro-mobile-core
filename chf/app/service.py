import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.engine import settle, settle_zero_rated
from app.errors import ChargingError
from app.models import (MAX_BYTES, ChargingDataRequest, ChargingDataResponse,
                        FinalUnitIndication, GrantedUnit, MultipleUnitInformation)
from app.repository import ChargingRepository, utc_now


def digest_json(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def request_hash(request):
    # RetransmissionIndicator may legitimately change on retry (TS 32.291).
    return digest_json(request.model_dump(mode="json", exclude_none=True,
                                         exclude={"retransmissionIndicator"}))


def create_key(request):
    if request.chargingId is None or not request.subscriberIdentifier:
        raise ChargingError(400, "MANDATORY_IE_MISSING", "subscriberIdentifier and chargingId are required by this profile")
    return digest_json([str(request.nfConsumerIdentification.nFName),
                        request.subscriberIdentifier, request.chargingId])


def requested_volume(request, default_grant, mode='BYTE_QUOTA'):
    if not request.multipleUnitUsage:
        raise ChargingError(400, "MANDATORY_IE_MISSING", "one ratingGroup is required")
    usage = request.multipleUnitUsage[0]
    unit = usage.requestedUnit
    messages = mode == 'MESSAGE_QUOTA'
    if unit and (unit.totalVolume is not None if messages else unit.serviceSpecificUnits is not None):
        raise ChargingError(400, 'UNIT_TYPE_MISMATCH', 'requested units differ from the service policy')
    wanted = (unit.serviceSpecificUnits if messages else unit.totalVolume) if unit else None
    return usage.ratingGroup, default_grant if wanted is None else wanted


class ChargingService:
    def __init__(self, repository: ChargingRepository, default_grant: int, validity_time: int):
        self.repository = repository
        self.default_grant = default_grant
        self.validity_time = validity_time

    def _response(self, request, rating_group, grant, final, *, denied=False, messages=False):
        return ChargingDataResponse(
            invocationTimeStamp=datetime.now(timezone.utc),
            invocationSequenceNumber=request.invocationSequenceNumber,
            multipleUnitInformation=[MultipleUnitInformation(
                resultCode="END_USER_SERVICE_DENIED" if denied else ("SUCCESS" if grant else "QUOTA_LIMIT_REACHED"),
                ratingGroup=rating_group, grantedUnit=GrantedUnit(**{
                    'serviceSpecificUnits' if messages else 'totalVolume': grant}),
                validityTime=self.validity_time,
                # FUI with a positive grant applies AFTER those last units are used.
                finalUnitIndication=FinalUnitIndication(finalUnitAction="TERMINATE") if final else None,
            )])

    def _valid_until(self):
        return (datetime.now(timezone.utc) + timedelta(seconds=self.validity_time)).isoformat()

    @staticmethod
    def _replay(conn, ref, operation, request):
        conn.execute('INSERT INTO charging_replays(charging_data_ref,operation,invocation_sequence,created_at) VALUES(?,?,?,?)',
                     (ref, operation, request.invocationSequenceNumber, utc_now()))

    @staticmethod
    def _event(conn, ref, op, request, result, used, grant, code):
        conn.execute("""INSERT INTO charging_events(charging_data_ref,operation,invocation_sequence,
            request_hash,used_bytes,granted_bytes,result_code,response_json,created_at)
            VALUES(?,?,?,?,?,?,?,?,?)""",
            (ref, op, request.invocationSequenceNumber, request_hash(request), used, grant, code,
             result.model_dump_json(exclude_none=True) if result else None, utc_now()))

    def create(self, request: ChargingDataRequest):
        identity = create_key(request)
        if request.invocationSequenceNumber not in (0, 1):
            raise ChargingError(400, "SEQUENCE_OUT_OF_ORDER", "initial invocation is 1 (0 accepted for legacy clients)")
        if not request.notifyUri:
            raise ChargingError(400, "MANDATORY_IE_MISSING", "notifyUri is required for session Create")
        if not request.multipleUnitUsage:
            raise ChargingError(400, 'MANDATORY_IE_MISSING', 'one ratingGroup is required')
        rating_group = request.multipleUnitUsage[0].ratingGroup
        unit = request.multipleUnitUsage[0]
        if unit.usedUnitContainer:
            raise ChargingError(400, "UNSUPPORTED_PROFILE", "initial usage on Create is not supported by this pre-delivery profile")
        supi = request.subscriberIdentifier
        owner = str(request.nfConsumerIdentification.nFName)
        with self.repository.transaction(immediate=True) as conn:
            existing = conn.execute("SELECT * FROM charging_sessions WHERE create_key=?", (identity,)).fetchone()
            if existing:
                prior = conn.execute("""SELECT * FROM charging_events WHERE charging_data_ref=?
                    AND operation='CREATE' AND invocation_sequence=?""",
                    (existing['charging_data_ref'], request.invocationSequenceNumber)).fetchone()
                if not prior or prior['request_hash'] != request_hash(request):
                    raise ChargingError(409, "CONTEXT_CONFLICT", "Create identity already has different data")
                self._replay(conn, existing['charging_data_ref'], 'CREATE', request)
                return existing['charging_data_ref'], ChargingDataResponse.model_validate_json(prior['response_json'])
            account = self.repository.account_snapshot(conn, supi)
            if not account or not account['enabled']:
                raise ChargingError(403, "USER_UNKNOWN", "subscriber has no enabled charging account")
            context = request.pDUSessionChargingInformation
            policy = self.repository.resolve_policy(conn, context, rating_group)
            mode = policy['mode']
            messages = mode == 'MESSAGE_QUOTA'
            _, wanted = requested_volume(request, policy['grantBlockSize'] if messages else self.default_grant, mode)
            if mode == 'ZERO_RATED':
                value = settle_zero_rated(authorized=0, used=0, wanted=wanted, enabled=True)
            else:
                wallet = self.repository.message_snapshot(conn, supi) if messages else account
                if not wallet:
                    raise ChargingError(403, 'USER_UNKNOWN', 'message quota account is required')
                suffix = 'messages' if messages else 'bytes'
                value = settle(quota=wallet['quota_' + suffix], consumed=wallet['consumed_' + suffix],
                               other_reserved=wallet['reserved_' + suffix], reserved=0, used=0,
                               wanted=min(wanted, policy['grantBlockSize']) if messages else wanted, enabled=True)
            ref, now = str(uuid4()), utc_now()
            conn.execute("""INSERT INTO charging_sessions(charging_data_ref,create_key,supi,rating_group,
                status,reserved_bytes,consumed_bytes,last_invocation_sequence,created_at,updated_at,
                owner_nf,charging_id,notify_uri,context_json,upf_id,granted_bytes,valid_until)
                VALUES(?,?,?,?,'OPEN',?,0,?,?,?,?,?,?,?,?,?,?)""",
                (ref, identity, supi, rating_group, value.reservation if mode == 'BYTE_QUOTA' else 0, request.invocationSequenceNumber,
                 now, now, owner, request.chargingId, str(request.notifyUri),
                 context.model_dump_json(exclude_none=True) if context else None,
                 str(unit.uPFID) if unit.uPFID else "", 0 if messages else value.reservation, self._valid_until()))
            conn.execute('''UPDATE charging_sessions SET policy_json=?,authorized_bytes=?,
                reserved_messages=?,granted_messages=? WHERE charging_data_ref=?''',
                (json.dumps(policy, sort_keys=True), 0 if messages else value.reservation,
                 value.reservation if messages else 0, value.reservation if messages else 0, ref))
            result = self._response(request, rating_group, value.reservation, value.final, messages=messages)
            self._event(conn, ref, "CREATE", request, result, 0, 0 if messages else value.reservation, result.multipleUnitInformation[0].resultCode)
            self.repository.append_ledger(conn, supi, ref, "CREATE", owner,
                {"before": account, "after": self.repository.account_snapshot(conn, supi),
                 "grant": value.reservation, 'policy': policy,
                 'unit': 'MESSAGE' if messages else 'BYTE',
                 'walletReservation': value.reservation if mode != 'ZERO_RATED' else 0})
        return ref, result

    @staticmethod
    def _check_owner(session, request):
        if not session['owner_nf']:
            raise ChargingError(409, "LEGACY_CONTEXT", "legacy session requires explicit operator reconciliation")
        if session['owner_nf'] != str(request.nfConsumerIdentification.nFName):
            raise ChargingError(403, "CONTEXT_MISMATCH", "NF consumer does not own this resource")
        if request.subscriberIdentifier and request.subscriberIdentifier != session['supi']:
            raise ChargingError(409, "CONTEXT_MISMATCH", "subscriber differs from Create")
        if request.chargingId is not None and request.chargingId != session['charging_id']:
            raise ChargingError(409, "CONTEXT_MISMATCH", "chargingId differs from Create")
        context = request.pDUSessionChargingInformation
        if context and context.pduSessionInformation and session['context_json']:
            old = json.loads(session['context_json']).get('pduSessionInformation') or {}
            new = context.pduSessionInformation.model_dump(mode="json", exclude_none=True)
            for key in ('pduSessionID', 'dnnId', 'networkSlicingInfo'):
                if key in new and key in old and new[key] != old[key]:
                    raise ChargingError(409, "CONTEXT_MISMATCH", "PDU session identity changed")

    @staticmethod
    def _usage(conn, session, request):
        totals = dict(total=0, ul=0, dl=0, messages=0)
        message_policy = json.loads(session['policy_json'])['mode'] == 'MESSAGE_QUOTA'
        last = session['last_usage_sequence']
        if not request.multipleUnitUsage:
            return totals, last
        unit = request.multipleUnitUsage[0]
        if unit.ratingGroup != session['rating_group'] or (str(unit.uPFID) if unit.uPFID else "") != session['upf_id']:
            raise ChargingError(409, "CONTEXT_MISMATCH", "ratingGroup or UPF differs from Create")
        for report in unit.usedUnitContainer:
            if (report.serviceSpecificUnits is None) == message_policy:
                raise ChargingError(400, 'UNIT_TYPE_MISMATCH', 'usage units differ from the service policy')
            payload = report.model_dump(mode="json", exclude_none=True)
            digest = digest_json(payload)
            previous = conn.execute("SELECT payload_hash FROM usage_events WHERE charging_data_ref=? AND local_sequence=?",
                                    (session['charging_data_ref'], report.localSequenceNumber)).fetchone()
            if previous:
                if previous['payload_hash'] != digest:
                    raise ChargingError(409, "USAGE_CONFLICT", "usage sequence reused with changed measurements")
                continue
            if report.localSequenceNumber <= last:
                raise ChargingError(409, "USAGE_OUT_OF_ORDER", "usage sequence must increase; buffer/reconcile reports at SMF")
            last = report.localSequenceNumber
            ul, dl = report.uplinkVolume or 0, report.downlinkVolume or 0
            conn.execute("""INSERT INTO usage_events(charging_data_ref,local_sequence,payload_hash,total_bytes,
                uplink_bytes,downlink_bytes,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?)""",
                (session['charging_data_ref'], last, digest, report.volume(), ul, dl, json.dumps(payload), utc_now()))
            totals['total'] += report.volume()
            totals['ul'] += ul
            totals['dl'] += dl
            totals['messages'] += report.serviceSpecificUnits or 0
        if session['observed_bytes'] + totals['total'] > MAX_BYTES:
            raise ChargingError(400, "VOLUME_LIMIT", "session exceeds the exact-integer profile limit")
        if session['observed_messages'] + totals['messages'] > MAX_BYTES:
            raise ChargingError(400, 'VOLUME_LIMIT', 'message count exceeds the exact-integer profile limit')
        return totals, last

    def update(self, ref, request):
        return self._change(ref, request, release=False)

    def release(self, ref, request):
        self._change(ref, request, release=True)

    def _change(self, ref, request, *, release):
        operation = "RELEASE" if release else "UPDATE"
        with self.repository.transaction(immediate=True) as conn:
            session = conn.execute("SELECT * FROM charging_sessions WHERE charging_data_ref=?", (ref,)).fetchone()
            if not session:
                raise ChargingError(404, "CONTEXT_NOT_FOUND", "charging resource was not found")
            self._check_owner(session, request)
            prior = conn.execute("""SELECT * FROM charging_events WHERE charging_data_ref=?
                AND operation=? AND invocation_sequence=?""", (ref, operation, request.invocationSequenceNumber)).fetchone()
            if prior:
                if prior['request_hash'] != request_hash(request):
                    raise ChargingError(409, "SEQUENCE_CONFLICT", "sequence reused with another payload")
                self._replay(conn, ref, operation, request)
                return None if release else ChargingDataResponse.model_validate_json(prior['response_json'])
            if session['status'] != 'OPEN':
                raise ChargingError(410, "CONTEXT_NOT_FOUND", "resource already closed; replay the exact Release for a retry")
            if request.invocationSequenceNumber != session['last_invocation_sequence'] + 1:
                raise ChargingError(409, "SEQUENCE_OUT_OF_ORDER", "invocation sequence must advance by exactly one")
            policy = json.loads(session['policy_json'])
            mode = policy['mode']
            messages = mode == 'MESSAGE_QUOTA'
            rating_group, wanted = (session['rating_group'], 0) if release else requested_volume(
                request, policy['grantBlockSize'] if messages else self.default_grant, mode)
            totals, last = self._usage(conn, session, request)
            account = self.repository.account_snapshot(conn, session['supi'])
            allowed = bool(account['enabled']) and (mode == 'ZERO_RATED' or not session['overrun_messages' if messages else 'overrun_bytes'])
            if mode == 'ZERO_RATED':
                value = settle_zero_rated(authorized=session['authorized_bytes'], used=totals['total'],
                                         wanted=wanted, enabled=allowed, release=release)
            else:
                wallet = self.repository.message_snapshot(conn, session['supi']) if messages else account
                suffix = 'messages' if messages else 'bytes'
                value = settle(quota=wallet['quota_' + suffix], consumed=wallet['consumed_' + suffix],
                               other_reserved=wallet['reserved_' + suffix]-session['reserved_' + suffix],
                               reserved=session['reserved_' + suffix], used=totals['messages' if messages else 'total'],
                               wanted=min(wanted, policy['grantBlockSize']) if messages else wanted,
                               enabled=allowed, release=release)
            if session['granted_messages' if messages else 'granted_bytes'] + value.reservation > MAX_BYTES:
                raise ChargingError(400, 'VOLUME_LIMIT', 'allocation history exceeds the exact-integer profile limit')
            now = utc_now()
            reason = self._release_reason(request) if release else None
            # Lower/update the reservation before incrementing the account debit.
            # Both writes commit atomically under BEGIN IMMEDIATE.
            conn.execute("""UPDATE charging_sessions SET status=?,reserved_bytes=?,consumed_bytes=consumed_bytes+?,
                observed_bytes=observed_bytes+?,uplink_bytes=uplink_bytes+?,downlink_bytes=downlink_bytes+?,
                unclassified_bytes=unclassified_bytes+?,overrun_bytes=overrun_bytes+?,
                granted_bytes=granted_bytes+?,last_usage_sequence=?,last_invocation_sequence=?,
                updated_at=?,released_at=?,valid_until=?,closure_reason=? WHERE charging_data_ref=?""",
                ("RELEASED" if release else "OPEN", value.reservation if mode == 'BYTE_QUOTA' else 0,
                 0 if messages else value.debit, totals['total'],
                 totals['ul'], totals['dl'], totals['total']-totals['ul']-totals['dl'],
                 0 if messages else value.overrun, 0 if messages else value.reservation, last, request.invocationSequenceNumber,
                 now, now if release else None, self._valid_until(), reason, ref))
            conn.execute('''UPDATE charging_sessions SET authorized_bytes=?,reserved_messages=?,
                consumed_messages=consumed_messages+?,observed_messages=observed_messages+?,
                overrun_messages=overrun_messages+?,granted_messages=granted_messages+?
                WHERE charging_data_ref=?''',
                (0 if messages else value.reservation, value.reservation if messages else 0,
                 value.debit if messages else 0, totals['messages'], value.overrun if messages else 0,
                 value.reservation if messages else 0, ref))
            if messages:
                conn.execute('UPDATE message_accounts SET consumed_messages=consumed_messages+? WHERE supi=?',
                             (value.debit, session['supi']))
            else:
                conn.execute("UPDATE charging_accounts SET consumed_bytes=consumed_bytes+?,updated_at=? WHERE supi=?",
                             (value.debit, now, session['supi']))
            result = None if release else self._response(request, rating_group, value.reservation, value.final, denied=not allowed, messages=messages)
            self._event(conn, ref, operation, request, result, totals['total'], 0 if messages else value.reservation,
                        "RELEASED" if release else result.multipleUnitInformation[0].resultCode)
            self.repository.append_ledger(conn, session['supi'], ref, operation, session['owner_nf'],
                {"before": account, "after": self.repository.account_snapshot(conn, session['supi']),
                 "observedDelta": totals['total'], "debit": value.debit, "overrun": value.overrun,
                 "returnedReservation": max(0, session['reserved_messages' if messages else 'reserved_bytes']-value.debit),
                 "replacementGrant": value.reservation, 'policy': policy,
                 'unit': 'MESSAGE' if messages else 'BYTE', 'observedMessages': totals['messages']})
            if release:
                self._cdr(conn, ref, reason, "NF_REPORTED")
            return result

    @staticmethod
    def _release_reason(request):
        triggers = [t.triggerType for t in request.triggers]
        triggers += [t.triggerType for u in request.multipleUnitUsage for c in u.usedUnitContainer for t in c.triggers]
        return ("QUOTA_EXHAUSTED" if "QUOTA_EXHAUSTED" in triggers else
                "ABNORMAL_RELEASE" if "ABNORMAL_RELEASE" in triggers else
                "FINAL" if "FINAL" in triggers else "UNSPECIFIED")

    @staticmethod
    def _cdr(conn, ref, reason, evidence):
        session = dict(conn.execute("SELECT * FROM charging_sessions WHERE charging_data_ref=?", (ref,)).fetchone())
        context = json.loads(session['context_json']) if session['context_json'] else {}
        pdu = context.get('pduSessionInformation') or {}
        record = dict(schemaVersion=2, recordType="MAESTRO_EDUCATIONAL_CDR",
                      servicePolicy=json.loads(session['policy_json']), ratingGroup=session['rating_group'],
                      totalMessages=session['observed_messages'], debitedMessages=session['consumed_messages'],
                      overrunMessages=session['overrun_messages'], sumOfReplacementGrantsMessages=session['granted_messages'],
                      chargingSessionId=ref, supi=session['supi'], nfConsumer=session['owner_nf'],
                      chargingId=session['charging_id'], pduSession=pdu, upfId=session['upf_id'] or None,
                      startTimestamp=session['created_at'], endTimestamp=session['released_at'],
                      uplinkBytes=session['uplink_bytes'], downlinkBytes=session['downlink_bytes'],
                      unclassifiedBytes=session['unclassified_bytes'], totalBytes=session['observed_bytes'],
                      debitedBytes=session['consumed_bytes'], overrunBytes=session['overrun_bytes'],
                      sumOfReplacementGrantsBytes=session['granted_bytes'],
                      terminationReason=reason, evidence=evidence)
        if record['servicePolicy'].get('unitKind') == 'IP_PACKET':
            record.update(serviceSpecificUnit='IP_PACKET', totalPackets=session['observed_messages'],
                          debitedPackets=session['consumed_messages'], overrunPackets=session['overrun_messages'])
        conn.execute("INSERT INTO charging_cdrs(charging_data_ref,supi,closed_at,record_json) VALUES(?,?,?,?)",
                     (ref, session['supi'], session['released_at'], json.dumps(record, sort_keys=True)))

    def reconcile(self, ref, reason, actor="admin"):
        # Never invoked by an expiry timer: first the operator MUST stop/fence the
        # consumer/UPF. Missing final usage remains explicitly unknown evidence.
        with self.repository.transaction(immediate=True) as conn:
            session = conn.execute("SELECT * FROM charging_sessions WHERE charging_data_ref=?", (ref,)).fetchone()
            if not session:
                raise ChargingError(404, "CONTEXT_NOT_FOUND", "charging resource was not found")
            if session['status'] != 'OPEN':
                return {"status": "RELEASED", "changed": False}
            before = self.repository.account_snapshot(conn, session['supi'])
            now = utc_now()
            conn.execute("""UPDATE charging_sessions SET status='RELEASED',reserved_bytes=0,authorized_bytes=0,reserved_messages=0,
                released_at=?,updated_at=?,closure_reason='OPERATOR_RECONCILIATION' WHERE charging_data_ref=?""", (now, now, ref))
            self.repository.append_ledger(conn, session['supi'], ref, "RECONCILE", actor,
                {"reason": reason, "before": before, "after": self.repository.account_snapshot(conn, session['supi']),
                 "unreportedUsage": "UNKNOWN", "consumerStoppedConfirmedByOperator": True})
            self._cdr(conn, ref, "OPERATOR_RECONCILIATION", "PARTIAL_FINAL_USAGE_UNKNOWN")
            return {"status": "RELEASED", "changed": True}
