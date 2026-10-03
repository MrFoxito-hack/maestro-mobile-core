# Terminal de laboratorio MAEstro

Interfaz propia de smartphone para controlar el UE UERANSIM configurado.
No es un Android real, un emulador Android ni un gemelo RF certificado.

Selector DNN, intranet y aislamiento: ver [terminal-dnn-isolation.md](terminal-dnn-isolation.md).

## Implementado

- Acceso flotante para docente/administrador, solo en 5G SA; diálogo accesible,
  navegación por conexión, bolsa y diagnóstico, adaptable a pantallas pequeñas.
- Estado real `nr-cli`, interfaces TUN, IP y contadores Linux; consulta cada
  cinco segundos solo mientras está abierto. Sin inventar señal o batería.
- Modo avión solicita desregistro NAS y detiene el servicio UE. La vuelta
  inicia el servicio; no presupone registro exitoso. Confirmar NAS en trazas.
- Saldo CHF real: restante = cuota menos débito; reserva mostrada por separado.
- Recarga administrativa de 50 MB decimales, transaccional e idempotente por
  UUID; no reinicia consumos ni elimina CDR. Reintentar usa el mismo UUID.
- Diagnóstico acotado: 40 pings desde uesimtun0 hacia 10.45.0.1, máximo 25 s.
  Es tráfico real UE–UPF, no demuestra Internet N6 ni streaming multimedia.
- Descarga HTTP N6 real de 262.144 bytes: servidor privado
  `10.210.50.1:18090`, UPF-01 y `uesimtun0`. El navegador solo recibe los
  resultados de curl ejecutado en el UE, nunca descarga directamente del origen.
  Destino fijo, máximo 12 segundos y sin reintentos implícitos. Se muestran los
  resultados incompletos sin atribuirlos automáticamente a cuota agotada.
- Endpoints protegidos por rol y acciones auditadas. Sin URL, comandos o
  destinos arbitrarios proporcionados por el navegador.

## Diferencias deliberadas respecto al plan original

`4012` no es un código HTTP de Nchf. La UI no fabrica una notificación de
rechazo CHF a partir de saldo reservado cero. Tampoco se promete que una
recarga reactive inmediatamente una sesión ni que un proceso detenido pruebe
que hubo Deregistration. La autoridad son la telemetría y los paquetes.

El video usa HLS/fMP4 y un patrón propio de 60 segundos, sin audio, en perfiles
720p/2 Mbps y 1080p/4 Mbps nominales. Los segmentos viajan del servidor N6 al
UE por su sesión PDU; un relay SSH/backend los entrega al navegador. No es
una descarga directa desde el navegador al servidor de contenido ni una
aplicación Android nativa. La pasarela Android externa continúa fuera del alcance
implementado. No se instalaron reglas NAT generales ni un proxy abierto.

El reproductor mantiene un búfer objetivo de 2–4 segundos y no reintenta
automáticamente transferencias fallidas. Una transferencia ya iniciada puede
tardar hasta 8 segundos en terminar al cerrar el terminal. Por ello no se
promete corte audiovisual instantáneo ni equivalencia entre bytes HTTP de
video y débito IP del CHF. Una interrupción de red no se etiqueta como fallo
de cuota sin consultar la evidencia contable.

### Verificación N6 y concurrencia Nchf

La primera descarga detectó dos fallas reales: retorno del Core por el antiguo
`ogstun` local y cierre conservador de la PDU al recibir un reporte PFCP mientras
un Nchf Update seguía pendiente. Se corrigió el cliente nativo para conservar
los reportes en orden y procesarlos antes de instalar una cuota de reemplazo.
No se incrementó el saldo ni el tamaño de concesión para ocultar el problema.

- Compilación Linux del SMF y 193 comprobaciones C aprobadas.
- `e2e_native.py --execute --case renewal --burst`: un reporte encolado,
  20.000 bytes debitados, 20.560 observados, 560 de exceso a granularidad de
  paquete, reserva final cero y liberación NAS recibida. El exceso se registra,
  no se presenta como débito. Evidencia: `.work/chf-e2e-9bd2e427a1b0`.
