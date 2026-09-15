# Open5GS baseline

Audit: 2026-09-14 America/Lima (guest clock 2026-09-15 UTC).

- EMS HEAD before work: `b3ce578` on `main`; original CHF was uncommitted.
- Runtime: Open5GS `2.8.0~jammy5`, binary reports `v2.8.0`.
- Source baseline selected for reproducible build: upstream tag `v2.8.0`,
  commit `157f611a530e292e40ec50f9d23f0ef5d4fcd6a6`.
- Package source-to-commit equivalence is **not proven**: no source checkout
  exists on the core VM. The unmodified source build must be tested separately.
- OS: Ubuntu 22.04.5 LTS, amd64; kernel 5.15.0-191-generic.
- Available compiler: GCC 11.4.0 (Ubuntu 11.4.0-1ubuntu1~22.04.3).
- Runtime uses systemd and Debian packages, not an Open5GS container image.
- SMF binary SHA256: `fb98285a8db096857c766cb5cc11f5fa164ff840ac5286ba3b84951d9b549a4b`.
- NRF binary SHA256: `8235e30fffeb4e9e608592ef4c06c95e471824494934b7d87e58c9c9e390cd88`.
- SCP binary SHA256: `09e70a0d54cef48712475600dbffcc0cd55e00968c54ecb450da6e3654f06f64`.
- UERANSIM on core: `2a3ef81f189ca95d5c1996a28ed7af9734f5cfb4`; lab YAML modified.

## Actual deployment

| Host | SSH localhost port | Private address | Role |
|---|---:|---|---|
| core | 2222 | 10.210.50.1 | AMF, SMF, NRF, SCP, shared NFs |
| upf-01 | 2223 | 10.210.50.8 | DNN internet, UE pool 10.45.0.0/16 |
| upf-02 | 2224 | 10.210.50.9 | DNN corporate, UE pool 10.46.0.0/16 |
| gnb-01 | 2225 | 10.210.50.10 | UERANSIM gNB |
| ue-01 | 2226 | 10.210.50.11 | UERANSIM UE |

SMF SBI is `127.0.0.4:7777`, SCP `127.0.0.200:7777`, NRF `127.0.0.10:7777`.
SMF uses the SCP for current SBI clients. PFCP peers are the two remote UPFs.

At audit time UPF2, gNB and UE also had duplicate `10.210.50.8/24` on
enp0s8, inherited from cloning. UE reported `RM-DEREGISTERED` and
`MM-DEREGISTERED/NO-CELL-AVAILABLE`, with no PDU interfaces. Service active
does not establish a healthy baseline. Repair and verify before charging trials.

Source: [upstream v2.8.0](https://github.com/open5gs/open5gs/tree/157f611a530e292e40ec50f9d23f0ef5d4fcd6a6).

