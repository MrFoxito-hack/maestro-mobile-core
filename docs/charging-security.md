# Security boundary of the current CHF increment

SBI and management are separate ASGI applications/listeners. No `/admin` route is
mounted on SBI. Admin and NF credentials are distinct bearer tokens; NF credentials
are bound to the UUID in the request. They are local lab authentication, **not NRF
OAuth2 access-token validation**. Keep both listeners isolated and use TLS before
crossing an untrusted network. HTTP/2 plaintext testing is confined to loopback.

Missing credentials fail startup. `CHF_SBI_LAB_NO_AUTH=true` is an explicit lab-only
opt-in; it never removes administrative authentication. It must not be used for
VNRT shared/untrusted access. No token or complete subscriber identifier is logged.
SBI request size is bounded even without Content-Length; errors do not echo bodies
or database exception text. Readiness checks storage and account invariants.

The default container runs Hypercorn (h2/h2c), with one worker. Management is a
second service on 8082; host bindings are localhost. These bindings alone do not
isolate peers on the Docker network, hence authentication is still mandatory.

Not yet implemented: mTLS deployment, OAuth2/NRF scopes, token rotation workflow,
distributed database credentials, per-principal rate limiting, HA and tamper-proof
external ledger storage. SQLite file access must be limited to the CHF runtime.
