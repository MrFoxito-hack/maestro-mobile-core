# GUION MAESTRO: EXPOSICIÓN MASTICABLE Y PROFESIONAL

> **Nota para la grabación:**  
> Las frases son cortas y directas para que puedas **respirar con calma y hablar con total naturalidad**.  
> El tono es de un ingeniero seguro que sabe exactamente lo que construyó: serio, técnico, pero sin palabras infladas ni trabalenguas.  
> Los corchetes `[PANTALLA: ...]` te indican qué mostrar en cada momento.

---

### 1. Introducción: Qué es MAEstro y qué problema resuelve
`[PANTALLA: Mostrar la pantalla principal de MAEstro en modo oscuro, con el mapa del Core y el dashboard general]`

«Buenos días a todos. Hoy les quiero presentar **MAEstro**, una plataforma integral de gestión de red, tarificación y experimentación autónoma para **5G Standalone**.

En el mundo de las telecomunicaciones, los núcleos 5G de código abierto tienen un problema real: son excelentes para establecer sesiones de datos básicas, pero no están listos para una operadora. No tienen cómo cobrar a los abonados, no pueden optimizar su tráfico de forma autónoma y se saturan con facilidad cuando hay carga pesada.

Con **MAEstro** convertimos ese Core en una solución robusta y de nivel operadora. No hicimos una simple maqueta web. Intervenimos directamente el **código en C del Core**, implementamos tarificación convergente con control de cuotas, aceleramos el tráfico con **eBPF** en el kernel de Linux y agregamos un asistente con **Inteligencia Artificial local usando Ollama**. A continuación, les muestro cada parte en acción.»

---

### 2. Infraestructura y Network Slicing Real
`[PANTALLA: Ir a "Topología" (/topology) y señalar con el mouse los dos SMF y los dos UPF]`

«Empecemos por la arquitectura de red. Desplegamos un Core 5G Release 16 con **Network Slicing** real, aislando el tráfico de control y de usuario conforme a la norma 3GPP TS 23.501.

Aquí pueden ver dos rebanadas de red totalmente independientes:
* A la izquierda está el **Slice de Internet masivo**, con SST 1, atendido por su propio `SMF-01` y `UPF-01`.
* A la derecha tenemos un **Slice Corporativo privado**, con SST 1 y SD 2, gobernado por un segundo `SMF-02` y un `UPF-02` dedicado.

Toda la comunicación interna se realiza mediante microservicios sobre **HTTP/2 con soporte de NRF y SCP**. Esto nos da una red elástica, sin enlaces rígidos, lista para separar tráfico crítico de tráfico comercial.»

---

### 3. Terminales Interactivos y Operación
`[PANTALLA: Ir a la sección "Terminal" (/terminal), mostrar el celular virtual, cambiar de APN o mostrar el estado]`

«Para operar el sistema sin depender de teléfonos comerciales de caja negra, integramos terminales 5G totalmente interactivos.

Desde esta consola controlamos la sesión del usuario en tiempo real:
* Vemos su registro 5G confirmado en el AMF.
* Podemos cambiar el **APN** entre `internet` y `corporate` en caliente, observando cómo la red conmuta de slice sin problemas.
* Disponemos de **Modo Avión** para desregistrar el terminal de forma limpia según el estándar 3GPP.
* Y podemos lanzar pruebas de velocidad y sondas hacia Internet.

Cada acción queda registrada con fecha, hora y métricas exactas en el sistema.»

---

### 4. Ingeniería en C: Resolviendo Fallas en el Core
`[PANTALLA: Ir a "Trazas E2E" (/traces/e2e), abrir la traza de idempotencia UDM y mostrar la respuesta 200 y el Registration Accept]`

«El núcleo técnico más fuerte de este proyecto está en las modificaciones que hicimos directamente en el código fuente en C de Open5GS.

Detectamos una falla crítica en el **UDM**: cuando un terminal entraba en bucles de reintento, el Core consumía memoria sin verificar suscripciones previas. Llegaba al límite de 4 096 registros y colapsaba la red entera en poco más de una hora.

Entramos al código en C e implementamos **idempotencia estricta según la norma 3GPP TS 29.503**. Si un terminal ya está registrado, el UDM reutiliza su espacio de memoria en vez de pedir uno nuevo.

Para comprobarlo, le lanzamos una prueba de estrés con **34 002 peticiones consecutivas por HTTP/2**. El resultado fue impecable: el sistema se mantuvo congelado en solo 2 espacios de memoria ocupados, con cero fugas y eliminando la caída del servicio. Además, desacoplamos el **PCF** de MongoDB para que opere de forma canónica por interfaces SBI en HTTP/2.»

---

### 5. Aceleración con eBPF/XDP: +227% de Rendimiento
`[PANTALLA: Mostrar la gráfica comparacion.png o el reporte en reportes/evidencias/upf-xdp-20261002/RESULTADO.md]`

