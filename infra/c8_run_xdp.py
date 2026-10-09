"""Balanced AB/BA C8 blocks under one v7 process and PDU session."""
import json
import random
import subprocess
import sys
import time
from c8_remote import ROOT


def main():
    def call(script,*args):
        subprocess.run([sys.executable,str(ROOT/'infra'/script),*args],cwd=ROOT/'backend',check=True)
    order=[['kernel','xdp'],['xdp','kernel']]*3
    random.Random(42017).shuffle(order)
    out=ROOT/'.work/c8-campaign/setup/xdp-order.json'
    out.write_text(json.dumps({'seed':42017,'blocks':order},indent=2))
    try:
        call('c8_xdp_window.py','prepare')
        time.sleep(8)
        for block,modes in enumerate(order):
            for mode in modes:
                call('c8_xdp_window.py',mode)
                call('c8_acquire.py','--experiment','urllc','--mode',mode,'--blocks','1',
                     '--block-start',str(block),'--duration','10','--seed',str(42017+block))
                call('c8_xdp_window.py','check')
    finally:
        if (ROOT/'.work/c8-campaign/setup/xdp-window.json').exists():
            try:
                call('c8_xdp_window.py','kernel')
            finally:
                call('c8_xdp_window.py','restore')


if __name__=='__main__':main()
