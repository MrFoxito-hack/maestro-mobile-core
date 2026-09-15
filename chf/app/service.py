import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

from app.models import (
    ChargingDataRequest,
    ChargingDataResponse,
    FinalUnitIndication,
    GrantedUnit,
    MultipleUnitInformation,
)
from app.repository import ChargingRepository, utc_now


class ChargingError(Exception):
    def __init__(self, status: int, cause: str, detail: str):
        self.status = status
        self.cause = cause
        self.detail = detail
        super().__init__(detail)


def request_hash(request: ChargingDataRequest) -> str:
    canonical = request.model_dump_json(by_alias=True, exclude_none=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def create_key(request: ChargingDataRequest) -> str:
    if request.chargingId is None:
        raise ChargingError(
            400,
            "MANDATORY_IE_MISSING",
            "chargingId is required by the MAEstro PDU-session charging profile",
        )
    consumer = request.nfConsumerIdentification.nFName
    if not consumer:
        raise ChargingError(
            400,
            "MANDATORY_IE_MISSING",
            "nfConsumerIdentification.nFName is required by this profile",
        )
    identity = f"{consumer}|{request.subscriberIdentifier}|{request.chargingId}"
    return hashlib.sha256(identity.encode()).hexdigest()


def requested_volume(request: ChargingDataRequest, default_grant: int) -> tuple[int, int]:
    if len(request.multipleUnitUsage) > 1:
        raise ChargingError(400, "CHARGING_FAILED", "the first profile supports one rating group")
    if not request.multipleUnitUsage:
        return 1, default_grant
    usage = request.multipleUnitUsage[0]
    requested = usage.requestedUnit.totalVolume if usage.requestedUnit else None
    return usage.ratingGroup, requested if requested is not None else default_grant


def used_volume(request: ChargingDataRequest) -> int:
    return sum(
        container.volume()
        for usage in request.multipleUnitUsage
        for container in usage.usedUnitContainer
    )


class ChargingService:
    def __init__(self, repository: ChargingRepository, default_grant: int, validity_time: int):
        self.repository = repository
        self.default_grant = default_grant
        self.validity_time = validity_time

    def _response(self, request: ChargingDataRequest, rating_group: int, grant: int) -> ChargingDataResponse:
        exhausted = grant == 0
        return ChargingDataResponse(
            invocationTimeStamp=datetime.now(timezone.utc),
            invocationSequenceNumber=request.invocationSequenceNumber,
            sessionFailover="FAILOVER_NOT_SUPPORTED",
            multipleUnitInformation=[
                MultipleUnitInformation(
                    resultCode="QUOTA_LIMIT_REACHED" if exhausted else "SUCCESS",
                    ratingGroup=rating_group,
                    grantedUnit=GrantedUnit(totalVolume=grant),
                    validityTime=self.validity_time,
                    finalUnitIndication=(
                        FinalUnitIndication(finalUnitAction="TERMINATE") if exhausted else None
                    ),
                )
            ],
        )

    def create(self, request: ChargingDataRequest) -> tuple[str, ChargingDataResponse]:
        supi = request.subscriberIdentifier
        if not supi:
            raise ChargingError(400, "MANDATORY_IE_MISSING", "subscriberIdentifier is required")
        rating_group, wanted = requested_volume(request, self.default_grant)
        identity = create_key(request)
        digest = request_hash(request)
        now = utc_now()
        with self.repository.transaction(immediate=True) as conn:
            existing = conn.execute(
                "SELECT * FROM charging_sessions WHERE create_key=?", (identity,)
            ).fetchone()
            if existing:
                prior = conn.execute(
                    """SELECT * FROM charging_events
                       WHERE charging_data_ref=? AND operation='CREATE'
                         AND invocation_sequence=?""",
                    (existing["charging_data_ref"], request.invocationSequenceNumber),
                ).fetchone()
                if not prior or prior["request_hash"] != digest:
                    raise ChargingError(
                        409,
                        "CONTEXT_CONFLICT",
                        "the charging context already exists with different Create data",
                    )
                return existing["charging_data_ref"], ChargingDataResponse.model_validate_json(
                    prior["response_json"]
                )
            account = conn.execute(
                "SELECT * FROM charging_accounts WHERE supi=?", (supi,)
            ).fetchone()
            if not account or not account["enabled"]:
                raise ChargingError(403, "USER_UNKNOWN", "subscriber has no enabled charging account")
            reserved = conn.execute(
                "SELECT COALESCE(SUM(reserved_bytes),0) FROM charging_sessions WHERE supi=? AND status='OPEN'",
                (supi,),
            ).fetchone()[0]
            available = max(0, account["quota_bytes"] - account["consumed_bytes"] - reserved)
            grant = min(wanted, available)
            response = self._response(request, rating_group, grant)
            response_json = response.model_dump_json(exclude_none=True)
            ref = str(uuid4())
            conn.execute(
                """INSERT INTO charging_sessions(
                     charging_data_ref,create_key,supi,rating_group,status,reserved_bytes,consumed_bytes,
                     last_invocation_sequence,created_at,updated_at
                   ) VALUES(?,?,?,?,'OPEN',?,0,?,?,?)""",
                (ref, identity, supi, rating_group, grant, request.invocationSequenceNumber, now, now),
            )
            conn.execute(
                """INSERT INTO charging_events(
                     charging_data_ref,operation,invocation_sequence,request_hash,
                     used_bytes,granted_bytes,result_code,response_json,created_at
                   ) VALUES(?,'CREATE',?,?,0,?,?,?,?)""",
                (
                    ref,
                    request.invocationSequenceNumber,
                    digest,
                    grant,
                    "QUOTA_LIMIT_REACHED" if grant == 0 else "SUCCESS",
                    response_json,
                    now,
                ),
            )
        return ref, response

    def update(self, ref: str, request: ChargingDataRequest) -> ChargingDataResponse:
        return self._change(ref, request, release=False)

    def release(self, ref: str, request: ChargingDataRequest) -> None:
        self._change(ref, request, release=True)

    def _change(
        self, ref: str, request: ChargingDataRequest, *, release: bool
    ) -> ChargingDataResponse:
        operation = "RELEASE" if release else "UPDATE"
        digest = request_hash(request)
        used = used_volume(request)
        rating_group, wanted = requested_volume(request, self.default_grant)
        now = utc_now()
        with self.repository.transaction(immediate=True) as conn:
            session = conn.execute(
                "SELECT * FROM charging_sessions WHERE charging_data_ref=?", (ref,)
            ).fetchone()
            if not session:
                raise ChargingError(404, "CONTEXT_NOT_FOUND", "charging resource was not found")
            prior = conn.execute(
                """SELECT * FROM charging_events
                   WHERE charging_data_ref=? AND operation=? AND invocation_sequence=?""",
                (ref, operation, request.invocationSequenceNumber),
            ).fetchone()
            if prior:
                try:
                    stored = self.repository.stored_response(prior, digest)
                except ValueError as exc:
                    raise ChargingError(409, "SEQUENCE_CONFLICT", "sequence was reused with another payload") from exc
                if release:
                    return self._response(request, session["rating_group"], 0)
                return ChargingDataResponse.model_validate(stored)
            if session["status"] != "OPEN":
                raise ChargingError(410, "CONTEXT_NOT_FOUND", "charging resource is already released")
            if request.invocationSequenceNumber <= session["last_invocation_sequence"]:
                raise ChargingError(409, "SEQUENCE_OUT_OF_ORDER", "invocation sequence must increase")
            if request.subscriberIdentifier and request.subscriberIdentifier != session["supi"]:
                raise ChargingError(409, "CONTEXT_MISMATCH", "subscriber does not own this resource")
            if request.multipleUnitUsage and rating_group != session["rating_group"]:
                raise ChargingError(409, "RATING_GROUP_MISMATCH", "rating group differs from Create")

            account = conn.execute(
                "SELECT * FROM charging_accounts WHERE supi=?", (session["supi"],)
            ).fetchone()
            applied_used = used
            new_consumed = account["consumed_bytes"] + applied_used
            remaining_reservation = max(0, session["reserved_bytes"] - applied_used)
            conn.execute(
                "UPDATE charging_accounts SET consumed_bytes=?,updated_at=? WHERE supi=?",
                (new_consumed, now, session["supi"]),
            )

            grant = 0
            if not release:
                other_reserved = conn.execute(
                    """SELECT COALESCE(SUM(reserved_bytes),0) FROM charging_sessions
                       WHERE supi=? AND status='OPEN' AND charging_data_ref<>?""",
                    (session["supi"], ref),
                ).fetchone()[0]
                available = max(0, account["quota_bytes"] - new_consumed - other_reserved - remaining_reservation)
                grant = min(wanted, available)
                remaining_reservation += grant
            response = self._response(request, session["rating_group"], grant)
            response_json = None if release else response.model_dump_json(exclude_none=True)
            conn.execute(
                """UPDATE charging_sessions SET
                     status=?,reserved_bytes=?,consumed_bytes=consumed_bytes+?,
                     last_invocation_sequence=?,updated_at=?,released_at=?
                   WHERE charging_data_ref=?""",
                (
                    "RELEASED" if release else "OPEN",
                    0 if release else remaining_reservation,
                    applied_used,
                    request.invocationSequenceNumber,
                    now,
                    now if release else None,
                    ref,
                ),
            )
            conn.execute(
                """INSERT INTO charging_events(
                     charging_data_ref,operation,invocation_sequence,request_hash,
                     used_bytes,granted_bytes,result_code,response_json,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    ref,
                    operation,
                    request.invocationSequenceNumber,
                    digest,
                    applied_used,
                    grant,
                    "RELEASED" if release else ("QUOTA_LIMIT_REACHED" if grant == 0 else "SUCCESS"),
                    response_json,
                    now,
                ),
            )
            return response
