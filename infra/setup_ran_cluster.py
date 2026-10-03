import io
import time
import paramiko

CORE_HOST = "127.0.0.1"
CORE_PORT = 2222

GNB_HOST = "127.0.0.1"
GNB_PORT = 2225

UE_HOST = "127.0.0.1"
UE_PORT = 2226

SSH_USER = "emsadmin"
SSH_PASS = "1506"

def run_cmd(client, cmd, use_sudo=True):
    if use_sudo:
        full_cmd = f"printf '%s\\n' '{SSH_PASS}' | sudo -S bash -c {cmd!r}"
    else:
        full_cmd = cmd
    stdin, stdout, stderr = client.exec_command(full_cmd)
    out = stdout.read().decode("utf-8", errors="replace").strip()
    err = stderr.read().decode("utf-8", errors="replace").strip()
    return out, err

def get_client(port):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect("127.0.0.1", port=port, username=SSH_USER, password=SSH_PASS, timeout=15, banner_timeout=30)
    return client

def step1_configure_core():
    print("\n--- STEP 1: Configuring AMF on Core VM (Port 2222) ---")
    c = get_client(CORE_PORT)
    sftp = c.open_sftp()
    
    # 1. Update /etc/open5gs/amf.yaml to include 10.210.50.1
    with sftp.file("/etc/open5gs/amf.yaml", "r") as f:
        content = f.read().decode("utf-8")
    
    # Check if 10.210.50.1 is already present in amf.yaml
    if "10.210.50.1" not in content:
        print("Adding 10.210.50.1 to amf.yaml ngap server list...")
        target = "  ngap:\n    server:\n      - address: 127.0.0.5"
        replacement = "  ngap:\n    server:\n      - address: 127.0.0.5\n      - address: 10.210.50.1"
        if target in content:
            new_content = content.replace(target, replacement, 1)
        else:
            new_content = content.replace("      - address: 127.0.0.5", "      - address: 127.0.0.5\n      - address: 10.210.50.1", 1)
        with sftp.file("/tmp/amf.yaml", "w") as f:
            f.write(new_content)
        run_cmd(c, "mv /tmp/amf.yaml /etc/open5gs/amf.yaml")
    else:
        print("10.210.50.1 already in amf.yaml.")

    # 2. Stop and disable local UERANSIM on Core VM so it doesn't conflict
    print("Disabling local ueransim-gnb and ueransim-ue on Core VM...")
    run_cmd(c, "systemctl disable --now ueransim-gnb ueransim-ue || true")

    # 3. Restart AMF
    print("Restarting open5gs-amfd...")
    run_cmd(c, "systemctl restart open5gs-amfd")
    time.sleep(2)
    
    # Check SCTP listening sockets
    out, _ = run_cmd(c, "ss -lnp --sctp")
    print(f"Core SCTP Sockets:\n{out}")

    # 4. Download UERANSIM package from Core VM into memory buffer
    print("Reading /tmp/ueransim.tar.gz from Core VM...")
    buffer = io.BytesIO()
    sftp.getfo("/tmp/ueransim.tar.gz", buffer)
    buffer.seek(0)
    data = buffer.read()
    print(f"Downloaded ueransim.tar.gz ({len(data)} bytes).")
    sftp.close()
    c.close()
    return data

GNB_CONFIG = """mcc: '999'          # Mobile Country Code value
mnc: '70'           # Mobile Network Code value (2 or 3 digits)

nci: '0x000000010'  # NR Cell Identity (36-bit)
idLength: 32        # NR gNB ID length in bits [22...32]
tac: 1              # Tracking Area Code

linkIp: 10.210.50.10   # gNB's local IP address for Radio Link Simulation
ngapIp: 10.210.50.10   # gNB's local IP address for N2 Interface (to AMF)
gtpIp: 10.210.50.10    # gNB's local IP address for N3 Interface (to UPF)

# List of AMF address information
amfConfigs:
  - address: 10.210.50.1
    port: 38412

# List of supported S-NSSAIs by this gNB
slices:
  - sst: 1

# Indicates whether or not SCTP stream number errors should be ignored.
ignoreStreamIds: true

cellAccessType: nr
"""

GNB_SERVICE = """[Unit]
Description=UERANSIM gNodeB Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/home/emsadmin/UERANSIM
ExecStart=/home/emsadmin/UERANSIM/build/nr-gnb -c /home/emsadmin/UERANSIM/config/open5gs-gnb.yaml
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
"""