«El plano de control es importante, pero el gran desafío de las redes de alta velocidad está en el plano de usuario. El UPF tradicional procesa los paquetes en espacio de usuario con interfaces virtuales TUN, lo que satura el procesador y genera demoras.

Para resolverlo, programamos un acelerador en C usando **eBPF y XDP** directo en el controlador de la tarjeta de red. Apenas llega un paquete de la antena, el programa de XDP recorta las cabeceras en memoria sin copias innecesarias y envía los datos directo a Internet.

Ejecutamos una campaña experimental de **24 ensayos comparativos** respaldados por **732 artefactos firmados con hashes SHA-256**. Los resultados fueron contundentes:
* El tráfico de descarga TCP aumentó en un **+227.30%**.
* El tráfico de subida mejoró en un **+23.54%**.
* Y confirmamos con capturas de red que la interfaz virtual TUN registró **cero paquetes**, demostrando que el tráfico pasó directo por el kernel a velocidad de hardware.»

---

### 6. Tarificación 5G (CHF): Control de Saldo en Tiempo Real
`[PANTALLA: Cliquear en "Charging" (/charging), mostrar el balance de bytes, la tabla de CDRs y el botón de recarga]`

«Una red comercial necesita cobrar. Por eso desarrollamos una **Función de Tarificación Convergente (CHF)** nativa, cumpliendo los estándares **3GPP TS 32.290 y 32.291**.

A través de la interfaz **Nchf** sobre HTTP/2, el sistema autoriza y descuenta cuotas de datos por bytes en tiempo real. 

Funciona bajo una política estricta de corte: si el usuario agota su saldo, el CHF rechaza la sesión con un código **HTTP 403**, ordenándole al SMF que detenga el tráfico de inmediato. También incluimos una API de recargas (*Top-up*) con libro contable inmutable, permitiendo simular planes prepago y corporativos con total transparencia.»

---

### 7. Redes Autónomas: Closed-Loop con NWDAF
`[PANTALLA: Ir al módulo de "Laboratorio / NWDAF" (/laboratory), mostrar la gráfica de MOS P.1203 y la recuperación de la campaña]`

«Para llevar a MAEstro hacia una **Red Autónoma Nivel 4**, integramos la función **NWDAF**. Este módulo mide la calidad de experiencia de video en tiempo real utilizando el algoritmo estandarizado **ITU-T P.1203**.

Hicimos una prueba real de laboratorio: pusimos a un celular a reproducir video en alta definición mientras otro terminal le inyectaba 20 Mbps de tráfico competidor. Sin automatización, el video se congela y tarda más de 7 segundos en arrancar.

Pero al activar el **Bucle Cerrado (Closed-Loop)**, el NWDAF detecta la degradación al instante y alerta al PCF. El PCF reconfigura las políticas en el UPF vía señalización **PFCP N4** en apenas **17.08 milisegundos de latencia de control**. El video recupera su ancho de banda garantizado casi de inmediato y vuelve a reproducir fluido sin intervención humana.»

---

### 8. Asistente con IA Local usando Ollama
`[PANTALLA: Mostrar la pestaña del Asistente de Investigación (/laboratory), hacer una pregunta y mostrar cómo cita evidencias numéricas]`

«En telecomunicaciones, las operadoras no pueden enviar datos de sus usuarios o fallas de red a servidores externos en la nube por motivos de privacidad y seguridad.

Por eso, integramos en MAEstro un copiloto de investigación que corre de forma **100% privada y local mediante Ollama**, acelerado en una tarjeta gráfica **NVIDIA GeForce RTX 5070**. Desplegamos el modelo **Qwen 2.5 de 7 mil millones de parámetros**, con descarga total en VRAM y **0% de uso de CPU**.

La IA no ejecuta comandos a ciegas. Lee los reportes de métricas sanitizadas y ayuda al ingeniero a entender las causas de una falla, citando directamente los números de las trazas y los eventos de red.»

---

### 9. Operación Carrier y Cierre
`[PANTALLA: Abrir la consola MML (/commands), mostrar un comando rápido y luego mostrar el PDF del Paper IEEE (reportes/Paper_CHF_5GSA_Open5GS_IEEE.pdf)]`

«Finalmente, completamos MAEstro con herramientas de operación profesional:
* Una **Consola MML** transaccional idéntica a las que se usan en los centros de control de Huawei o Ericsson.
* Un visor de **Trazas E2E SmartCare** alineado a 3GPP Release 16 con diagramas de flujo decodificados.
* Y un centro de alarmas vinculado a los procedimientos de red.

En resumen: **MAEstro une código C de bajo nivel, aceleración eBPF (+227%), tarificación CHF, analítica NWDAF e Inteligencia Artificial local con Ollama.** 

Todo el trabajo está respaldado por más de **160 pruebas automatizadas aprobadas**, datos reproducibles y un **artículo científico en formato IEEE** listo para publicación.

Quedo a su completa disposición para responder sus preguntas o realizar cualquier demostración en vivo. Muchas gracias.»