- Tras el despliegue, descarga completa: HTTP 200, 262.144 bytes en 7,05 s,
  IP del UE 10.45.0.2. No es una medición de velocidad máxima ni video.
- El perfil nativo compacta su ventana de 256 registros en memoria conservando
  todos los pendientes y los 32 confirmados más recientes; el diario durable
  completo se conserva. Los reportes antiguos fuera de ventana, inconsistentes
  o con huecos siguen teniendo tratamiento conservador, no aceptación silenciosa.
- La regresión C recorre 10.000 reportes y completa 50.198 comprobaciones.
  Prueba real `.work/chf-e2e-77ffe043de6e`: 440 Updates, 2.300.000 bytes
  debitados, 2.300.664 observados, 664 de exceso, cero reserva al cierre y
  liberación NAS recibida. Servicios permanentes restaurados a activos.
- El escenario aislado usa concesiones de 10 KB para ejercitar la ventana.
  No representa el perfil de video operativo, que actualmente concede 500 KB.
  Una primera prueba HTTP con concesiones de 10 KB terminó por interrupción
  de descarga antes de alcanzar el límite antiguo; no probaba una falla de la
  ventana. Otra prueba ICMP no agotó el saldo por pérdida de paquetes. La
  aceptación final exige tanto superar 256 Updates como cerrar sin reserva.

La política de retorno usa tabla/prioridad 18090 únicamente para TCP con puerto
origen 18090 desde 10.210.50.1 hacia el pool 10.45.0.0/16, vía UPF-01
10.210.50.8. No cambia la tabla principal, no añade NAT ni un proxy abierto.
Depende del inventario actual y de reverse-path filtering en modo loose (2),
verificado en el Core. El servicio de origen usa DynamicUser, filesystem de
solo lectura y límites de memoria/tareas; no publica archivos del sistema.

Despliegue: `infra/charging/deploy_terminal_origin.py --execute`.
Reversión: `sudo systemctl disable --now maestro-terminal-origin maestro-terminal-n6-route`.
SMF activo: `/opt/maestro-charging/maestro-charging-b3005a21b872`.
Reversión SMF: `sudo /bin/sh /home/emsadmin/smf-upgrade-RznyK3/rollback.sh`.
Se conserva el despliegue anterior y la base contable; no se borraron CDR.
El parche reproducible se exporta con `infra/charging/export_native.py`, con
comprobación de aplicación inversa contra el árbol revisado.

## Fuentes y reutilización

