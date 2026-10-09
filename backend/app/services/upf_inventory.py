"""Operator-owned mapping. Desired S-NSSAI is never inferred from interface names."""
import ipaddress
import json
from pathlib import Path

INVENTORY = Path(__file__).resolve().parents[1] / 'catalog' / 'upf_inventory.json'


def inventory():
    document = json.loads(INVENTORY.read_text(encoding='utf-8'))
    targets = document['targets']
    for field in ('id', 'dnn', 'pm_object_id', 'n3_address'):
        if len({t[field] for t in targets}) != len(targets):
            raise ValueError('Ambiguous UPF inventory: ' + field)
    for target in targets:
        if ipaddress.ip_address(target['gateway']) not in ipaddress.ip_network(target['pool']):
            raise ValueError('Gateway outside UE pool')
    return document


def profile(dnn):
    return next((t for t in inventory()['targets'] if t['dnn'] == dnn), None)
