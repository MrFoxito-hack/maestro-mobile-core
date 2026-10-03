"""Validate captured component messages against the locally verified ETSI schemas."""
from pathlib import Path

import jsonschema
import yaml
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT4


def validator(type_name):
    root = Path(__file__).resolve().parents[2] / ".work" / "charging-contract"
    registry = Registry()
    for name in ("TS32291_Nchf_ConvergedCharging.yaml", "TS29571_CommonData.yaml"):
        path = root / name
        if not path.exists():
            raise RuntimeError("Run chf/tools/fetch_contract.py before native acceptance")
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        registry = registry.with_resource("https://contract.invalid/" + name,
                                         Resource.from_contents(document, default_specification=DRAFT4))
    schema = {"$ref": "https://contract.invalid/TS32291_Nchf_ConvergedCharging.yaml#/components/schemas/" + type_name}
    return jsonschema.Draft4Validator(schema, registry=registry, format_checker=jsonschema.FormatChecker())
