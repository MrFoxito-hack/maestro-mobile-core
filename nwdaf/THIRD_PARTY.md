# External research dependencies

3GPP/ETSI OpenAPI artifacts are downloaded unchanged from their official publications,
verified against SHA-256 and cached in ignored `Code/.work/nwdaf-contract`.
Their copyright notices remain intact; they are not copied into this module's source tree.

QoE uses https://github.com/itu-p1203/itu-p1203 at commit
`fa4a8735ca983af169c84d346c631c8f4ff7b85a` (1.8.3).
This implementation is not an official ITU publication. Its LICENSE.md grants
non-commercial research use but forbids other uses including redistribution and merging.
Do NOT vendor the source, redistribute a container including it, or claim the dependency
is MIT merely because its package metadata includes that classifier. The license text controls.
Install it separately for thesis research only; obtain rights-holder permission for other use.

Reference: W. Robitza et al., “HTTP Adaptive Streaming QoE Estimation with ITU-T Rec.
P.1203: Open Databases and Software,” ACM MMSys 2018. See the upstream README for citation.

Statistical model implementation: statsmodels Holt-Winters and NumPy linear algebra.
Algorithm choices, calibration, and resulting predictions are MAEstro research decisions,
not algorithms prescribed or certified by 3GPP.
