"""Run one explicit command on the configured lab without retrying mutations.

Credentials remain in backend/.env. This respects EMS host-key policy; a
non-strict policy is suitable only for this isolated lab, not for production.
"""
import argparse
import sys
from pathlib import Path

import paramiko

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.core.config import get_settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--sudo", action="store_true")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("command")
    args = parser.parse_args()
    settings = get_settings()
    if args.port not in {settings.ssh_port, settings.upf_ssh_port,
                         settings.upf2_ssh_port, settings.gnb_ssh_port, settings.ue_ssh_port}:
        raise SystemExit("Port is not in the configured lab inventory")
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    if not settings.ssh_strict_host_key:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        client.connect(settings.testbed_host, port=args.port,
                       username=settings.ssh_user, password=settings.ssh_password,
                       key_filename=str(settings.ssh_key_path) if settings.ssh_key_path else None,
                       look_for_keys=False, allow_agent=False, timeout=10)
        command = args.command
        if args.sudo:
            command = "sudo -S -p '' " + command
        stdin, stdout, stderr = client.exec_command(command, timeout=args.timeout)
        if args.sudo and settings.ssh_password:
            stdin.write(settings.ssh_password + "\n")
            stdin.flush()
        stdin.channel.shutdown_write()
        channel = stdout.channel
        channel.set_combine_stderr(True)
        for line in stdout:
            print(line, end="", flush=True)
        raise SystemExit(channel.recv_exit_status())
    finally:
        client.close()


if __name__ == "__main__":
    main()
