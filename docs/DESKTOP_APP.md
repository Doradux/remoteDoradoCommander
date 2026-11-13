# Dorado Relay Desktop — Windows y Ubuntu (candidata)

**Estado:** aplicación gráfica nativa Qt para las dos plataformas. Todavía NO hay
servidor Cloud de producción, Checkout, cobros ni cuentas públicas activas. No
debe publicarse ni ofrecerse a compradores hasta recibir visto bueno explícito.

## Qué verá el usuario

Al iniciar `DoradoRelay`, puede escoger entre:

**Mi servidor (gratis)**
- Introduce una vez la URL HTTPS de su propio relay.
- Elige nombre del PC y permisos locales `inspect`, `files` o `full`.
- Se abre su navegador habitual para aprobar un código de vinculación.
- El token del dispositivo se guarda en el almacén seguro del sistema operativo.
- Cada vez que reabre la aplicación, recupera la URL, el dispositivo y el modo
  elegido y reconecta automáticamente (mientras el relay está accesible).

**Dorado Cloud (futuro servicio alojado)**
- Introduce correo y contraseña de su cuenta asociada a un plan de pago activo.
- El proveedor central comprueba **en el servidor** las credenciales y la
  suscripción Stripe previamente reconciliada; no se acepta una mera bandera de
  plan activo guardada en el PC.
- Un broker de Cloud firma una solicitud de alta para el relay privado del
  cliente. El relay valida HMAC, nonce, cuota firmada y fecha y devuelve un
  token de dispositivo. No se solicita una segunda contraseña del relay.
- La aplicación guarda únicamente tokens de sesión y dispositivo en
  Windows Credential Manager o Secret Service de Ubuntu, no en un JSON.
- Cada inicio exige una nueva verificación de sesión/plan. Durante el uso
  se verifica cada cinco minutos y se detiene la conexión al fallar.
- Si la sesión caduca, si el plan no está activo, si el relay no está disponible
  o si no hay servicio Cloud configurado, la aplicación lo muestra; **no habilita
  Cloud por su cuenta**.
- La cuenta anterior se rellena y el último modo seleccionado se recuerda;
  para usar otra cuenta hay un botón de cambio de conexión.

## Auto-conexión tipo Steam

El archivo de preferencias del usuario contiene solo:
`last_mode`, `own_relay`, `device_name`, `own_device_name`,
`cloud_device_name`, `permission`, `cloud_email`, `cloud_relay`,
`auto_connect`. El nombre de equipo usado en cada modalidad se recuerda
por separado.

Ubicación:
- Windows: `%APPDATA%\\dorado-relay\\desktop.json`
- Ubuntu: `~/.config/dorado-relay/desktop.json`

Nunca incluye password/token/session secret. Los tokens se guardan en el llavero
nativo del usuario y están diferenciados por servidor, equipo y correo.

Al abrirse:
1. Lee la preferencia **last_mode**, sin cambiar automáticamente de cuenta.
2. Si era Mi servidor, recupera la credencial almacenada y reconecta en un hilo.
3. Si era Cloud, verifica el token de sesión con el servicio central antes de
   usar el token de dispositivo. Rechaza un plan inactivo o una URL cambiada.
4. La conexión WebSocket reintenta automáticamente si se pierde la red.
5. Si faltan credenciales seguras, muestra el formulario sin pedir acciones
   imposibles ni conectar a una cuenta distinta.

La casilla de auto-conexión permite desactivar esta conducta sin borrar la
preferencia del último modo.

## Cómo probar desde el repositorio

Ubuntu con Python 3.12 y `python3-venv`, o Windows con Python 3.12 instalado:

```bash
python3 -m venv .venv-desktop
.venv-desktop/bin/python -m pip install -e '.[desktop]'
.venv-desktop/bin/dorado-relay-desktop
```

En Windows PowerShell:

```powershell
py -3.12 -m venv .venv-desktop
.\\.venv-desktop\\Scripts\\python.exe -m pip install -e '.[desktop]'
.\\.venv-desktop\\Scripts\\dorado-relay-desktop.exe
```

La candidata ofrece builds PyInstaller independientes para **Ubuntu x64** y
**Windows x64**, en carpetas portables `DoradoRelay/` con todo Qt incluido;
no es preciso instalar Python para ejecutar esos paquetes. Antes de
distribuirlos públicamente, firmar los ejecutables, revisar Qt/LLGPL y
verificar el comportamiento en PCs limpios.

Se puede ejecutar la prueba de GUI sin conectar a Internet:

```bash
dorado-relay-desktop --smoke-test
```

## Cómo arrancar el servidor propio sin pagar un VPS

Ver [HOME_SELF_HOSTED.md](HOME_SELF_HOSTED.md). Desde la copia del repositorio:

```bash
python3 scripts/setup_selfhost.py --home --domain mi-relay.duckdns.org
docker compose -f compose.yaml -f compose.home.yaml up -d --build
```

Esto requiere Docker, DNS, HTTPS, reenvío TCP 80/443 y un anfitrión accesible
desde Internet. CG-NAT impide el reenvío IPv4 normal. Para controlar ese mismo PC
también debe ejecutarse allí Dorado Relay Desktop.

## Qué falta para habilitar Cloud comercial

El backend `remote_dorado/cloud_desktop_auth.py` implementa los endpoints de
escritorio `POST /desktop/v1/login`, `GET /desktop/v1/session` y
`POST /desktop/v1/logout` dentro del centro de control, y el relay aislado
implementa `POST /internal/cloud/device` y `POST /internal/cloud/revoke`.
No se incluyen contraseñas master, tokens de operador ni claves Stripe en la app.

Para un piloto controlado, un operador debe:
- Registrar al cliente y su suscripción Stripe verificada (no existen altas
  públicas ni pagos automáticos todavía).
- Preparar una cuenta Cloud con contraseña mediante
  `python scripts/control_plane_admin.py set-account --customer-id ID --email usuario@example.com`,
  que solicita el secreto sin imprimirlo.
- Configurar el dominio Cloud público detrás de TLS y el relay dedicado con
  `DORADO_CLOUD_MANAGED=1`, cuota firmada y secretos HMAC por cliente.
- Indicar el dominio público central en la variable de build
  `DORADO_BUILD_CLOUD_URL=https://cloud.TU-DOMINIO.com` en el
  entorno del compilador, **sin incluir ninguna clave secreta**.
- Mantener reconciliación de Stripe y entrega de cuotas automática/fiable.
  Con el prototipo actual, el export de cuotas sigue siendo manual y caduca.
- Añadir límites globales de rate limiting en el proxy y auditar las rutas
  internas de broker, prevención SSRF, sesiones, revocación y aislamientos.

**No hay Cloud disponible para usuarios finales todavía.** Mientras su URL no
esté configurada, el botón de Cloud queda inhabilitado en la interfaz.

## Consideraciones de seguridad y UX

- El modo `full` ejecuta comandos con los permisos reales del usuario de OS.
- Una ventana Qt no da un sandbox de sistema operativo.
- La app no abre puertos: el agente conecta **hacia fuera** mediante WebSocket.
- Las preferencias no contienen secretos. Cuando el llavero no está operativo
  se informa del problema; nunca se crea un archivo de tokens en texto plano.
- Un cierre de sesión Cloud solicita también revocar el dispositivo. Si no
  se confirma, debe revocarse manualmente desde el panel del relay.
- El programa es nativo. Los futuros paneles **web** deben seguir usando
  exclusivamente componentes de Animate UI.
