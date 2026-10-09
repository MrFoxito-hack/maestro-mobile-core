"""Desired device identity, joined to the existing UPF registry; never live IPs."""
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.upf_inventory import inventory as upf_inventory

PATH = Path(__file__).resolve().parents[1]/'catalog/terminal_inventory.json'


class Group(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    id: str = Field(pattern=r'^[a-z0-9-]+$')
    label: str = Field(min_length=1)


class Device(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    id: str = Field(pattern=r'^[a-z0-9-]+$')
    group_id: str
    kind: Literal['smartphone', 'vehicle', 'sensor']
    service: Literal['embb', 'urllc', 'miot']
    supi: str = Field(pattern=r'^imsi-\d{15}$')
    label: str = Field(min_length=1)

    @model_validator(mode='after')
    def correct_profile(self):
        if {'smartphone':'embb','vehicle':'urllc','sensor':'miot'}[self.kind]!=self.service:
            raise ValueError('Device kind and service do not match')
        return self


class Inventory(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    schema_version: Literal[1]
    version: str
    testbed: str
    groups: tuple[Group, ...]
    devices: tuple[Device, ...]

    @model_validator(mode='after')
    def unique_resources(self):
        for rows,field in [(self.groups,'id'),(self.devices,'id'),(self.devices,'supi')]:
            if len({getattr(r,field) for r in rows})!=len(rows):
                raise ValueError('Duplicate terminal inventory identity: '+field)
        groups={g.id for g in self.groups}
        if any(d.group_id not in groups for d in self.devices):
            raise ValueError('Unknown device group')
        return self


def inventory():
    result=Inventory.model_validate(json.loads(PATH.read_text(encoding='utf-8')))
    targets=upf_inventory()['targets']
    for device in result.devices:
        if sum(t['service']==device.service for t in targets)!=1:
            raise ValueError('Device service must resolve exactly one UPF')
    return result


def by_supi(supi):
    normalized=supi if supi.startswith('imsi-') else 'imsi-'+supi
    return next((d for d in inventory().devices if d.supi==normalized),None)


def public_device(device):
    target=next(t for t in upf_inventory()['targets'] if t['service']==device.service)
    # Configuration is not registration evidence. Do not emit hosts or commands.
    return {**device.model_dump(),'dnn':target['dnn'],
            'snssai':{'sst':target['sst'],'sd':target['sd']},'upf_id':target['id'],
            'observation_status':'not_queried'}
