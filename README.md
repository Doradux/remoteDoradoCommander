# Remote Dorado Commander

Un MCP remoto **autoalojado**, creado de forma independiente, para usar tus propios equipos desde clientes MCP remotos (incluido ChatGPT, cuando tu cuenta tenga habilitadas las acciones necesarias). **Sin cuotas de llamadas ni suscripción del proveedor original**: tú alojas el relay y el agente. Siguen existiendo los límites de tu infraestructura y de cada cliente/plataforma de IA.

> **Estado: MVP v0.3.0**. Probadas las rutas OAuth, PKCE, refresh, JSON-RPC MCP y el encaminamiento WebSocket con tests automatizados. No es una copia exacta ni una versión del servicio propietario Remote Desktop Commander. No hay despliegue ni conexión real con ChatGPT comprobados todavía.

## Arquitectura

```text
ChatGPT / otro cliente MCP -- HTTPS + OAuth/PKCE --> TU SERVIDOR /mcp
                                                   |  WebSocket autenticado
                                                   v
                                agente (conexión SALIENTE desde tu PC)
                                                   |
                                                OS / shell / archivos
```

- Transporte **MCP Streamable HTTP**, JSON-RPC stateless (`POST /mcp`), sin sesiones ni SSE. Respuestas `application/json` y notificaciones `202`. Endpoint `GET /mcp` devuelve `405` cuando el cliente ya está autorizado.
- **OAuth 2.0**: discovery, registro dinámico de clientes, autorización con contraseña, PKCE S256, tokens bearer y refresh rotatorio. Cada conexión ChatGPT pide aprobación mediante contraseña del administrador.
- Agente **saliente** por `wss://`, múltiples equipos por nombre, reconexión automática; no hay que abrir puertos de entrada en el PC controlado. El servidor recuerda nombres conectados anteriormente y muestra si están desconectados.
- Sesiones de shell persistentes **por PC**: varias simultáneas, con `session_id`, PID, estado, directorio, ejecución interactiva y buffer de salida consultable por cursor. La sesión se mantiene entre distintas llamadas MCP y reconexiones WebSocket mientras el proceso del agente siga vivo.
- Descubrimiento global: `list_all_sessions` consulta en paralelo las terminales de **todos los PCs conectados** y devuelve cada una con su nombre de dispositivo.
- Herramientas: `list_devices`, `ping_device`, `list_directory`, `read_file`, `write_file`, `edit_file`, `search_files`, `execute_command`, `list_active_sessions`, `list_all_sessions`, `start_process`, `send_input_to_process`, `read_process_output`, `terminate_session`, `forget_session`.
- Sin llamadas a servicios del proveedor ni contador/cuota incorporados. Concurrencia simultánea máxima por defecto: 16; salida máxima por stream de comando: 250 KB; timeout de comando: 120 s. Son límites de seguridad/recursos, no cobros por número de llamadas.

## Instalación rápida (sin clonar el repo ni instalar Python)

