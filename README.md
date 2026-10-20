# Remote Dorado Commander

Servidor **MCP remoto autoalojado** y agente para controlar varios ordenadores y sus terminales persistentes desde ChatGPT u otros clientes MCP. Proyecto independiente, MIT. Sin cuotas del proveedor original; los límites de ChatGPT y de tu infraestructura siguen aplicando.

> **MVP v0.4.0:** vinculación por navegador y código temporal. El servidor de enlace debe estar desplegado por ti. El código está probado localmente, pero no se ha validado aún el flujo completo desde una cuenta real de ChatGPT.

## Vinculación sin copiar tokens

1. Instala el agente desde [Releases](https://github.com/Doradux/remoteDoradoCommander/releases), o utiliza los instaladores de abajo.
2. Ejecútalo con `remote-dorado-agent`. La primera vez te preguntará cómo quieres llamar al PC.
3. El programa abrirá en el navegador una página de tu servidor con un **código temporal**.
4. Introduce **la contraseña de administración del servidor de enlace** y confirma ese PC.
5. Listo. El agente guarda su credencial individual de forma privada y la reutiliza en futuros arranques.

Repetir `remote-dorado-agent` no vuelve a preguntar. Utiliza `remote-dorado-agent --setup` para cambiar el nombre o volver a vincularlo. La credencial anterior del mismo nombre se invalida tras autorizar una nueva.

**Importante:** vincular el agente autoriza **tu servidor MCP**, no inicia sesión en OpenAI. ChatGPT debe conectarse una sola vez a ese servidor mediante su conector MCP y el OAuth del relay. Al añadir más PCs, aparecen en el mismo conector sin volverlo a configurar.

### Evitar también la pregunta de la URL

Configura la variable **GitHub Actions → Variables** `DORADO_DEFAULT_RELAY_URL` con tu dirección pública real, por ejemplo `https://mcp.tudominio.es`, y construye una nueva Release. Se integra únicamente una URL **pública**, nunca secretos. Si no se configura esa variable, el agente tendrá que preguntar la URL del relay una sola vez. **No hay un relay público de Dorado desplegado automáticamente por este repositorio.**

## Instalación sin clonar el repositorio

El repositorio es privado: instala [GitHub CLI](https://cli.github.com/) y autorízate una vez con `gh auth login`.

**Linux / macOS**

```bash
bash <(gh api -H 'Accept: application/vnd.github.raw' repos/Doradux/remoteDoradoCommander/contents/scripts/install.sh) agent
remote-dorado-agent
```

**Windows (PowerShell)**

```powershell
& ([scriptblock]::Create((gh api -H 'Accept: application/vnd.github.raw' repos/Doradux/remoteDoradoCommander/contents/scripts/install.ps1 | Out-String)))
remote-dorado-agent
```

El instalador descarga la build de tu sistema desde la Release privada y verifica SHA256. El asistente de vinculación aparece **en el primer arranque** del programa, no durante la instalación. No se necesita Python si instalas los ejecutables.

## Configurar el servidor MCP

Necesitas una máquina accesible por **HTTPS**, un nombre de dominio (o túnel HTTPS autorizado) y una contraseña administrativa fuerte. En Linux puedes arrancarlo mediante `compose.yaml`:

```bash
git clone git@github.com:Doradux/remoteDoradoCommander.git
cd remoteDoradoCommander
cp .env.example .env
# Edita .env: DORADO_PUBLIC_URL y DORADO_ADMIN_PASSWORD
docker compose up -d --build
```

Expón el servidor a través de un proxy TLS que soporte WebSocket. El servicio por defecto escucha en el puerto local `8765`. No publiques ese puerto directamente por internet. Comprueba que `https://TU-DOMINIO/health` devuelve el estado y que la ruta `/mcp` solicita autorización.

En **ChatGPT → Plugins → Añadir servidor MCP personalizado**, introduce `https://TU-DOMINIO/mcp` y autoriza la conexión OAuth del relay. La disponibilidad de ejecución/escritura depende de los permisos y del plan de ChatGPT; no se puede eludir desde un servidor MCP propio.

## Varios PCs y terminales

Inicia un agente en cada ordenador con un nombre diferente. Las herramientas incluyen `list_devices`, `list_all_sessions`, `start_process`, `send_input_to_process`, `read_process_output`, `execute_command`, `list_directory`, `read_file`, `write_file`, `edit_file`, `search_files` y `terminate_session`.

Las shells creadas por el agente conservan estado entre llamadas y durante desconexiones breves del WebSocket mientras el agente siga ejecutándose. **No se pueden interceptar automáticamente terminales externas** de VS Code, SSH u otras aplicaciones.

## Seguridad y pruebas

- Cada PC obtiene una credencial diferente mediante aprobación temporal por navegador; los tokens se guardan por hash en el relay.
- La confirmación requiere contraseña administrativa y tiene caducidad y límites de intentos.
- Utiliza HTTPS, MFA donde esté disponible, firewall y límites de solicitudes adicionales en el proxy.
- Las acciones se ejecutan con permisos del usuario del sistema donde corre el agente; la lista de rutas permitidas **no es un sandbox para comandos de shell**.
- `DORADO_AGENT_TOKEN` solo se admite como compatibilidad heredada; **no lo configures** en una nueva instalación.

Pruebas de desarrollo: `pip install -e '.[test]'` y `pytest -q`. La build automatizada compila Linux x64, Windows x64 y macOS para Intel y Apple Silicon.

Licencia MIT. El servicio remoto comercial Desktop Commander no publica su implementación, por lo que esta alternativa es independiente.
