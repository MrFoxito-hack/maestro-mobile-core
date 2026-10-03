# Native charging development

Experimental downstream patch, AGPL-3.0-or-later where it modifies Open5GS.
Not an upstream CHF implementation or a claim of full Release 16 conformance.

## Reproduce the source change

Use a separate Open5GS v2.8.0 checkout at commit
`157f611a530e292e40ec50f9d23f0ef5d4fcd6a6`. Do not apply to an unrelated source
version or install over `/usr/bin`.

From that clean checkout, using an absolute path to this repository's patch:

```sh
git rev-parse HEAD
git apply --check /path/to/Code/infra/charging/patches/0001-native-nchf.patch
git apply /path/to/Code/infra/charging/patches/0001-native-nchf.patch
```

Configure Open5GS with its normal build dependencies and an isolated prefix.
The lab's existing Meson build uses `--buildtype=debugoptimized` and prefix
`/home/emsadmin/maestro-charging/open5gs/install`.

```sh
ninja -C build -j 2 src/smf/open5gs-smfd src/smf/chf-unit src/smf/chf-probe
ninja -C build -j 2 src/upf/open5gs-upfd src/upf/quota-unit
meson test -C build --suite charging --print-errorlogs
```

There is deliberately no `ninja install`, service replacement or activation in
these commands. Shared SBI request structures changed: eventual packaging must
include ABI-matched private libraries, not load the patched SMF with system SBI
libraries. The build-tree executables use their build-tree libraries.

## Component test

Place `chf/` next to `open5gs/`, create `chf/.venv` and install its
`requirements-dev.txt`. Then:

```sh
chf/.venv/bin/python chf/tools/fetch_contract.py
chf/.venv/bin/python chf/tools/native_acceptance.py
```

The runner uses a synthetic test identity, separate database, random secrets
in process environment and a temporary loopback HTTP/2 listener. It invokes
the compiled native SMF client. Its lifecycle case sends explicit protocol-test
usage fixtures (400 bytes), never presented as UE/N4 measurements. It does not
establish N4.
Logs, request JSON and result JSON remain under `.work/native-create-*`.
Create-only reservations are explicitly reconciled after the probe exits.
The lifecycle case closes through native Release and emits a fixture CDR.
No installed NF is stopped by this test.

## VM development helpers

Run `sync_native.py` / `stage_chf.py` with the backend virtualenv from `backend/`.
They reuse existing SSH configuration without printing credentials, upload only
the isolated development tree and reject unknown remote modifications. Their
transfer manifests are local `.work/` artifacts, not deployment receipts.

## Activation remains gated

See [checkpoint](../../docs/charging-native-checkpoint.md). Keep `smf.chf.enabled`
false in live configurations. A successful Create reserves credit but does not
prove UPF enforcement. Static direct Nchf here is not delegated SCP discovery.
The actual native profile currently supports one default QoS flow, one rating
group and one UPF per PDU session, and requires UPF VTIME support. Unsupported
initial flow shapes are rejected before reserving credit.

The UPF has a separate `upf.charging_enforcement: false` flag. Keep it off in
service until real N4/UE acceptance. Its experimental total-volume gate uses a
monotonic validity deadline and an absolute counter limit independent of report
snapshots. A whole packet may cross the limit; this is not byte-exact blocking.
The first profile does not support buffered/idle DL charging or individual
removal of an active charged URR (close the session instead). These limitations
must be resolved or explicitly bounded before an educational E2E claim.
