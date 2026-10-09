"""Supported subset of TS 32.291 Rel16, not the entire OpenAPI."""
from datetime import datetime
from ipaddress import IPv4Address, IPv6Address
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, model_validator

# Explicit profile bound: exact JSON integers in cJSON, SQLite and JavaScript.
# Reject values beyond this range, never silently truncate a normative Uint64.
MAX_BYTES = 2**53 - 1
Volume = Annotated[int, Field(strict=True, ge=0, le=MAX_BYTES)]
Uint32 = Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)]
Supi = Annotated[str, Field(pattern=r"^imsi-[0-9]{5,15}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class NFIdentification(StrictModel):
    nFName: UUID  # Required by our owner-bound session profile.
    nFIPv4Address: IPv4Address | None = None
    nFIPv6Address: IPv6Address | None = None
    nFFqdn: str | None = Field(default=None, max_length=253)
    nodeFunctionality: Literal["SMF"]


class RequestedUnit(StrictModel):
    totalVolume: Volume | None = None
    serviceSpecificUnits: Volume | None = None


class Trigger(StrictModel):
    triggerType: Literal["QUOTA_THRESHOLD", "QUOTA_EXHAUSTED", "VALIDITY_TIME",
                         "FINAL", "ABNORMAL_RELEASE", "FORCED_REAUTHORISATION"]
    triggerCategory: Literal["IMMEDIATE_REPORT", "DEFERRED_REPORT"]


class UsedUnitContainer(StrictModel):
    localSequenceNumber: Uint32
    totalVolume: Volume | None = None
    uplinkVolume: Volume | None = None
    downlinkVolume: Volume | None = None
    serviceSpecificUnits: Volume | None = None
    triggers: list[Trigger] = Field(default_factory=list, max_length=16)
    triggerTimestamp: AwareDatetime | None = None
    quotaManagementIndicator: Literal["ONLINE_CHARGING"] | None = None

    @model_validator(mode="after")
    def consistent_volume(self):
        if self.totalVolume is None and (self.uplinkVolume is not None or self.downlinkVolume is not None) and (self.uplinkVolume is None or self.downlinkVolume is None):
            raise ValueError('both directional volumes are required without totalVolume')
        if self.serviceSpecificUnits is None and self.totalVolume is None and (self.uplinkVolume is None or self.downlinkVolume is None):
            raise ValueError("provide totalVolume or both directional volumes")
        directional = (self.uplinkVolume or 0) + (self.downlinkVolume or 0)
        if self.totalVolume is not None:
            if directional > self.totalVolume:
                raise ValueError("directional usage exceeds totalVolume")
            if self.uplinkVolume is not None and self.downlinkVolume is not None and directional != self.totalVolume:
                raise ValueError("totalVolume must equal UL + DL when both are supplied")
        if self.volume() > MAX_BYTES:
            raise ValueError("volume exceeds the exact-integer profile limit")
        return self

    def volume(self) -> int:
        return self.totalVolume if self.totalVolume is not None else (self.uplinkVolume or 0) + (self.downlinkVolume or 0)


class MultipleUnitUsage(StrictModel):
    ratingGroup: Uint32
    requestedUnit: RequestedUnit | None = None
    usedUnitContainer: list[UsedUnitContainer] = Field(default_factory=list, max_length=256)
    uPFID: UUID | None = None


class Snssai(StrictModel):
    sst: Annotated[int, Field(strict=True, ge=0, le=255)]
    sd: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{6}$")


class NetworkSlicingInfo(StrictModel):
    sNSSAI: Snssai


class PDUSessionInformation(StrictModel):
    pduSessionID: Annotated[int, Field(strict=True, ge=1, le=255)]
    dnnId: str = Field(min_length=1, max_length=100)
    networkSlicingInfo: NetworkSlicingInfo | None = None
    pduType: Literal["IPV4", "IPV6", "IPV4V6"] | None = None
    startTime: AwareDatetime | None = None
    stopTime: AwareDatetime | None = None
    sessionStopIndicator: bool | None = None


class PDUSessionChargingInformation(StrictModel):
    chargingId: Uint32 | None = None
    pduSessionInformation: PDUSessionInformation | None = None


class ChargingDataRequest(StrictModel):
    subscriberIdentifier: Supi | None = None
    chargingId: Uint32 | None = None
    nfConsumerIdentification: NFIdentification
    invocationTimeStamp: AwareDatetime
    invocationSequenceNumber: Uint32
    retransmissionIndicator: bool | None = None
    notifyUri: HttpUrl | None = None
    multipleUnitUsage: list[MultipleUnitUsage] = Field(default_factory=list, max_length=1)
    triggers: list[Trigger] = Field(default_factory=list, max_length=16)
    pDUSessionChargingInformation: PDUSessionChargingInformation | None = None

    @model_validator(mode="after")
    def consistent_charging_id(self):
        pdu = self.pDUSessionChargingInformation
        if pdu and pdu.chargingId is not None and self.chargingId is not None and pdu.chargingId != self.chargingId:
            raise ValueError("chargingId fields disagree")
        return self


class GrantedUnit(StrictModel):
    totalVolume: Volume | None = None
    serviceSpecificUnits: Volume | None = None


class FinalUnitIndication(StrictModel):
    finalUnitAction: Literal["TERMINATE"]


class MultipleUnitInformation(StrictModel):
    resultCode: str
    ratingGroup: Uint32
    grantedUnit: GrantedUnit
    validityTime: int
    volumeQuotaThreshold: Volume | None = None
    finalUnitIndication: FinalUnitIndication | None = None


class ChargingDataResponse(StrictModel):
    invocationTimeStamp: datetime
    invocationSequenceNumber: Uint32
    sessionFailover: Literal["FAILOVER_NOT_SUPPORTED"] = "FAILOVER_NOT_SUPPORTED"
    multipleUnitInformation: list[MultipleUnitInformation]


class ProblemDetails(BaseModel):
    status: int
    cause: str
    title: str
    detail: str | None = None


class AccountUpsert(StrictModel):
    supi: Supi
    quotaBytes: Annotated[int, Field(strict=True, gt=0, le=MAX_BYTES)] | None = None
    enabled: bool = True


class ReconcileRequest(StrictModel):
    confirmedConsumerStopped: Literal[True]
    reason: str = Field(min_length=10, max_length=500)


class TopupRequest(StrictModel):
    requestId: str = Field(pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')
    amountBytes: Annotated[int, Field(strict=True, gt=0, le=100_000_000)]


class ServicePolicy(StrictModel):
    dnn: str = Field(min_length=1, max_length=100, pattern=r'^[a-z0-9.-]+$')
    sst: Annotated[int, Field(strict=True, ge=0, le=255)]
    sd: str = Field(pattern=r'^[0-9a-f]{6}$')
    ratingGroup: Uint32
    mode: Literal['BYTE_QUOTA', 'ZERO_RATED', 'MESSAGE_QUOTA']
    grantBlockSize: Annotated[int, Field(strict=True, gt=0, le=MAX_BYTES)] = 100
    unitKind: Literal['MESSAGE', 'IP_PACKET'] = 'MESSAGE'


class MessageAccountUpsert(StrictModel):
    quotaMessages: Volume
