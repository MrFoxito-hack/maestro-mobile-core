"""Offline validation against unchanged, SHA256-pinned OAS 3.0 source schemas.

Only the implemented payload subset is exercised. Not a full TS conformance claim.
Unresolved references fail closed: network resolution is forbidden.
"""
import yaml
from jsonschema import FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT4
from openapi_schema_validator import OAS30Validator
from tools.fetch_contract import verify

INFO = 'TS29520_Nnwdaf_AnalyticsInfo.yaml'
EVENTS = 'TS29520_Nnwdaf_EventsSubscription.yaml'


class Contract:
    def __init__(self):
        self.documents = {name: yaml.safe_load(data) for name, data in verify().items()}
        self.base = 'https://contract.invalid/'
        self.registry = Registry().with_resources(
            (self.base + name, Resource.from_contents(doc, default_specification=DRAFT4))
            for name, doc in self.documents.items())

    def validate(self, name, payload, file=INFO):
        schema = {'$ref': self.base + file + '#/components/schemas/' + name}
        OAS30Validator(schema, registry=self.registry, format_checker=FormatChecker()).validate(payload)
