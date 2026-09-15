from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class NFIdentification(StrictModel):
    nFName: str | None = None
    nFIPv4Address: str | None = None
    nFIPv6Address: str | None = None
    nFFqdn: str | None = None
    nodeFunctionality: str


class RequestedUnit(StrictModel):
    time: int | None = Field(default=None, ge=0)
    totalVolume: int | None = Field(default=None, ge=0)
    uplinkVolume: int | None = Field(default=None, ge=0)
    downlinkVolume: int | None = Field(default=None, ge=0)
    serviceSpecificUnits: int | None = Field(default=None, ge=0)


class UsedUnitContainer(StrictModel):
    localSequenceNumber: int = Field(ge=0)
    time: int | None = Field(default=None, ge=0)
    totalVolume: int | None = Field(default=None, ge=0)
    uplinkVolume: int | None = Field(default=None, ge=0)
    downlinkVolume: int | None = Field(default=None, ge=0)

    def volume(self) -> int:
        # TS 32.291 permits total and directional counters in the same
        # container. totalVolume already represents UL+DL and must not be
        # added to those values a second time.
        if self.totalVolume is not None:
            return self.totalVolume
        return (self.uplinkVolume or 0) + (self.downlinkVolume or 0)


class MultipleUnitUsage(StrictModel):
    ratingGroup: int = Field(ge=0)
    requestedUnit: RequestedUnit | None = None
    usedUnitContainer: list[UsedUnitContainer] = Field(default_factory=list)
    uPFID: str | None = None


class ChargingDataRequest(StrictModel):
    subscriberIdentifier: str | None = None
    chargingId: int | None = Field(default=None, ge=0)
    nfConsumerIdentification: NFIdentification
    invocationTimeStamp: datetime
    invocationSequenceNumber: int = Field(ge=0)
    retransmissionIndicator: bool | None = None
    notifyUri: str | None = None
    multipleUnitUsage: list[MultipleUnitUsage] = Field(default_factory=list)
    pDUSessionChargingInformation: dict | None = None

    @field_validator("subscriberIdentifier")
    @classmethod
    def validate_supi(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("imsi-"):
            raise ValueError("the online-charging profile requires an imsi- SUPI")
        return value


class GrantedUnit(StrictModel):
    time: int | None = None
    totalVolume: int | None = None
    uplinkVolume: int | None = None
    downlinkVolume: int | None = None


class FinalUnitIndication(StrictModel):
    finalUnitAction: Literal["TERMINATE", "REDIRECT", "RESTRICT_ACCESS"]


class MultipleUnitInformation(StrictModel):
    resultCode: str | None = None
    ratingGroup: int
    grantedUnit: GrantedUnit | None = None
    validityTime: int | None = None
    volumeQuotaThreshold: int | None = None
    finalUnitIndication: FinalUnitIndication | None = None


class ChargingDataResponse(StrictModel):
    invocationTimeStamp: datetime
    invocationSequenceNumber: int
    sessionFailover: Literal["FAILOVER_NOT_SUPPORTED", "FAILOVER_SUPPORTED"] | None = None
    multipleUnitInformation: list[MultipleUnitInformation] = Field(default_factory=list)


class ProblemDetails(BaseModel):
    status: int
    cause: str
    title: str
    detail: str | None = None


class AccountUpsert(StrictModel):
    supi: str
    quotaBytes: int = Field(gt=0)
    enabled: bool = True

    @field_validator("supi")
    @classmethod
    def validate_supi(cls, value: str) -> str:
        if not value.startswith("imsi-"):
            raise ValueError("supi must use the imsi- prefix")
        return value