**Recomendado: descargas binarias privadas desde GitHub Releases.** La Release contiene un agente autónomo para Linux x64, Windows x64, macOS Intel y macOS Apple Silicon; y un servidor autónomo para Linux x64. También se publica un wheel Python y sumas SHA256. La compilación la realiza GitHub Actions automáticamente cuando se sube a `main` una versión nueva en `pyproject.toml`. Puedes consultar los paquetes en [Releases](https://github.com/Doradux/remoteDoradoCommander/releases).

Dado que **el repositorio es privado**, instala [GitHub CLI](https://cli.github.com/) y autoriza la cuenta que tiene acceso con `gh auth login` (una vez por ordenador). **No** expongas credenciales en comandos ni publiques el repositorio para evitar la autenticación.

**Linux / macOS** (Bash; detecta arquitectura automáticamente; verifica SHA256 y pregunta por el URL/token del agente):

```bash
bash <(gh api -H 'Accept: application/vnd.github.raw' repos/Doradux/remoteDoradoCommander/contents/scripts/install.sh) agent
```

**Windows (PowerShell):**

```powershell
& ([scriptblock]::Create((gh api -H 'Accept: application/vnd.github.raw' repos/Doradux/remoteDoradoCommander/contents/scripts/install.ps1 | Out-String)))
```

Una vez instalado, **arranca el agente** con `remote-dorado-agent` (o su ruta impresa por el instalador). El asistente `--setup` guarda URL, nombre del PC y token de forma local, sin incluir el token en el historial de la terminal. Abre automáticamente una shell persistente al arrancar. Debe existir primero un **servidor remoto desplegado por ti**, accesible por HTTPS; el instalador no te da un relay alojado gratuitamente.

Instalar el **servidor Linux x64** como binario (aunque en un VPS es preferible el Docker Compose de más abajo):

```bash
bash <(gh api -H 'Accept: application/vnd.github.raw' repos/Doradux/remoteDoradoCommander/contents/scripts/install.sh) server
```

Para instalación no interactiva: añade `--no-setup` tras `agent` y usa `DORADO_PUBLIC_URL`, `DORADO_AGENT_TOKEN` y `DORADO_DEVICE_NAME` como variables de entorno. Puedes cambiar el destino en Unix con `DORADO_INSTALL_DIR`. Para actualizar, vuelve a ejecutar la misma orden; se descarga la última Release. Las builds son **por arquitectura** y no incorporan ni publican tus secretos.

**Alternativa pip sin clonar:**

```bash
gh release download --repo Doradux/remoteDoradoCommander -p '*.whl' -D /tmp/dorado-wheel
pipx install /tmp/dorado-wheel/*.whl
```

No hay paquete publicado en npm todavía: un comando `npx` público no funcionaría mientras no se publique en el registro npm. Por ahora la descarga desde Releases evita copiar el repositorio y evita depender de Python instalado para el agente. Las Actions del repositorio privado pueden consumir minutos facturables según tu plan de GitHub.

## Preparar el servidor (Linux + Docker)

**Requisitos**: VPS/servidor al que ChatGPT pueda llegar por HTTPS, Docker + Docker Compose, un dominio y un proxy TLS (Caddy, nginx, Cloudflare Tunnel o Secure MCP Tunnel si está disponible en tu entorno).

```bash
git clone git@github.com:Doradux/remoteDoradoCommander.git
cd remoteDoradoCommander
cp .env.example .env
python3 -c 'import secrets; print("DORADO_ADMIN_PASSWORD="+secrets.token_urlsafe(32)); print("DORADO_AGENT_TOKEN="+secrets.token_urlsafe(48))'
```

Copia **los valores aleatorios** impresos a `.env` en lugar de sus `REPLACE_...`, y configura `DORADO_PUBLIC_URL=https://mcp.TU-DOMINIO.com` (sin `/mcp`). No publiques el `.env` ni envíes secretos por chat.

```bash
mkdir -p data
# En Linux, la imagen Docker usa uid 10001 para la base de datos:
sudo chown 10001:10001 data
docker compose up -d --build
curl http://127.0.0.1:8765/health
```

El contenedor solo expone `127.0.0.1:8765` del host. Configura HTTPS con un proxy inverso. **Ejemplo Caddyfile** en el mismo host:

```caddyfile
mcp.TU-DOMINIO.com {
    reverse_proxy 127.0.0.1:8765
}
```

El proxy debe admitir WebSockets, conservar la cabecera `Authorization`, aplicar TLS y limitar el tráfico hacia `/oauth/*` frente a abusos. En producción, añade firewall, copias de seguridad cifradas de `data/`, y monitorización.

Verifica desde internet (en un dispositivo separado):

```bash
curl https://mcp.TU-DOMINIO.com/health
curl https://mcp.TU-DOMINIO.com/.well-known/oauth-authorization-server
curl -i https://mcp.TU-DOMINIO.com/mcp
# Esta última debe devolver HTTP 401 con WWW-Authenticate.
```

## Instalar el agente en un equipo propio

Python **3.11+**; funciona con Linux, macOS y Windows. Método alternativo de desarrollo (requiere descargar el repositorio privado; para evitarlo usa la instalación rápida anterior):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
export DORADO_PUBLIC_URL=https://mcp.TU-DOMINIO.com
export DORADO_AGENT_TOKEN='VALOR_ALEATORIO_DEL_SERVIDOR'
export DORADO_ALLOWED_ROOTS="$HOME"
remote-dorado-agent --name marcos --auto-shell
```

En **PowerShell** (instalación previa con `py -m pip install -e .`):

```powershell
$env:DORADO_PUBLIC_URL="https://mcp.TU-DOMINIO.com"
$env:DORADO_AGENT_TOKEN="VALOR_ALEATORIO_DEL_SERVIDOR"
$env:DORADO_ALLOWED_ROOTS=$HOME
remote-dorado-agent --name marcos --auto-shell
```

Para otro equipo, usa otro nombre (`marcos-hpr`, `servidor`, etc.). El agente debe permanecer ejecutándose (systemd, servicio de Windows, tmux o supervisor de procesos) para que ChatGPT llegue al equipo. Sin el agente, no hay acceso.

## Trabajar con varios PCs y shells activas

Con **un solo servidor MCP**, puedes arrancar el agente en tantos equipos propios como necesites, con nombres únicos como `marcos` (Windows), `marcos-hpr` (Linux), `ubuntu-server`, etc. Si se desconectan, se conservan en `known_devices` como *offline*; los procesos locales mantienen su estado durante breves caídas de red siempre que el agente siga ejecutándose.

- `list_devices`: equipos conectados **ahora** en `devices`, historial con estado en `known_devices`.
- `list_all_sessions`: consulta todas las shells persistentes **gestionadas por el agente** en todos los equipos online. Resultado con `device` y `session_id` para distinguirlas.
- `list_active_sessions(device="marcos-hpr")`: enumera las sesiones de un PC concreto, incluso las finalizadas hasta que se descarten.
- `start_process(device="marcos-hpr", cwd="/home/marcos")`: crea una terminal interactiva; omitir `command` abre la shell predeterminada. Con `command` ejecuta un proceso persistente.
- `send_input_to_process(device="marcos-hpr", session_id="...", input="pwd")`: escribe en la sesión activa y devuelve también la primera salida disponible (espera 200 ms por defecto, configurable con `wait_ms`).
- `read_process_output(device="marcos-hpr", session_id="...", cursor=123)`: lee únicamente lo nuevo a partir del cursor devuelto, sin consumir la salida ni perder el estado de la sesión.
- `terminate_session` y `forget_session` cierran una sesión del agente y liberan su historial.

**Ejemplo de secuencia desde ChatGPT:** «Lista los equipos y todas sus shells activas» → «En `marcos-hpr`, crea una terminal en `/home/marcos/hpr`» → «Ejecuta `git status` en esa sesión» → «Lee lo que ha devuelto» → «Continúa en esa misma terminal». Las shells de distintos dispositivos son independientes, y `cd` y variables propias de cada proceso perduran entre comandos.

**Importante sobre shells externas:** el agente **solo puede enviar texto o capturar salida de sesiones que él mismo haya creado**. No intercepta ni secuestra automáticamente una terminal de VS Code, Windows Terminal, GNOME Terminal, SSH o tmux creada al margen del agente; hacerlo no está soportado por este MVP. `--auto-shell` abre una sesión controlable inmediatamente al arrancar el agente; quita esa opción si prefieres iniciar las sesiones a demanda. La lista `list_all_sessions` no afirma detectar todas las terminales del sistema operativo.

Detalles de implementación: Linux/macOS usan una pseudo-terminal (PTY); Windows usa un proceso persistente `cmd.exe` con tuberías (compatible con comandos básicos, pero no con interfaces de pantalla completa tipo `vim`/`top`). Hasta 24 sesiones activas o retenidas **por agente** (ajustable en código) y 1 MB de salida reciente por sesión; no es una cuota de llamadas ni un servicio de pago. Las sesiones **no sobreviven al reinicio del agente** y las que se ejecuten por fuera no aparecen por sí solas.

## Conectar a ChatGPT

1. Comprueba que tu cuenta/espacio de trabajo tenga habilitada la creación de aplicaciones MCP personalizadas y que permita las **acciones de escritura** que necesitas. Estas capacidades dependen del plan y del despliegue de funciones de ChatGPT.
2. En **Configuración → Aplicaciones**, habilita el modo de desarrollador si se ofrece y entra en **Crear**.
3. URL del servidor MCP: `https://mcp.TU-DOMINIO.com/mcp`. Selecciona OAuth si se solicita (el servidor anuncia sus metadatos).
4. En la pantalla de autorización de **tu dominio** introduce el valor `DORADO_ADMIN_PASSWORD` y aprueba el acceso.
5. Escanea las herramientas, crea/activa la aplicación y prueba `list_devices` y `ping_device` en un chat compatible.

**Importante**: la integración no se activa por subir el proyecto a GitHub ni publicar una Release: debes ejecutar el servidor en una máquina accesible por HTTPS y el agente en los PC deseados. En ChatGPT algunas acciones de terminal/escritura pueden estar restringidas aunque el servidor anuncie las herramientas; esas restricciones son de ChatGPT y este proyecto no las elimina.

## Seguridad

⚠️ **Permisos elevados**: `execute_command` es acceso real al shell como el usuario que ejecuta el agente. Es posible borrar datos, leer claves, modificar repositorios o ejecutar software. Autoriza solamente tus propios clientes; usa un usuario del sistema con pocos privilegios, idealmente una VM o contenedor para pruebas.

- La contraseña de administración es **distinta** del token de agente y se almacena únicamente en variables de entorno; ambos deben ser aleatorios, largos y secretos.
- Todas las conexiones exteriores deben llegar por HTTPS/WSS. No publiques el puerto sin TLS; no abras el servicio en una red pública usando HTTP.
- `DORADO_ALLOWED_ROOTS` limita las rutas aceptadas por las herramientas de archivos, pero **no es un sandbox**: el shell puede alcanzar todo lo que permita tu usuario del sistema.
- `write_file` no sobreescribe por defecto; `edit_file` exige coincidencia única. Los comandos tienen timeout, salida limitada y una concurrencia acotada.
- Para revocar **todas** las sesiones OAuth puedes parar el servidor, hacer una copia de seguridad y borrar `data/dorado.sqlite3` (esto elimina los registros de clientes y tokens). Cambiar solamente la contraseña NO invalida tokens previamente emitidos.
- Detener el agente corta el acceso a ese equipo; cambiar `DORADO_AGENT_TOKEN` en el servidor y los agentes invalida el token anterior.
- El endpoint de registro OAuth es público para soportar clientes dinámicos; protégete de abuso externo mediante firewall, proxy y rate-limiting. Esta versión no proporciona gestión avanzada de usuarios, ni aislamiento entre propietarios, ni auditoría persistente.

## Desarrollo

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
pytest -q
```

La CI prueba autenticación, rechazo de tokens, autorización OAuth/PKCE, refresh, herramientas MCP, WebSocket de dispositivo, descubrimiento de dos PCs a la vez, terminales interactivas reales, continuidad de directorio y recuperación de salida. No sustituye a una prueba E2E con ChatGPT real, TLS y un equipo remoto.

## Licencias y atribución

Este código es una implementación **independiente MIT**. El repositorio `desktop-commander/remote-desktop-commander` **no** publica el servicio hospedado como código abierto; su licencia permite consultar la documentación pero **no** copiar ni redistribuir la implementación propietaria. El proyecto `wonderwhy-er/DesktopCommanderMCP` ofrece por separado un servidor MCP local MIT, que podría integrarse en una evolución futura.

- Proyecto de referencia: https://github.com/desktop-commander/remote-desktop-commander
- Servidor MCP local MIT: https://github.com/wonderwhy-er/DesktopCommanderMCP
- Especificación MCP: https://modelcontextprotocol.io/specification/2025-06-18/basic/transports
- ChatGPT developer mode: https://help.openai.com/en/articles/12584461-developer-mode-and-full-mcp-connectors-in-chatgpt
