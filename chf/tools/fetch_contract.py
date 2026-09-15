"""Download unchanged ETSI electronic attachments, verify SHA256, extract only schemas.

The standards are not republished in this repository. Requires network only at
setup; contract tests read the verified local files offline thereafter.
"""
import hashlib
import io
import urllib.request
import zipfile
from pathlib import Path

SOURCES = [
    ('https://www.etsi.org/deliver/etsi_ts/132200_132299/132291/16.17.00_60/ts_132291v161700p0.zip',
     '357654667583e5c069b6b40c295461b1918e2576cec0af2df71eb2a43bbc480a', 'TS32291_Nchf_ConvergedCharging.yaml'),
    ('https://www.etsi.org/deliver/etsi_ts/129500_129599/129571/16.13.00_60/ts_129571v161300p0.zip',
     '40bdd4b23bf057853a093567643e1b3280a3c38a92500e4078bb7845e6084e90', 'TS29571_CommonData.yaml'),
]
DESTINATION = Path(__file__).resolve().parents[2] / '.work' / 'charging-contract'


def main():
    DESTINATION.mkdir(parents=True, exist_ok=True)
    for url, expected, filename in SOURCES:
        cache = DESTINATION.parent / url.rsplit('/', 1)[1]
        if cache.exists():
            archive = cache.read_bytes()
        else:
            request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 MAEstro-contract-check'})
            with urllib.request.urlopen(request, timeout=30) as response:
                archive = response.read(2 * 1024 * 1024)
        if hashlib.sha256(archive).hexdigest() != expected:
            raise SystemExit('Official attachment hash changed; review before accepting')
        with zipfile.ZipFile(io.BytesIO(archive)) as source:
            content = source.read(filename)
        target = DESTINATION / filename
        if target.exists() and target.read_bytes() != content:
            raise SystemExit(f'{filename} differs locally; refusing to overwrite')
        target.write_bytes(content)
        print(f'{filename}: verified')


if __name__ == '__main__':
    main()
