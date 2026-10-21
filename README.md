# Remote Dorado Commander

[**English**](#english) · [**Español**](#español)

## English

A **self-hosted remote MCP** implementation, developed independently, that lets remote MCP clients (including ChatGPT, where the required capabilities are available) work on computers you own. **No original vendor subscription or per-call quota**: you operate the relay and the device agents yourself. Your hosting infrastructure and AI client/platform may still impose their own limits.


### Architecture

```text
ChatGPT / another MCP client -- HTTPS + OAuth/PKCE --> YOUR RELAY /mcp
                                                       | Authenticated WebSocket
                                                       v
                                      Device agent (outbound connection)
                                                       |
                                               OS / shell / files
```

- **MCP Streamable HTTP** transport, stateless JSON-RPC (`POST /mcp`), without sessions or SSE. JSON responses and `202` for notifications. The `GET /mcp` endpoint returns `405` once authorized.
- **OAuth 2.0**: discovery, dynamic client registration, password-based authorization, PKCE S256, bearer tokens, and rotating refresh tokens. ChatGPT connector authorization requires approval with the relay administrator password.
- **Outbound agent** using `wss://`, multiple named computers, automatic reconnection; no inbound ports are required on the controlled machine. The server remembers previously connected devices and reports whether they are offline.
- **Persistent per-device shell sessions**: multiple sessions simultaneously, with `session_id`, PID, status, working directory, interactive input, and cursor-based output history. Sessions persist between MCP calls and WebSocket reconnects while the agent process continues running.
- **Global session discovery**: `list_all_sessions` queries agent-managed terminal sessions on **all online computers** in parallel, returning the owning device name.
- **Tools**: `list_devices`, `ping_device`, `list_directory`, `read_file`, `write_file`, `edit_file`, `search_files`, `execute_command`, `list_active_sessions`, `list_all_sessions`, `start_process`, `send_input_to_process`, `read_process_output`, `terminate_session`, `forget_session`.
- No vendor service calls, metered billing, or built-in call quotas. Default resource limits include 16 concurrent operations, 250 KB per command output stream, and a 120-second command timeout. These are safety/resource limits, not paid-call limits.

### Pairing a computer

The agent requests a temporary pairing code from **your relay**, opens the authorization page in a browser, and waits for your approval with the relay administrator password. Each computer receives its own credential, stored securely in its local configuration and reused on subsequent launches. Re-pairing a device rotates its credential.

```text
$ remote-dorado-agent
Name for this PC [marcos-hpr]:
Opening https://YOUR-RELAY/pair?code=ABCD-EFGH-JKLM
Enter your relay administrator password and click Authorize.
Device paired! Future launches connect without prompting.
```

**Preset relay address for distributed builds:** set the GitHub Actions repository variable `DORADO_DEFAULT_RELAY_URL=https://mcp.yourdomain.com` under Settings → Secrets and variables → Actions → Variables. Compiled executables include this **public address only**, never passwords or credentials. Deploy your HTTPS relay and configure the variable before building the executable. Without a preset address, the agent asks for the relay URL at first launch.

**Connect ChatGPT:** add a custom MCP server in ChatGPT using `https://mcp.yourdomain.com/mcp` and authorize it using OAuth. Computers paired with the same relay appear in `list_devices`. The local agent does not access your private ChatGPT credentials or login session: your relay handles authorization with OAuth. Available actions depend on ChatGPT account and workspace permissions.

**Commands:** `remote-dorado-agent` starts the agent and opens the pairing flow when needed. `remote-dorado-agent --setup` opens the configuration and pairing flow.

**Full filesystem access (opt-in):** by default, file tools are scoped to the agent user's home directory. On a trusted computer, set `"allowed_roots": "*"` in the local `agent.json` and restart the agent to permit all filesystem paths; alternatively use `DORADO_ALLOWED_ROOTS=*` when launching the agent. This changes **only path validation in the agent**; operating-system permissions still apply. Do not enable it on untrusted machines or relays.

> Security: only approve codes displayed by computers you control. MCP tools can execute commands and modify files with the agent user's privileges. Protect the relay with HTTPS, a strong password, and access controls.

### Quick installation (no repository clone or Python installation)

**Recommended: private, prebuilt downloads from GitHub Releases.** Releases include standalone agents for Linux x64, Windows x64, macOS Intel, and macOS Apple Silicon; a standalone Linux x64 server; a Python wheel; and SHA256 checksums. GitHub Actions builds packages automatically when a version change in `pyproject.toml` is pushed to `main`. See [Releases](https://github.com/Doradux/remoteDoradoCommander/releases).

Because **the repository is private**, install [GitHub CLI](https://cli.github.com/) and sign in to an account with access using `gh auth login` (once per computer). **Never** include credentials in shell commands or make the repository public just to avoid authentication.

**Linux / macOS** (Bash; automatic architecture detection and SHA256 verification; setup occurs when the agent is first launched):

```bash
bash <(gh api -H 'Accept: application/vnd.github.raw' repos/Doradux/remoteDoradoCommander/contents/scripts/install.sh) agent
```

**Windows (PowerShell):**

```powershell
& ([scriptblock]::Create((gh api -H 'Accept: application/vnd.github.raw' repos/Doradux/remoteDoradoCommander/contents/scripts/install.ps1 | Out-String)))
```

After installing, run `remote-dorado-agent`. Enter a **device name** (the hostname is suggested) and authorize the **pairing code** in your browser. If the build has no preset relay address, enter its URL. The agent stores its configuration locally and starts a persistent shell by default. Run `remote-dorado-agent --setup` to change the device settings. **A deployed HTTPS relay is required.**

To install the **Linux x64 server** as a standalone executable (Docker Compose is preferable for a VPS):

```bash
bash <(gh api -H 'Accept: application/vnd.github.raw' repos/Doradux/remoteDoradoCommander/contents/scripts/install.sh) server
```

For **non-interactive installations**, pair each device interactively, then run the agent as a service. Use `DORADO_INSTALL_DIR` to select the Unix installation directory. Rerun the installer to download the latest Release. Builds are **architecture-specific** and do not bundle secrets.

**Alternative pip installation, without cloning:**

```bash
gh release download --repo Doradux/remoteDoradoCommander -p '*.whl' -D /tmp/dorado-wheel
pipx install /tmp/dorado-wheel/*.whl
```

Private GitHub Releases provide executable installation without downloading the source repository or requiring Python on the target PC. GitHub Actions build minutes may be billed depending on your plan.

### Deploying the relay (Linux + Docker)

**Requirements**: a VPS or other server reachable by ChatGPT over HTTPS, Docker and Docker Compose, a domain, and a TLS reverse proxy such as Caddy, nginx, or a suitable secure tunnel.

```bash
git clone git@github.com:Doradux/remoteDoradoCommander.git
cd remoteDoradoCommander
cp .env.example .env
python3 -c 'import secrets; print("DORADO_ADMIN_PASSWORD="+secrets.token_urlsafe(32))'
```

Put the printed **random administrator password** in `.env` in place of `REPLACE_...` and set `DORADO_PUBLIC_URL=https://mcp.YOUR-DOMAIN.com` (without `/mcp`). Do not commit `.env` or share its secrets in chat.

```bash
mkdir -p data
# The Linux Docker image uses UID 10001 for database access:
sudo chown 10001:10001 data
docker compose up -d --build
curl http://127.0.0.1:8765/health
```

The container exposes port `8765` only on the host's loopback interface (`127.0.0.1`). Configure a reverse proxy with HTTPS. **Example Caddyfile** on the same machine:

```caddyfile
mcp.YOUR-DOMAIN.com {
    reverse_proxy 127.0.0.1:8765
}
```

The proxy must support WebSockets, preserve the `Authorization` header, enforce TLS, and rate-limit `/oauth/*` against abuse. For production, add a firewall, encrypted backups of `data/`, and monitoring.

Verify externally from a separate device:

```bash
curl https://mcp.YOUR-DOMAIN.com/health
curl https://mcp.YOUR-DOMAIN.com/.well-known/oauth-authorization-server
curl -i https://mcp.YOUR-DOMAIN.com/mcp
# The last command should return HTTP 401 with WWW-Authenticate.
```

### Install an agent on your own computer

Python **3.11+**; supports Linux, macOS, and Windows. The following is an alternative **development installation** that requires checking out the private repository; use the quick installation above to avoid that.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
export DORADO_ALLOWED_ROOTS="$HOME"
remote-dorado-agent
```

On **PowerShell** (after installing with `py -m pip install -e .`):

```powershell
$env:DORADO_ALLOWED_ROOTS=$HOME
remote-dorado-agent
```

Choose a different name (`marcos`, `marcos-hpr`, `server`, etc.) on each computer the first time; the name is remembered for future launches. Keep the agent running (systemd, a Windows service, tmux, or a process supervisor) for ChatGPT to reach that computer. Without a running agent, it is inaccessible.

### Working with multiple computers and active shells

A **single MCP relay** supports agents on as many computers you control as you need, each with a unique name such as `marcos` (Windows), `marcos-hpr` (Linux), or `ubuntu-server`. Offline devices remain recorded in `known_devices`; local processes survive brief network outages as long as their agent continues running.

- `list_devices`: current connected computers in `devices`, plus historical entries with online/offline status in `known_devices`.
- `list_all_sessions`: all **agent-managed persistent shells** on every online device, with `device` and `session_id`.
- `list_active_sessions(device="marcos-hpr")`: sessions for a specific computer, including completed sessions until discarded.
- `start_process(device="marcos-hpr", cwd="/home/marcos")`: start an interactive terminal; omit `command` to use the default shell, or provide it to launch a persistent command.
- `send_input_to_process(device="marcos-hpr", session_id="...", input="pwd")`: send input and return initial output (defaults to a 200 ms wait; configurable via `wait_ms`).
- `read_process_output(device="marcos-hpr", session_id="...", cursor=123)`: retrieve new output after the last cursor without consuming it or resetting the process.
- `terminate_session` and `forget_session`: close an agent session and release its history.

**Example ChatGPT workflow:** “List all my computers and their active shells” → “On `marcos-hpr`, open a terminal in `/home/marcos/hpr`” → “Run `git status` in that session” → “Read the output” → “Continue using that same terminal.” Device shells are independent; working directories and process-local environment variables persist between interactions.

**External terminal limitation:** the agent can only send input to or capture output from **sessions it created**. It cannot automatically attach to unrelated VS Code, Windows Terminal, GNOME Terminal, SSH, or tmux sessions. `--auto-shell` opens an agent-managed session immediately; omit it if you prefer to start sessions on demand. `list_all_sessions` does **not** claim to enumerate every OS terminal.

Implementation details: Linux/macOS use a pseudo-terminal (PTY); Windows uses a persistent `cmd.exe` process with pipes (suitable for basic commands but not full-screen interfaces such as `vim`/`top`). Each agent allows up to 24 active/retained sessions (configurable in source) with 1 MB of recent output per session. This is a resource limit, not a paid-use quota. **Sessions do not survive agent restarts**; unrelated processes are not automatically imported.

### Connecting ChatGPT

1. Make sure your ChatGPT account/workspace permits custom MCP apps and the **write actions** you need. Availability varies by plan, workspace permissions, and feature rollout.
2. In **Settings → Apps**, enable developer mode if it is available, then select **Create**.
3. Enter your MCP URL: `https://mcp.YOUR-DOMAIN.com/mcp`. Choose OAuth if requested (the relay advertises discovery metadata).
4. On the authorization page **served by your domain**, enter `DORADO_ADMIN_PASSWORD` and approve access.
5. Discover the tools, create/enable the app, then test `list_devices` and `ping_device` in a compatible chat.

**Required for ChatGPT integration:** run the relay at a public HTTPS endpoint and keep agents online on the computers you want to access. Available terminal and file actions depend on ChatGPT permissions.

### Security

⚠️ **Powerful permissions:** `execute_command` gives a remote client real shell execution using the local agent user's privileges. It can delete files, read keys, change repositories, and run software. Only approve clients you trust. Use a low-privilege OS account, ideally inside a VM or container for experimentation.

- The administrator password is stored **only on the relay**; agents get individual credentials after browser approval. Each device's credential is stored locally with restricted file permissions, never shown in the console or embedded in builds.
- All externally reachable traffic must use HTTPS/WSS. Never expose the HTTP service directly to the public Internet.
- `DORADO_ALLOWED_ROOTS` restricts file-tool paths but **is not a sandbox**: shell commands can reach anything the OS user can access.
- `write_file` does not overwrite by default; `edit_file` requires an exact unique match. Commands have timeouts, bounded output, and concurrency limits.
- To revoke **all** OAuth sessions, stop the relay, back up its data, and delete `data/dorado.sqlite3` (which also removes registered clients/tokens). Changing only the administrator password does **not** revoke existing tokens.
- Stopping an agent cuts access to that device. Re-pairing rotates that device's credential.
- The OAuth dynamic registration endpoint is public for compatibility; protect it using a firewall, reverse proxy, and rate limiting. This MVP has no advanced user administration, multi-owner isolation, or persistent audit logging.

### Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
pytest -q
```

CI covers authentication, token rejection, OAuth/PKCE, refresh, browser pairing and credential rotation, MCP tools, device WebSockets, two-computer discovery, real interactive terminal sessions, working-directory continuity, and output recovery. CI is **not** a substitute for an end-to-end test with real ChatGPT, TLS, and a remote machine.

### License and attribution

This is an **independent implementation licensed under MIT**. The repository `desktop-commander/remote-desktop-commander` does **not** publish its hosted implementation as open-source software; its license allows viewing the documentation but **not** copying or redistributing proprietary implementation code. The separate `wonderwhy-er/DesktopCommanderMCP` project provides an MIT-licensed local MCP server that might be integrated in the future.

- Reference project: https://github.com/desktop-commander/remote-desktop-commander
- MIT local MCP server: https://github.com/wonderwhy-er/DesktopCommanderMCP
- MCP specification: https://modelcontextprotocol.io/specification/2025-06-18/basic/transports
- ChatGPT developer mode: https://help.openai.com/en/articles/12584461-developer-mode-and-full-mcp-connectors-in-chatgpt

---

## Español

Un MCP remoto **autoalojado**, creado de forma independiente, para usar tus propios equipos desde clientes MCP remotos (incluido ChatGPT, cuando tu cuenta tenga habilitadas las acciones necesarias). **Sin cuotas de llamadas ni suscripción del proveedor original**: tú alojas el relay y el agente. Siguen existiendo los límites de tu infraestructura y de cada cliente/plataforma de IA.


### Arquitectura

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

### Vincular un ordenador

El agente solicita un código temporal de vinculación a **tu servidor**, abre la página de autorización en el navegador y espera tu aprobación con la contraseña de administración. Cada ordenador recibe una credencial propia, que se guarda de forma segura en la configuración local y se reutiliza en los siguientes arranques. Al volver a vincular un equipo, se renueva su credencial.

```text
$ remote-dorado-agent
Nombre para este PC [marcos-hpr]:
Abriendo https://TU-RELAY/pair?code=ABCD-EFGH-JKLM
Introduce la contraseña de administración de tu relay y pulsa Autorizar.
¡Equipo vinculado! En siguientes ejecuciones no pregunta nada.
```

**Dirección del servidor preconfigurada:** define la variable de repositorio de GitHub Actions `DORADO_DEFAULT_RELAY_URL=https://mcp.tudominio.es` en Settings → Secrets and variables → Actions → Variables. Los ejecutables incluyen únicamente esa **dirección pública**, nunca contraseñas ni credenciales. Despliega el servidor HTTPS y configura la variable antes de compilar. Sin una dirección preconfigurada, el agente solicita la URL del servidor en su primer arranque.

**Conectar ChatGPT:** añade el servidor MCP personalizado en ChatGPT usando `https://mcp.tudominio.es/mcp` y autoriza el acceso mediante OAuth. Los demás ordenadores vinculados al mismo servidor aparecen en `list_devices`. El programa local no utiliza las credenciales ni la sesión privada de ChatGPT: la autorización la gestiona tu propio servidor mediante OAuth. La disponibilidad de las herramientas depende de los permisos de ChatGPT.

**Comandos:** `remote-dorado-agent` inicia el agente y abre la vinculación cuando hace falta. `remote-dorado-agent --setup` abre la configuración y el proceso de vinculación.

**Acceso completo a archivos (opcional):** por defecto, las herramientas de archivos se limitan a la carpeta personal. En un equipo de confianza, establece `"allowed_roots": "*"` en el `agent.json` local y reinicia el agente para permitir cualquier ruta; también puedes usar `DORADO_ALLOWED_ROOTS=*` al iniciar el agente. Esto modifica **solo la validación de rutas del agente** y no evita los permisos del sistema operativo. No lo actives en equipos o servidores MCP que no controles.

> Seguridad: autoriza solamente códigos que aparecen en un equipo tuyo. Las herramientas MCP pueden ejecutar comandos y modificar archivos con los privilegios de ese usuario. El relay debe usar HTTPS, contraseña fuerte y límites de acceso.

### Instalación rápida (sin clonar el repo ni instalar Python)

**Recomendado: descargas binarias privadas desde GitHub Releases.** La Release contiene un agente autónomo para Linux x64, Windows x64, macOS Intel y macOS Apple Silicon; y un servidor autónomo para Linux x64. También se publica un wheel Python y sumas SHA256. La compilación la realiza GitHub Actions automáticamente cuando se sube a `main` una versión nueva en `pyproject.toml`. Puedes consultar los paquetes en [Releases](https://github.com/Doradux/remoteDoradoCommander/releases).

Dado que **el repositorio es privado**, instala [GitHub CLI](https://cli.github.com/) y autoriza la cuenta que tiene acceso con `gh auth login` (una vez por ordenador). **No** expongas credenciales en comandos ni publiques el repositorio para evitar la autenticación.

**Linux / macOS** (Bash; detecta arquitectura automáticamente; verifica SHA256; el asistente de configuración aparece en el primer arranque del agente):

```bash
bash <(gh api -H 'Accept: application/vnd.github.raw' repos/Doradux/remoteDoradoCommander/contents/scripts/install.sh) agent
```

**Windows (PowerShell):**

```powershell
& ([scriptblock]::Create((gh api -H 'Accept: application/vnd.github.raw' repos/Doradux/remoteDoradoCommander/contents/scripts/install.ps1 | Out-String)))
```

Tras instalarlo, ejecuta `remote-dorado-agent`. Introduce un **nombre de equipo** (se propone el hostname) y autoriza el **código de vinculación** en el navegador. Si el ejecutable no tiene una dirección preconfigurada, introduce la URL del servidor. El agente guarda su configuración localmente y abre una shell persistente por defecto. Usa `remote-dorado-agent --setup` para cambiar la configuración. **Es necesario tener el servidor HTTPS desplegado.**

Instalar el **servidor Linux x64** como binario (aunque en un VPS es preferible el Docker Compose de más abajo):

```bash
bash <(gh api -H 'Accept: application/vnd.github.raw' repos/Doradux/remoteDoradoCommander/contents/scripts/install.sh) server
```

Para instalaciones **no interactivas**, vincula cada equipo de forma interactiva y después ejecuta el agente como servicio. Usa `DORADO_INSTALL_DIR` para elegir el directorio de instalación en Unix. Repite el instalador para descargar la última versión. Las compilaciones son **específicas de cada arquitectura** y no incluyen secretos.

**Alternativa pip sin clonar:**

```bash
gh release download --repo Doradux/remoteDoradoCommander -p '*.whl' -D /tmp/dorado-wheel
pipx install /tmp/dorado-wheel/*.whl
```

Las Releases privadas de GitHub permiten instalar ejecutables sin descargar el código fuente ni necesitar Python en el PC. GitHub Actions puede consumir minutos de compilación facturables según tu plan.

### Preparar el servidor (Linux + Docker)

**Requisitos**: VPS/servidor al que ChatGPT pueda llegar por HTTPS, Docker + Docker Compose, un dominio y un proxy TLS (Caddy, nginx, Cloudflare Tunnel o Secure MCP Tunnel si está disponible en tu entorno).

```bash
git clone git@github.com:Doradux/remoteDoradoCommander.git
cd remoteDoradoCommander
cp .env.example .env
python3 -c 'import secrets; print("DORADO_ADMIN_PASSWORD="+secrets.token_urlsafe(32))'
```

Copia la **contraseña aleatoria** impresa a `.env` en lugar de `REPLACE_...`, y configura `DORADO_PUBLIC_URL=https://mcp.TU-DOMINIO.com` (sin `/mcp`). No publiques el `.env` ni envíes secretos por chat.

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

### Instalar el agente en un equipo propio

Python **3.11+**; funciona con Linux, macOS y Windows. Método alternativo de desarrollo (requiere descargar el repositorio privado; para evitarlo usa la instalación rápida anterior):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
export DORADO_ALLOWED_ROOTS="$HOME"
remote-dorado-agent
```

En **PowerShell** (instalación previa con `py -m pip install -e .`):

```powershell
$env:DORADO_ALLOWED_ROOTS=$HOME
remote-dorado-agent
```

En cada equipo, introduce un nombre diferente (`marcos`, `marcos-hpr`, `servidor`, etc.) la primera vez; se recuerda para futuros arranques. El agente debe permanecer ejecutándose (systemd, servicio de Windows, tmux o supervisor de procesos) para que ChatGPT llegue al equipo. Sin el agente, no hay acceso.

### Trabajar con varios PCs y shells activas

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

### Conectar a ChatGPT

1. Comprueba que tu cuenta/espacio de trabajo tenga habilitada la creación de aplicaciones MCP personalizadas y que permita las **acciones de escritura** que necesitas. Estas capacidades dependen del plan y del despliegue de funciones de ChatGPT.
2. En **Configuración → Aplicaciones**, habilita el modo de desarrollador si se ofrece y entra en **Crear**.
3. URL del servidor MCP: `https://mcp.TU-DOMINIO.com/mcp`. Selecciona OAuth si se solicita (el servidor anuncia sus metadatos).
4. En la pantalla de autorización de **tu dominio** introduce el valor `DORADO_ADMIN_PASSWORD` y aprueba el acceso.
5. Escanea las herramientas, crea/activa la aplicación y prueba `list_devices` y `ping_device` en un chat compatible.

**Requisito para integrar ChatGPT:** ejecuta el servidor en un endpoint HTTPS público y mantén los agentes activos en los ordenadores a los que quieras acceder. Las acciones de terminal y archivos dependen de los permisos de ChatGPT.

### Seguridad

⚠️ **Permisos elevados**: `execute_command` es acceso real al shell como el usuario que ejecuta el agente. Es posible borrar datos, leer claves, modificar repositorios o ejecutar software. Autoriza solamente tus propios clientes; usa un usuario del sistema con pocos privilegios, idealmente una VM o contenedor para pruebas.

- La contraseña de administración permanece **solo en el relay**; los agentes obtienen sus tokens individuales tras la aprobación en el navegador. El token de cada PC se almacena localmente con permisos restrictivos; no se muestra en consola ni se incorpora a las builds.
- Todas las conexiones exteriores deben llegar por HTTPS/WSS. No publiques el puerto sin TLS; no abras el servicio en una red pública usando HTTP.
- `DORADO_ALLOWED_ROOTS` limita las rutas aceptadas por las herramientas de archivos, pero **no es un sandbox**: el shell puede alcanzar todo lo que permita tu usuario del sistema.
- `write_file` no sobreescribe por defecto; `edit_file` exige coincidencia única. Los comandos tienen timeout, salida limitada y una concurrencia acotada.
- Para revocar **todas** las sesiones OAuth puedes parar el servidor, hacer una copia de seguridad y borrar `data/dorado.sqlite3` (esto elimina los registros de clientes y tokens). Cambiar solamente la contraseña NO invalida tokens previamente emitidos.
- Detener el agente corta el acceso a ese equipo. Al volver a vincularlo, se renueva su credencial.
- El endpoint de registro OAuth es público para soportar clientes dinámicos; protégete de abuso externo mediante firewall, proxy y rate-limiting. Esta versión no proporciona gestión avanzada de usuarios, ni aislamiento entre propietarios, ni auditoría persistente.

### Desarrollo

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
pytest -q
```

La CI prueba autenticación, rechazo de tokens, autorización OAuth/PKCE, refresh, vinculación por navegador y rotación de credenciales, herramientas MCP, WebSocket de dispositivo, descubrimiento de dos PCs a la vez, terminales interactivas reales, continuidad de directorio y recuperación de salida. No sustituye a una prueba E2E con ChatGPT real, TLS y un equipo remoto.

### Licencias y atribución

Este código es una implementación **independiente MIT**. El repositorio `desktop-commander/remote-desktop-commander` **no** publica el servicio hospedado como código abierto; su licencia permite consultar la documentación pero **no** copiar ni redistribuir la implementación propietaria. El proyecto `wonderwhy-er/DesktopCommanderMCP` ofrece por separado un servidor MCP local MIT, que podría integrarse en una evolución futura.

- Proyecto de referencia: https://github.com/desktop-commander/remote-desktop-commander
- Servidor MCP local MIT: https://github.com/wonderwhy-er/DesktopCommanderMCP
- Especificación MCP: https://modelcontextprotocol.io/specification/2025-06-18/basic/transports
- ChatGPT developer mode: https://help.openai.com/en/articles/12584461-developer-mode-and-full-mcp-connectors-in-chatgpt
