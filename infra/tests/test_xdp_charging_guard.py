import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'upf_xdp'))
from upf_xdp_agent import require_uncharged_upf


def test_active_charging_upf_cannot_use_unsynchronized_redirect(tmp_path):
    process = tmp_path / '123'
    process.mkdir()
    config = tmp_path / 'private-upf.yaml'
    config.write_text('upf:\n  charging_enforcement: true\n')
    (process/'comm').write_text('open5gs-upfd\n')
    (process/'cmdline').write_bytes(('open5gs-upfd\0-c\0'+str(config)+'\0').encode())
    with pytest.raises(ValueError, match='synchronization is not implemented'):
        require_uncharged_upf(tmp_path)


def test_unknown_upf_cannot_enable_redirect(tmp_path):
    with pytest.raises(ValueError, match='No running UPF'):
        require_uncharged_upf(tmp_path)
