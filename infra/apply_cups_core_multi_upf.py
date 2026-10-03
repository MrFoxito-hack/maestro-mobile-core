import sys
import time
import paramiko

CORE_HOST = "127.0.0.1"
CORE_PORT = 2222
CORE_USER = "emsadmin"
CORE_PASS = "1506"

def run_ssh(host, port, user, password, cmd, sudo=False):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, port=port, username=user, password=password, timeout=15)
    try:
        if sudo:
            full_cmd = f"printf '%s\\n' '{password}' | sudo -S bash -c {cmd!r}"
        else:
            full_cmd = cmd
        stdin, stdout, stderr = client.exec_command(full_cmd, timeout=30)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        status = stdout.channel.recv_exit_status()
        return status, out, err
    finally:
        client.close()

def main():
    print("=== Step 1: Update SMF config for Dual UPF on Core VM ===")
    status, smf_content, _ = run_ssh(CORE_HOST, CORE_PORT, CORE_USER, CORE_PASS, "cat /etc/open5gs/smf.yaml")
    if status != 0:
        print("Error reading /etc/open5gs/smf.yaml")
        sys.exit(1)

    # Clean existing client upf block in SMF
    # Target structure:
    #   pfcp:
    #     server:
    #       - address: 10.210.50.1
    #       - address: 127.0.0.4
    #     client:
    #       upf:
    #         - address: 10.210.50.8
    #           dnn: internet
    #         - address: 10.210.50.9
    #           dnn: corporate
    #   session:
    #     - subnet: 10.45.0.0/16
    #       gateway: 10.45.0.1
    #       dnn: internet
    #     - subnet: 2001:db8:cafe::/48
    #       gateway: 2001:db8:cafe::1
    #       dnn: internet
    #     - subnet: 10.46.0.0/16
    #       gateway: 10.46.0.1
    #       dnn: corporate

    # Upload clean updated smf.yaml
    # We construct the updated file:
    import re
    # Replace pfcp client section
    pfcp_replacement = """  pfcp:
    server:
      - address: 10.210.50.1
      - address: 127.0.0.4
    client:
      upf:
        - address: 10.210.50.8
          dnn: internet
        - address: 10.210.50.9
          dnn: corporate"""
    
    # We replace from "  pfcp:" until the next top-level or section
    pattern_pfcp = r"  pfcp:[\s\S]*?(?=  gtpc:)"
    smf_content = re.sub(pattern_pfcp, pfcp_replacement + "\n", smf_content)

    # Replace session section
    session_replacement = """  session:
    - subnet: 10.45.0.0/16
      gateway: 10.45.0.1
      dnn: internet
    - subnet: 2001:db8:cafe::/48
      gateway: 2001:db8:cafe::1
      dnn: internet
    - subnet: 10.46.0.0/16
      gateway: 10.46.0.1
      dnn: corporate"""

    pattern_session = r"  session:[\s\S]*?(?=  dns:)"
    smf_content = re.sub(pattern_session, session_replacement + "\n", smf_content)

    # Write smf.yaml to Core VM
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(CORE_HOST, port=CORE_PORT, username=CORE_USER, password=CORE_PASS, timeout=15)
    sftp = client.open_sftp()
    with sftp.file("/tmp/smf.yaml", "w") as f:
        f.write(smf_content)
    sftp.close()
    client.close()
    run_ssh(CORE_HOST, CORE_PORT, CORE_USER, CORE_PASS, "cp /tmp/smf.yaml /etc/open5gs/smf.yaml", sudo=True)
    print("smf.yaml successfully updated with dual UPFs (internet + corporate)!")

    print("=== Step 2: Authorize 'corporate' DNN in MongoDB ===")
    mongo_script = """
    db = db.getSiblingDB('open5gs');
    var sub = db.subscribers.findOne({ imsi: '999700000000001' });
    if (sub) {
        var sessions = sub.slice[0].session;
        var hasCorp = sessions.some(function(s) { return s.name === 'corporate'; });
        if (!hasCorp) {
            sessions.push({
                name: 'corporate',
                type: 3,
                qos: {
                    index: 9,
                    arp: { priority_level: 8, pre_emption_capability: 1, pre_emption_vulnerability: 2 }
                },
                ambr: {
                    downlink: { value: 1000000000, unit: 0 },
                    uplink: { value: 1000000000, unit: 0 }
                },
                pcc_rule: []
            });
            db.subscribers.updateOne({ imsi: '999700000000001' }, { $set: { 'slice.0.session': sessions } });
            print('Added corporate session to subscriber in MongoDB');
        } else {
            print('Corporate session already exists in MongoDB');
        }
    }
    """
    cmd_mongo = f"mongosh --eval {mongo_script!r}"
    st, out, err = run_ssh(CORE_HOST, CORE_PORT, CORE_USER, CORE_PASS, cmd_mongo)
    print("MongoDB update:", out.strip())

    print("=== Step 3: Update UERANSIM open5gs-ue.yaml for Dual PDU Sessions ===")
    _, ue_content, _ = run_ssh(CORE_HOST, CORE_PORT, CORE_USER, CORE_PASS, "cat /home/emsadmin/UERANSIM/config/open5gs-ue.yaml")
    
    ue_session_replacement = """sessions:
  - type: 'IPv4'
    apn: 'internet'
    slice:
      sst: 1
      sd: 1"""

    pattern_ue = r"sessions:[\s\S]*?(?=# Configured NSSAI)"
    ue_content = re.sub(pattern_ue, ue_session_replacement + "\n\n", ue_content)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(CORE_HOST, port=CORE_PORT, username=CORE_USER, password=CORE_PASS, timeout=15)
    sftp = client.open_sftp()
    with sftp.file("/tmp/open5gs-ue.yaml", "w") as f:
        f.write(ue_content)
    sftp.close()
    client.close()
    run_ssh(CORE_HOST, CORE_PORT, CORE_USER, CORE_PASS, "cp /tmp/open5gs-ue.yaml /home/emsadmin/UERANSIM/config/open5gs-ue.yaml", sudo=True)
    print("open5gs-ue.yaml updated with one requested PDU session (internet)!")

    print("=== Step 4: Restart 5G Core, gNB, and UE ===")
    restart_cmd = "systemctl restart open5gs-smfd && sleep 3 && systemctl restart ueransim-gnb && sleep 3 && systemctl restart ueransim-ue && sleep 4"
    st, out, err = run_ssh(CORE_HOST, CORE_PORT, CORE_USER, CORE_PASS, restart_cmd, sudo=True)
    print("Restart completed.")

    print("=== Step 5: Check PFCP Association and UERANSIM Interfaces ===")
    _, smf_pfcp, _ = run_ssh(CORE_HOST, CORE_PORT, CORE_USER, CORE_PASS, "journalctl -u open5gs-smfd -n 25 --no-pager | grep -i 'pfcp'")
    print("SMF PFCP Logs:\n", smf_pfcp)

    _, ue_netns, _ = run_ssh(CORE_HOST, CORE_PORT, CORE_USER, CORE_PASS, "ip netns exec ueransim ip -brief addr show", sudo=True)
    print("UE Network Interfaces:\n", ue_netns)

if __name__ == "__main__":
    main()
