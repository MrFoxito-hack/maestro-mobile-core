# Decisión de ingeniería: implementación CHF propia basada en antecedente comunitario

Fecha: 2026-09-14

## Decisión

Se implementará un CHF autónomo y un cliente Nchf para el SMF de Open5GS. La
discusión comunitaria Open5GS #4421 se reconoce y cita como antecedente de
arquitectura, pero no como código adoptado: su autor confirmó que el desarrollo
no continuó y no existe un artefacto público verificable para reutilizar.

## Elementos conservados del antecedente

- entrega incremental;
- NF CHF independiente;
- ciclo Create/Update/Release;
- medición UPF mediante PFCP URR;
- cliente Nchf en SMF protegido por feature flag;
- pruebas de retransmisión, agotamiento y cierre;
- descubrimiento NRF y routing SCP en fases posteriores.

## Cambios propios

- base contable separada de MongoDB de Open5GS;
- reserva transaccional por cuenta y sesiones concurrentes;
- ledger append-only e idempotencia con detección de payload contradictorio;
- fallo conservador para online charging, sin cuota gratuita ante caída de BD;
- concesión unificada `totalVolume`, sin partición artificial UL/DL 50/50;
- umbral configurable y no presentado como requisito fijo de 3GPP;
- contrato fijado a TS 32.291 Release 16 y matriz requisito-prueba.

## Estado de evidencia

El servicio local y sus pruebas demuestran solamente el motor de autorización y
el contrato HTTP del subconjunto declarado. No demuestran todavía
interoperabilidad con Open5GS, HTTP/2, NRF/SCP ni enforcement del UPF.

