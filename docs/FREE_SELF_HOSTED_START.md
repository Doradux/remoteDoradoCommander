# Dorado Relay — versión gratuita y autoalojada

**¿No quieres pagar un servidor?** [**Guía para montarlo en tu propio PC, con Caddy, DDNS y port forwarding**](HOME_SELF_HOSTED.md). Esta es la opción recomendada para los compradores de Agensi que tengan IPv4 pública y puedan abrir puertos. Usa `python3 scripts/setup_selfhost.py --home --domain mi-relay.duckdns.org` y después Docker Compose; el proxy HTTPS queda incluido. Con CG-NAT no funciona el port forwarding IPv4 directo. El PC anfitrión debe permanecer encendido.

**Funciona independientemente de Agensi y de Dorado Relay Cloud.** No requiere una suscripción, una cuenta del proveedor, una compra adicional ni pagar por dispositivo. El servidor MCP, los agentes y la documentación pública están en el repositorio. El alojamiento propio y el cliente de IA pueden tener sus propios costes y requisitos.

**Nota de desarrollo (noviembre de 2026):** los cambios todavía están en la rama `feature/commercial-hardening`. El comando siguiente apunta a esa rama. Cuando esté validada y fusionada, la instalación pública utilizará `main`. No se han anunciado Releases de esta versión.

Esto es un procedimiento para una instancia privada de un único propietario. **No aloja varios clientes independientes en el mismo relay.**

## Preparación (Linux con dominio y HTTPS)

Necesitas un equipo Linux o VPS bajo tu control, un nombre de dominio que resuelva a él, Docker y Docker Compose. Para usar clientes MCP externos, el servidor debe estar disponible por **HTTPS público**; no uses directamente HTTP en Internet.

```bash
git clone --branch feature/commercial-hardening https://github.com/Doradux/remoteDoradoCommander.git
cd remoteDoradoCommander
python3 scripts/setup_selfhost.py --domain mcp.tudominio.com
```

El asistente no realiza peticiones de red ni instala software. Genera `.env` (modo 0600 en Unix) con contraseña aleatoria privada, modo gratuito ilimitado en dispositivos y URL pública; también genera `Caddyfile.dorado` con un ejemplo de proxy HTTPS. Si ya existen esos archivos, **se niega a sobrescribirlos**.

**La contraseña de administración no se muestra en la consola**. Solo se guarda en `.env`. Cópiala a tu gestor de contraseñas por un canal privado. Este archivo nunca debe subirse a GitHub ni compartirse con un agente de IA.

## Arrancar voluntariamente

Comprueba el dominio DNS y configura Caddy (o un proxy HTTPS equivalente) en la misma máquina, usando el ejemplo generado. Antes de arrancar revisa el puerto 8765.

```bash
mkdir -p data
sudo chown 10001:10001 data
sudo chmod 700 data
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:8765/health
curl https://mcp.tudominio.com/.well-known/oauth-authorization-server
```

La API HTTP se expone únicamente por `127.0.0.1:8765` en el host; el proxy debe ser la única puerta de entrada desde Internet. Habilita firewall, copias de seguridad cifradas, monitorización, actualizaciones y protección contra fuerza bruta antes de utilizarlo para tareas sensibles.

No es necesario tocar Dorado Relay Cloud para instalar este servidor.

## Vincular tu ordenador

Descarga el agente desde [GitHub Releases](https://github.com/Doradux/remoteDoradoCommander/releases) y verifica sus sumas SHA256 mediante el instalador. Los instaladores son públicos y no requieren una cuenta de GitHub. Linux/macOS:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/Doradux/remoteDoradoCommander/main/scripts/install.sh) agent
remote-dorado-agent
```

Windows: usa el instalador de PowerShell del repositorio según el README. En el primer arranque el agente te pedirá nombre de equipo, URL del relay y permisos de acceso. Después abre la autorización de vinculación en el navegador.

**No introduzcas contraseñas, API keys o tokens de dispositivo en el chat.** El navegador se ocupa de la vinculación.

### Permisos locales por equipo

| Perfil | Acceso remoto |
|---|---|
| `inspect` (predeterminado para instalaciones nuevas) | Listar y leer archivos, información de equipo y estado de sesiones; **sin escritura ni shell** |
| `files` | Lectura, creación y edición de archivos; **sin shell** |
| `full` | Lectura, escritura, comandos y procesos interactivos |

El perfil lo elige el usuario **en el propio ordenador** durante el asistente. Un cliente MCP no puede ampliarlo mediante argumentos. Puedes reconfigurarlo ejecutando `remote-dorado-agent --setup` (repite la vinculación) o seleccionar temporalmente `remote-dorado-agent --permission files` / `--permission full` en el propio PC. `--read-only` fuerza inspección aunque la configuración guardada tenga más privilegios.

Los agentes instalados antes de existir estos perfiles conservan por compatibilidad el modo completo cuando no tienen ninguna configuración de permisos: revisa expresamente los permisos si actualizas una instalación existente.

**Ninguno de estos perfiles es una sandbox del sistema operativo.** Los permisos se ejecutan con la identidad del usuario local; el modo `full` da una shell real con sus privilegios. Utiliza cuentas de OS restringidas, carpetas permitidas y máquinas de prueba para operaciones arriesgadas. Las carpetas permitidas restringen herramientas de archivos y directorios de arranque, pero **no** limitan lo que puede alcanzar un comando shell.

## Conectar el cliente MCP

Indica el endpoint `https://mcp.tudominio.com/mcp` a tu cliente compatible, realiza la autorización OAuth en tu dominio y comprueba primero `list_devices` y `ping_device`. Los permisos concretos de acciones MCP dependen también de tu cliente y del plan o entorno del propio cliente de IA.

La skill de Agensi aporta flujos y procedimientos de trabajo adicionales para agentes, pero **no aporta hosting ni concede permisos**. Incluso si compras la skill, el relay gratuito anterior ya funciona por sí solo. El servicio Cloud de pago es independiente y opcional.

## Limitaciones antes de ofrecer el producto al público

No hay todavía 2FA, auditoría de seguridad externa, garantías SLA ni recuperación automática de cuenta. Revisa el estado de este proyecto en [COMMERCIAL_LAUNCH_PLAN.md](COMMERCIAL_LAUNCH_PLAN.md). El desarrollo de una nueva experiencia comercial no implica que el servicio Cloud esté desplegado o que Agensi haya aprobado la publicación.
