"""Resume sequential startup; set process affinity by supported inheritance."""
import ctypes
import json
import logging
from pathlib import Path
import subprocess
import time
from c8_2vcpu_host import OUT, VMS, info, command
from c8_remote import LoggedLab, get_settings

MASKS={'EMS-Testbed-4G5G':1,'EMS-UPF-01':20,'EMS-UPF-02':64,'EMS-GNB-01':1280,'EMS-UE-01':20480}


def start(vm):
    command('modifyvm',vm,'--vm-process-priority','high')
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.GetCurrentProcess.restype=ctypes.c_void_p
    kernel.GetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_size_t),ctypes.POINTER(ctypes.c_size_t)]
    kernel.SetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
    handle=kernel.GetCurrentProcess();old=ctypes.c_size_t();system=ctypes.c_size_t()
    assert kernel.GetProcessAffinityMask(handle,ctypes.byref(old),ctypes.byref(system))
    assert kernel.SetProcessAffinityMask(handle,MASKS[vm])
    try:
        with (OUT/(vm+'-pinned-launch.log')).open('ab') as log:
            process=subprocess.Popen([r'C:\Program Files\Oracle\VirtualBox\VBoxHeadless.exe','--startvm',vm],
                 stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                 creationflags=subprocess.CREATE_NO_WINDOW|subprocess.HIGH_PRIORITY_CLASS)
        print(json.dumps({'started':vm,'launcher_pid':process.pid,'inherited_mask':MASKS[vm]}),flush=True)
    finally:
        assert kernel.SetProcessAffinityMask(handle,old.value)


def main():
    logging.getLogger('paramiko').setLevel(logging.CRITICAL)
    record={}
    for vm,port in VMS:
        if info(vm)['VMState']=='"poweroff"':start(vm)
        deadline=time.monotonic()+360
        last_retry=0
        while True:
            try:
                host=LoggedLab(get_settings(),port,OUT/'boot-recovery')
                try:online=int(host.run(['getconf','_NPROCESSORS_ONLN']).strip())
                finally:host.client.close()
                record[vm]={'online_cpus':online,'ssh':True};break
            except Exception as exc:
                if time.monotonic()>deadline:raise
                if time.monotonic()-last_retry>60:
                    print(json.dumps({'waiting':vm,'reason':type(exc).__name__}),flush=True);last_retry=time.monotonic()
                time.sleep(2)
        print(json.dumps({'vm':vm,**record[vm]}),flush=True)
        (OUT/'boot-recovery.json').write_text(json.dumps(record,indent=2)+'\n')


if __name__=='__main__':main()