def step2_configure_gnb(ueransim_data):
    print("\n--- STEP 2: Configuring EMS-GNB-01 (Port 2225) ---")
    c = get_client(GNB_PORT)
    sftp = c.open_sftp()

    # 1. Clean up UPF remnants on GNB VM
    print("Disabling UPF remnants on GNB VM...")
    run_cmd(c, "systemctl disable --now open5gs-upfd || true")
    run_cmd(c, "rm -f /etc/systemd/network/99-open5gs.netdev /etc/systemd/network/99-open5gs.network || true")
    run_cmd(c, "ip link del ogstun || true")
    run_cmd(c, "iptables -t nat -F POSTROUTING || true")

    # 2. Hostname & Netplan for 10.210.50.10
    netplan = """network:
  version: 2
  renderer: networkd
  ethernets:
    enp0s8:
      addresses:
        - 10.210.50.10/24
"""
    with sftp.file("/tmp/01-netcfg.yaml", "w") as f:
        f.write(netplan)
    run_cmd(c, "hostnamectl set-hostname gnb-01.ems.test")
    run_cmd(c, "mv /tmp/01-netcfg.yaml /etc/netplan/01-netcfg.yaml && netplan apply")

    # 3. Upload UERANSIM tarball & extract
    print("Uploading UERANSIM package to GNB VM...")
    with sftp.file("/tmp/ueransim.tar.gz", "wb") as f:
        f.write(ueransim_data)
    run_cmd(c, "tar -xzf /tmp/ueransim.tar.gz -C /home/emsadmin")
    run_cmd(c, "chown -R emsadmin:emsadmin /home/emsadmin/UERANSIM")

    # 4. Write gNB config
    with sftp.file("/home/emsadmin/UERANSIM/config/open5gs-gnb.yaml", "w") as f:
        f.write(GNB_CONFIG)

    # 5. Setup systemd service
    with sftp.file("/tmp/ueransim-gnb.service", "w") as f:
        f.write(GNB_SERVICE)
    run_cmd(c, "mv /tmp/ueransim-gnb.service /etc/systemd/system/ueransim-gnb.service")
    run_cmd(c, "systemctl daemon-reload")
    run_cmd(c, "systemctl enable --now ueransim-gnb")
    time.sleep(3)

    out, _ = run_cmd(c, "systemctl is-active ueransim-gnb")
    print(f"ueransim-gnb status: {out}")
    out, _ = run_cmd(c, "ip -br a")
    print(f"GNB interfaces:\n{out}")

    sftp.close()
    c.close()

UE_CONFIG = """supi: 'imsi-999700000000001'
mcc: '999'
mnc: '70'
protectionScheme: 0
homeNetworkPublicKey: '5a8d38864820197c3394b92613b20b91633cbd897119273bf8e4a6f4eec0a650'
homeNetworkPublicKeyId: 1
routingIndicator: '0000'

key: '465B5CE8B199B49FAA5F0A2EE238A6BC'
op: 'E8ED289DEBA952E4283B54E88E6183CA'
opType: 'OPC'
amf: '8000'
imei: '356938035643803'
imeiSv: '4370816125816151'

tunNetmask: '255.255.255.0'
useNamespace: false

gnbSearchList:
  - 10.210.50.10

uacAic:
  mps: false
  mcs: false

uacAcc:
  normalClass: 0
  class11: false
  class12: false
  class13: false
  class14: false
  class15: false

sessions:
  - type: 'IPv4'
    apn: 'internet'
    slice:
      sst: 1

configured-nssai:
  - sst: 1

default-nssai:
  - sst: 1
    sd: 1

integrity:
  IA1: true
  IA2: true
  IA3: true

ciphering:
  EA1: true
  EA2: true
  EA3: true

integrityMaxRate:
  uplink: 'full'
  downlink: 'full'
"""

UE_SERVICE = """[Unit]
Description=UERANSIM UE Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/home/emsadmin/UERANSIM
ExecStart=/home/emsadmin/UERANSIM/build/nr-ue -c /home/emsadmin/UERANSIM/config/open5gs-ue.yaml
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
"""

def step3_configure_ue(ueransim_data):
    print("\n--- STEP 3: Configuring EMS-UE-01 (Port 2226) ---")
    c = get_client(UE_PORT)
    sftp = c.open_sftp()

    # 1. Clean up UPF remnants on UE VM
    print("Disabling UPF remnants on UE VM...")
    run_cmd(c, "systemctl disable --now open5gs-upfd || true")
    run_cmd(c, "rm -f /etc/systemd/network/99-open5gs.netdev /etc/systemd/network/99-open5gs.network || true")
    run_cmd(c, "ip link del ogstun || true")
    run_cmd(c, "iptables -t nat -F POSTROUTING || true")

    # 2. Hostname & Netplan for 10.210.50.11
    netplan = """network:
  version: 2
  renderer: networkd
  ethernets:
    enp0s8:
      addresses:
        - 10.210.50.11/24
"""
    with sftp.file("/tmp/01-netcfg.yaml", "w") as f:
        f.write(netplan)
    run_cmd(c, "hostnamectl set-hostname ue-01.ems.test")
    run_cmd(c, "mv /tmp/01-netcfg.yaml /etc/netplan/01-netcfg.yaml && netplan apply")

    # 3. Upload UERANSIM tarball & extract
    print("Uploading UERANSIM package to UE VM...")
    with sftp.file("/tmp/ueransim.tar.gz", "wb") as f:
        f.write(ueransim_data)
    run_cmd(c, "tar -xzf /tmp/ueransim.tar.gz -C /home/emsadmin")
    run_cmd(c, "chown -R emsadmin:emsadmin /home/emsadmin/UERANSIM")

    # 4. Write UE config
    with sftp.file("/home/emsadmin/UERANSIM/config/open5gs-ue.yaml", "w") as f:
        f.write(UE_CONFIG)

    # 5. Setup systemd service
    with sftp.file("/tmp/ueransim-ue.service", "w") as f:
        f.write(UE_SERVICE)
    run_cmd(c, "mv /tmp/ueransim-ue.service /etc/systemd/system/ueransim-ue.service")
    run_cmd(c, "systemctl daemon-reload")
    run_cmd(c, "systemctl enable --now ueransim-ue")
    time.sleep(3)

    out, _ = run_cmd(c, "systemctl is-active ueransim-ue")
    print(f"ueransim-ue status: {out}")
    out, _ = run_cmd(c, "ip -br a")
    print(f"UE interfaces:\n{out}")

    sftp.close()
    c.close()

if __name__ == "__main__":
    ueransim_tar = step1_configure_core()
    step2_configure_gnb(ueransim_tar)
    step3_configure_ue(ueransim_tar)
    print("\n=== All nodes provisioned and configured successfully! ===")
