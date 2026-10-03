import json
import shlex
import re
from datetime import datetime, timezone
from copy import deepcopy

from pymongo import MongoClient

from app.core.config import get_settings
from app.models import SubscriberCreate, SubscriberUpdate
from app.services.ue_observation import SUBSCRIBER_STATUS_SCRIPT, observed_nodes, parse_status, terminal_label, unknown_status


def mask_secret(value: str | None) -> str | None:
    return f"{value[:4]}{'•' * 24}{value[-4:]}" if value else None


class SubscriberService:
    def __init__(self) -> None:
        self.memory: dict[str, dict] = {}

    def _remote_mongosh(self, js_code: str, *, mutating: bool = False) -> str:
        from app.services.scenarios import scenario_manager
        from app.services.execution import RemoteExecutionAdapter
        adapter = scenario_manager.adapter
        if isinstance(adapter, RemoteExecutionAdapter):
            cmd = shlex.join(['mongosh', get_settings().mongo_database, '--quiet', '--eval', js_code])
            return adapter._execute_sync(cmd, retries=1 if mutating else 3)
        raise RuntimeError("Adaptador MongoDB remoto no disponible")

    def _collection(self):
        settings = get_settings()
        if not settings.enable_mongo:
            return None
        return MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=2000)[settings.mongo_database]["subscribers"]

    def _document(self, item: SubscriberCreate) -> dict:
        session = {"name": item.apn_dnn, "type": 3,
                   "qos": {"index": 9, "arp": {"priority_level": 8,
                            "pre_emption_capability": 1, "pre_emption_vulnerability": 2}},
                   "ambr": {"downlink": {"value": 1, "unit": 3},
                            "uplink": {"value": 1, "unit": 3}}, "pcc_rule": []}
        slice_data = {"sst": item.sst, "default_indicator": True, "session": [session]}
        if item.sd:
            slice_data["sd"] = item.sd
        return {
            "imsi": item.imsi,
            "subscriber_status": 0,
            "network_access_mode": 0,
            "security": {"k": item.key, "opc": item.opc, "amf": item.amf},
            "slice": [slice_data],
        }

    def _public(self, document: dict) -> dict:
        result = deepcopy(document)
        result.pop("_id", None)
        result["terminal_label"] = terminal_label(str(result.get("imsi", "")))
        if "security" in result:
            result["security"]["k"] = mask_secret(result["security"].get("k"))
            result["security"]["opc"] = mask_secret(result["security"].get("opc"))
        return result

    def get_live_status(self, target_imsi: str | None = None, *, imsis: list[str] | None = None) -> dict[str, dict]:
        settings = get_settings()
        requested = imsis or ([target_imsi] if target_imsi else [])
        statuses = {imsi: unknown_status() for imsi in requested}
        if target_imsi and not re.fullmatch(r'\d{14,15}', target_imsi):
            raise ValueError('IMSI inválido')
        if settings.execution_mode != "remote":
            return statuses
        from app.services.scenarios import scenario_manager
        from app.services.execution import RemoteExecutionAdapter
        adapter = scenario_manager.adapter
        if not isinstance(adapter, RemoteExecutionAdapter):
            return statuses
        command = ['python3', '-c', SUBSCRIBER_STATUS_SCRIPT]
        if target_imsi:
            command.append('imsi-' + target_imsi)
        try:
            raw = adapter._execute_sync(shlex.join(command), port=settings.ue_ssh_port, retries=1)
            payload = json.loads(raw)
            nodes = observed_nodes(payload['nodes'])
            native = payload['native']
            if not isinstance(native, dict):
                return statuses
        except Exception:
            return statuses
        observed_at = datetime.now(timezone.utc).isoformat()
        statuses = {imsi: {**unknown_status('not_observed', available=False), 'observed_at': observed_at}
                    for imsi in requested}
        for node in nodes:
            imsi = node.removeprefix('imsi-')
            if target_imsi and imsi != target_imsi:
                continue
            observation = native.get(node)
            statuses[imsi] = parse_status(observation) if isinstance(observation, dict) else unknown_status(available=True)
            statuses[imsi]['observed_at'] = observed_at
        return statuses

    def list(self) -> list[dict]:
        settings = get_settings()
        documents = []
        if settings.execution_mode == "remote":
            try:
                js = "JSON.stringify(db.subscribers.find().toArray())"
                raw = self._remote_mongosh(js)
                if raw.strip():
                    documents = json.loads(raw.strip())
            except Exception:
                pass
        if not documents:
            collection = self._collection()
            documents = list(collection.find({})) if collection is not None else list(self.memory.values())

        public_docs = [self._public(doc) for doc in documents]
        live_statuses = self.get_live_status(imsis=[str(doc["imsi"]) for doc in public_docs])
        for doc in public_docs:
            imsi = doc.get("imsi")
            doc["live_status"] = live_statuses.get(imsi) or unknown_status()
        return public_docs

    def create(self, item: SubscriberCreate) -> dict:
        document = self._document(item)
        settings = get_settings()
        if settings.execution_mode == "remote":
            try:
                check_js = f'db.subscribers.countDocuments({{imsi: "{item.imsi}"}})'
                count = int(self._remote_mongosh(check_js).strip() or "0")
                if count > 0:
                    raise ValueError("El IMSI ya existe")
                doc_json = json.dumps(document)
                insert_js = f"db.subscribers.insertOne({doc_json}).acknowledged"
                if self._remote_mongosh(insert_js, mutating=True).strip() != "true":
                    raise RuntimeError("Inserción MongoDB no confirmada")
                res = self._public(document)
                res["live_status"] = unknown_status()
                return res
            except ValueError:
                raise
            except Exception as exc:
                raise RuntimeError("Error creando suscriptor en MongoDB remoto; inserción no confirmada") from exc

        collection = self._collection()
        if collection is not None:
            if collection.find_one({"imsi": item.imsi}):
                raise ValueError("El IMSI ya existe")
            collection.insert_one(document)
        else:
            if item.imsi in self.memory:
                raise ValueError("El IMSI ya existe")
            self.memory[item.imsi] = document
        res = self._public(document)
        res["live_status"] = unknown_status()
        return res

    def update(self, imsi: str, item: SubscriberUpdate) -> dict:
        settings = get_settings()
        # Update only requested paths so concurrent provisioning and BSON IDs
        # on other slices are preserved by MongoDB's atomic document update.
        paths = {"key": "security.k", "opc": "security.opc", "amf": "security.amf",
                 "sst": "slice.0.sst", "sd": "slice.0.sd", "apn_dnn": "slice.0.session.0.name"}
        updates = {paths[key]: value for key, value in item.model_dump(exclude_none=True).items()}
        if item.apn_dnn:
            updates["slice.0.session.0.type"] = 3
        if settings.execution_mode == "remote":
            check_js = f'db.subscribers.findOne({{imsi: "{imsi}"}})'
            existing_raw = self._remote_mongosh(f'JSON.stringify({check_js})').strip()
            if not existing_raw or existing_raw == "null":
                raise KeyError("Suscriptor no encontrado")
            doc = json.loads(existing_raw)

            if item.key or item.opc or item.amf:
                sec = doc.get("security", {})
                if item.key: sec["k"] = item.key
                if item.opc: sec["opc"] = item.opc
                if item.amf: sec["amf"] = item.amf
                doc["security"] = sec

            if item.sst is not None or item.sd is not None or item.apn_dnn:
                slices = doc.get("slice", [{}])
                if not slices: slices = [{}]
                first_slice = slices[0]
                if item.sst is not None: first_slice["sst"] = item.sst
                if item.sd is not None: first_slice["sd"] = item.sd
                if item.apn_dnn:
                    sessions = first_slice.get("session", [{}])
                    if not sessions: sessions = [{}]
                    sessions[0]["name"] = item.apn_dnn
                    sessions[0]["type"] = 3
                    first_slice["session"] = sessions
                doc["slice"] = slices

            doc.pop("_id", None)
            update_js = f'db.subscribers.updateOne({json.dumps({"imsi": imsi})}, {json.dumps({"$set": updates})}).matchedCount'
            if int(self._remote_mongosh(update_js, mutating=True).strip() or "0") != 1:
                raise KeyError("Suscriptor no encontrado")
            res = self._public(doc)
            live = self.get_live_status(imsi).get(imsi)
            res["live_status"] = live or unknown_status()
            return res

        collection = self._collection()
        doc = collection.find_one({"imsi": imsi}) if collection is not None else self.memory.get(imsi)
        if not doc:
            raise KeyError("Suscriptor no encontrado")
        doc = deepcopy(doc)
        if item.key or item.opc or item.amf:
            sec = doc.get("security", {})
            if item.key: sec["k"] = item.key
            if item.opc: sec["opc"] = item.opc
            if item.amf: sec["amf"] = item.amf
            doc["security"] = sec
        if item.sst is not None or item.sd is not None or item.apn_dnn:
            slices = doc.get("slice", [{}])
            if not slices: slices = [{}]
            first_slice = slices[0]
            if item.sst is not None: first_slice["sst"] = item.sst
            if item.sd is not None: first_slice["sd"] = item.sd
            if item.apn_dnn:
                sessions = first_slice.get("session", [{}])
                if not sessions: sessions = [{}]
                sessions[0]["name"] = item.apn_dnn
                sessions[0]["type"] = 3
                first_slice["session"] = sessions
            doc["slice"] = slices
        if collection is not None:
            if collection.update_one({"imsi": imsi}, {"$set": updates}).matched_count != 1:
                raise KeyError("Suscriptor no encontrado")
        else:
            self.memory[imsi] = doc
        res = self._public(doc)
        res["live_status"] = unknown_status()
        return res

    def delete(self, imsi: str) -> bool:
        settings = get_settings()
        if settings.execution_mode == "remote":
            try:
                del_js = f'db.subscribers.deleteOne({{imsi: "{imsi}"}}).deletedCount'
                count = int(self._remote_mongosh(del_js, mutating=True).strip() or "0")
                return count > 0
            except Exception:
                return False

        collection = self._collection()
        if collection is not None:
            return collection.delete_one({"imsi": imsi}).deleted_count == 1
        return self.memory.pop(imsi, None) is not None


subscriber_service = SubscriberService()