- [shadcn/ui](https://github.com/shadcn-ui/ui),
  [licencia MIT](https://github.com/shadcn-ui/ui/blob/main/LICENSE.md): reutiliza
  los componentes de diálogo que ya contiene MAEstro. Conservar sus avisos.
- [UERANSIM: uso](https://github.com/aligungr/UERANSIM/wiki/Usage): control CLI
  y túneles de datos. No se importó código adicional de ese repositorio.
- Diseño del chasis, distribución y conectores backend: implementación local.
- [hls.js 1.7.3](https://github.com/video-dev/hls.js),
  [licencia Apache-2.0](https://github.com/video-dev/hls.js/blob/master/LICENSE):
  reproductor MSE cargado bajo demanda. Aviso en `frontend/public/THIRD_PARTY_NOTICES.txt`.
- [FFmpeg, multiplexor HLS](https://ffmpeg.org/ffmpeg-formats.html#hls-2):
  generación local con `infra/charging/generate_terminal_media.py --execute`.
  Se usa un patrón sintético propio, no material audiovisual de terceros.

## Verificación adicional de streaming

- Navegador real, sin fixtures: reproducción 1080p y corte tras agotar crédito
  disponible para nuevas reservas, con débito y nuevo CDR confirmados.
  Evidencia `.work/video-quota-bd17eef2-ff7c-4662-9602-aef59d2b05a3.json`:
  débito de 42.264.313 bytes, repuesto mediante una recarga idempotente del
  mismo importe, sin borrar consumo ni CDR. Existían reservas anteriores de
  otras sesiones: disponible cero no implica saldo total menos consumo cero.
- Backend de terminal, media, tarificación y trazas Nchf: 19 pruebas aprobadas.
- Repetición el 18 de septiembre: suite backend completa, **73 aprobadas**.
  La falla DOCX registrada en la ejecución anterior ya no se reproduce en esta
  corrida; no se modificó Performance como parte de este trabajo.
- Video 720p: `.work/video-quota-041b5441-3aee-43ba-bad7-cc94bd985382.json`.
  Corte de transferencia observado por el navegador, crédito disponible cero,
  nuevo CDR y 35.760.643 bytes debitados y repuestos. Tras recargar, reproducción
  real verificada tanto en 720p como en 1080p.
- Se detectó una carrera en la restauración del arnés: iniciar el UE antes de
  la asociación PFCP del UPF de Internet podía abrir una PDU sin conectividad
  N6. La restauración normal ahora espera las asociaciones configuradas antes
  de reiniciar gNodeB y arrancar UE; se registra `pfcp_ready` en su evidencia.
  Repetición `.work/chf-e2e-faa96d291a50`: 443 Updates, débito de 2.300.000
  bytes, reserva final cero, liberación NAS y `pfcp_ready: true`; UPF, SMF y UE
  activos al terminar.
- Reproducción y agotamiento: `infra/charging/verify_video_quota.py --execute`.
  Consume saldo experimental y repone el débito de la prueba. Las verificaciones
  posteriores de reproducción generan consumo adicional. No ejecutar a la vez
  que el arnés aislado, pues este reinicia temporalmente SMF, UPF y UE.

Esto verifica el alcance educativo documentado, no alta disponibilidad,
capacidad ilimitada ni certificación completa 3GPP Release 16 o de producción.

### Presentación de Data Lab

El video y sus controles son la vista principal. Se retiraron las barras
decorativas y las tarjetas redundantes; ICMP, descarga N6 y RX/TX se conservan
en «Diagnóstico de red», cerrado inicialmente. «Acerca de esta prueba» contiene
las tasas nominales, arquitectura y límites de interpretación, sin saturar la
pantalla principal. Los errores y resultados siguen siendo visibles cuando
corresponde; no se ocultan mediante el cambio visual.

No se descargó una plantilla de teléfono de licencia desconocida.

## Verificación realizada el 17 de septiembre de 2026

- Frontend: TypeScript y Vite build correctos; ESLint del componente sin errores.
- Navegador Edge: datos reales UE/CHF, vistas de conexión/bolsa/diagnóstico,
  1440×1000 y 390×844, cierre por Escape; sin fixtures de red.
- Prueba operativa vía API: recarga de 50 MB acreditada exactamente una vez al
  repetir su UUID, modo avión con servicio inactivo y posterior RM-REGISTERED
  con interfaz PDU, inicio/detención de tráfico acotado. UE devuelto a activo.
- La prueba añadió 50 MB experimentales reales a la cuenta del laboratorio;
  no se borraron débitos, registros contables ni CDR.
- CHF: 55 pruebas aprobadas en Windows, una omitida por bloqueo POSIX; dos
  advertencias de deprecación de bibliotecas de pruebas.
- Capturas visuales locales: `.work/terminal-network.png`,
  `.work/terminal-balance.png` y `.work/terminal-mobile.png`.
- Suite backend completa: 64 aprobadas y una falla en el informe DOCX de
  Performance (`test_report_is_docx_with_native_plot_and_missing_data_notice`,
  no encuentra `test-fixture`). Esta falla también apareció antes de añadir
  el terminal; no se modificó ese módulo para ocultarla. Las pruebas específicas
  de terminal, tarificación y Nchf pasaron.
