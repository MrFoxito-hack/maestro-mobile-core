"""Read-only source excerpt helper for native integration review."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
sys.path.insert(0,str(ROOT/'infra/charging'))
from e2e_native import Lab
from app.core.config import get_settings

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    relative = Path(sys.argv[1])
    if relative.is_absolute() or '..' in relative.parts: raise ValueError('Relative source path required')
    settings = get_settings(); host=Lab(settings,settings.ssh_port)
    try:
        lines=host.read('/home/emsadmin/maestro-charging/open5gs/'+relative.as_posix()).decode().splitlines()
        start,end=int(sys.argv[2]),int(sys.argv[3])
        print('\n'.join(f'{i+1}: {line}' for i,line in enumerate(lines) if start<=i+1<=end))
    finally: host.client.close()
