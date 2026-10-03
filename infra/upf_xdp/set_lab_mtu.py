from remote import connect, run
u, g = connect(), connect(2225)
try:
    print(run(g, 'ip link set dev enp0s8 mtu 1600; ip -d link show enp0s8', True))
    print(run(u, 'ip link set dev enp0s8 mtu 1600; ethtool -K enp0s3 gro on; cd /home/emsadmin/upf-xdp; python3 upf_xdp_agent.py register --pcap evidence/session.pcap --ue 10.45.0.83 --output evidence/registered-session.json', True))
finally:
    u.close(); g.close()
