"""Fetch immutable official ETSI artifacts; never follow live branches at runtime."""
from hashlib import sha256
from pathlib import Path
from urllib.request import Request, urlopen
from zipfile import ZipFile
import io

ROOT = Path(__file__).resolve().parents[2] / '.work' / 'nwdaf-contract'
SOURCES = [
    ('129520', '16.07.00', '160700', '7c87eca6faac44ce123401cb44ac337e99eb1c405fa9a5a18c83782f302de539',
     ('TS29520_Nnwdaf_AnalyticsInfo.yaml', 'TS29520_Nnwdaf_EventsSubscription.yaml')),
    ('129571', '16.08.00', '160800', '3a5451e8ab7adbc42d202666567731b24bc9c9e404d096c7aa22885082b62701',
     ('TS29571_CommonData.yaml',)),
    ('129517', '16.05.00', '160500', '96a4818ae87ff2d1c9becf46f16aa882700d7824a3c977f68630d9d12b75b360',
     ('TS29517_Naf_EventExposure.yaml',)),
]


def verify(root=ROOT, download=False):
    schemas = {}
    for spec, directory, version, digest, names in SOURCES:
        filename = f'ts_{spec}v{version}p0.zip'
        archive = root / filename
        if not archive.exists() and download:
            url = f'https://www.etsi.org/deliver/etsi_ts/129500_129599/{spec}/{directory}_60/{filename}'
            with urlopen(Request(url, headers={'User-Agent': 'MAEstro-contract-verifier'}), timeout=30) as response:
                data = response.read(4 * 1024 * 1024)
            if sha256(data).hexdigest() != digest:
                raise ValueError(f'Untrusted artifact: {filename}')
            root.mkdir(parents=True, exist_ok=True)
            archive.write_bytes(data)
        data = archive.read_bytes()
        if sha256(data).hexdigest() != digest:
            raise ValueError(f'Contract hash mismatch: {filename}')
        with ZipFile(io.BytesIO(data)) as zip_file:
            for name in names:
                schemas[name] = zip_file.read(name)
                destination = root / 'schema' / name
                if download:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if destination.exists() and destination.read_bytes() != schemas[name]:
                        raise ValueError(f'Local schema modified: {name}')
                    destination.write_bytes(schemas[name])
                elif destination.read_bytes() != schemas[name]:
                    raise ValueError(f'Local schema modified: {name}')
    return schemas


if __name__ == '__main__':
    print('Verified:', ', '.join(verify(download=True)))
