# Terminal 5G: selección de DNN e intranet

## Alcance implementado

El selector de Conexión elige qué **sesión PDU ya establecida** utiliza Data Lab.
No altera la ruta por defecto del sistema UE, no libera/recrea una PDU y no
simula señalización NAS ni cambios en la topología. Las dos sesiones pueden
permanecer activas simultáneamente.

| DNN | Pool | UPF | Video del laboratorio | Intranet |
| --- | --- | --- | --- | --- |
| internet | 10.45.0.0/16 | UPF-01 | Permitido | Rechazado |
| corporate | 10.46.0.0/16 | UPF-02 | Rechazado | Permitido |

El video es un servidor controlado del laboratorio, no una prueba de acceso
general a Internet público. La intranet escucha en `10.46.0.1:8080/intranet`.
Su autorización es por subred/interfaz, no un login empresarial ni HTTPS.

## Distinción normativa importante

Las sesiones observadas tienen **SST=1 y SD ausente en ambas**. Esto demuestra
separación por DNN, sesión PDU, UPF y política de red, **no demuestra dos
S-NSSAI distintos**. No se escribió `SD=1` en el portal porque sería falso.
El portal muestra el S-NSSAI devuelto por `nr-cli ps-list`.

Base local: TS 23.501 V16.20.0, cláusulas 5.6.1 y 5.15, en
`../../Releases/markdown/TS_23.501.md`. DNN y S-NSSAI son dimensiones
relacionadas, pero tener DNN diferentes no prueba por sí solo slices diferentes.
La selección de una PDU existente para transmitir datos es consistente con la
distinción entre establecer una PDU y usar una ya establecida descrita en
[TS 24.501 V16.12.0, §4.6.3 y §6.4.1, Release 16](https://www.etsi.org/deliver/etsi_ts/124500_124599/124501/16.12.00_60/ts_124501v161200p.pdf).
Este selector manual no implementa distribución de reglas URSP ni certifica
conformidad integral con dichas especificaciones.

Para demostrar dos S-NSSAI distintos falta configurar y validar coherentemente
el SD corporativo en suscripción, AMF/SMF, gNodeB y UE, y comprobarlo en NAS/NGAP.
No se cambiaron esas configuraciones como parte del aislamiento de DNN.

## Implementación

- `terminal_sessions.py` cruza DNN/IP de `nr-cli ps-list` con interfaces Linux.
  No presupone `uesimtun0=internet` ni `uesimtun1=corporate`: se rechazan
  asociaciones ausentes, ambiguas o fuera del pool esperado.
- La preferencia se persiste en SQLite por hash de host, puerto de UE e IMSI.
  No es una variable global compartida entre identidades. El endpoint APN
  admite `imsi` opcional; sin él se utiliza el UE configurado. Estado, video
  e intranet también admiten `imsi` para clientes futuros.
- Los nodos CLI deben coincidir con el IMSI solicitado. Los contadores y las
  interfaces de estado se restringen a las PDU de ese UE.
- El relay multimedia verifica el DNN en cada petición y utiliza la interfaz
  observada. En corporate responde 403 antes de descargar video.
- El relay de intranet hace una consulta acotada, sin redirecciones ni destinos
  arbitrarios, ligada a la interfaz corporativa. Verifica que la IP vista por
  el portal coincida con la IP de la sesión. React presenta datos estructurados;
  nunca ejecuta HTML remoto dentro del origen del EMS.
- ICMP usa la sesión seleccionada; la descarga de diagnóstico N6 solo internet.
  RX/TX son **bytes**, no número de paquetes, y corresponden a la interfaz elegida.
- Cambiar de pestaña/DNN desmonta el reproductor. No se inician descargas nuevas
  de video en corporate; una transferencia previa ya iniciada puede terminar
  dentro de su plazo máximo de 8 segundos.

La selección y el mapeo están preparados para varios UEs en el host gestionado,
con pruebas de identidades independientes e interfaces renumeradas. La UI actual
controla el UE configurado; no se ha certificado operación simultánea de múltiples
UEs reales ni un inventario de UEs repartidos entre varias VMs.

## Aislamiento de red

Tabla nftables propia `inet maestro_terminal`, sin vaciar reglas ajenas:

- UPF-01: rechaza reenvío desde `ogstun` hacia `10.46.0.0/16`.
- UPF-02: rechaza reenvío desde `ogstun`; permite al pool corporativo entrar
  por `ogstun` únicamente al portal y al ICMP de diagnóstico de `10.46.0.1`.
  Rechaza el resto del acceso local desde ese túnel y el acceso al portal
  desde otras interfaces. No cambia SSH de gestión, N3 ni N4.
- El servidor aplica además una lista de acceso por subred corporativa.

Instalación: `backend/.venv/Scripts/python.exe infra/charging/deploy_terminal_slices.py --execute`
(ajustar rutas según directorio de ejecución).

Servicios habilitados: `maestro-terminal-isolation` en ambos UPF y
`maestro-corporate-portal` en UPF-02. DynamicUser, filesystem de solo lectura,
sin privilegios nuevos, 64 MB y 16 tareas para el portal.

Reversión en UPF-02:

```sh
sudo systemctl disable --now maestro-corporate-portal maestro-terminal-isolation
```

Reversión en UPF-01:

```sh
sudo systemctl disable --now maestro-terminal-isolation
```

Esto retira exclusivamente los servicios/reglas propios. No borra evidencia,
datos CHF, configuraciones del core ni directorios de versiones.

## Evidencia de aceptación

- `verify_terminal_slices.py --execute`: cuatro consultas HTTP reales desde
  interfaces UE, IP de origen en conexiones exitosas y aumento de contadores
  de rechazo en ambos UPF. Evidencia: `.work/terminal-slices-eed04be2d29e.json`.
- Internet → video: HTTP 200 desde `10.45.0.2`.
- Corporate → intranet: HTTP 200 desde `10.46.0.2`.
- Cruces: curl exit 7 y cero bytes recibidos, con rechazos nftables observados.
  En TCP rechazado curl puede no informar `local_ip`; no se fabrica ese dato.
- `check_live_browser.py --apn`: selección, portal real, denegaciones del backend,
  persistencia tras recargar la página y reproducción real al volver a internet.
- `check_live_browser.py --terminal`: revisión escritorio/móvil, diagnóstico
  plegado accesible y cierre con teclado, sin fixtures del navegador.
- Suite backend: 79 pruebas aprobadas. Build TypeScript/Vite correcto; permanece
  la advertencia de tamaño del módulo HLS cargado bajo demanda.
- Capturas: `.work/terminal-corporate.png`, `.work/terminal-internet.png`.

Las pruebas generan tráfico y consumen crédito experimental; no se reinició
la contabilidad ni se eliminaron CDR.
