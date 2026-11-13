# Dorado Relay: usarlo en casa sin contratar servidor (gratuito)

**No necesitas comprar un VPS ni contratar Dorado Cloud.** Tu propio ordenador puede ejecutar tanto el **relay MCP (servidor)** como el **agente que controla ese ordenador**. Si tienes varios PCs, también puedes conectar sus agentes al mismo relay doméstico. El equipo que aloja el relay debe permanecer encendido y conectado a Internet cuando quieras acceder a distancia.

**El software autoalojado no tiene cuota por dispositivo.** Sigues pagando la electricidad y tu conexión habitual a Internet, y podrías tener costes de dominio o del cliente de IA si eliges servicios de pago. La skill de Agensi es independiente: si la compras, funciona con esta instalación gratuita y no exige una suscripción Cloud.

## 1. Antes de instalar: comprueba si puedes abrir puertos

1. Entra en la configuración de tu router y busca la **IP WAN/Internet** (no es la IP local `192.168.x.x` del PC).
2. Contrasta esa IP WAN con la IP que Internet ve para tu conexión (por ejemplo, un comprobador público de IPv4). Si **no coinciden**, tienes CG-NAT o una segunda capa de NAT y hay que resolverla antes de continuar.
3. Si la WAN está en `10.x.x.x`, `172.16–31.x.x`, `192.168.x.x` o `100.64–100.127.x.x`, no es una IPv4 pública alcanzable directamente. **Abrir puertos en tu router no será suficiente**.
4. Si tienes una IPv4 pública y controlas el router y firewall, puedes hacer el reenvío. Si hay dos routers, tendrás que configurar ambos, poner uno en modo puente o una configuración equivalente.
5. **Starlink estándar** utiliza CG-NAT para IPv4 y el router Starlink no admite port forwarding IPv4. Una suscripción/servicio que ofrezca IPv4 pública con router compatible, o una solución de túnel/IPv6 debidamente configurada, sería necesaria; no prometemos que el port forwarding directo funcione en esa red.

Un túnel gestionado puede ser una alternativa opcional cuando no hay IPv4 pública, pero **no está incluido ni validado en el instalador doméstico**. No debe presentarse como un requisito de pago ni como parte del producto que esté ya funcionando.

## 2. Necesitas un nombre DNS (puede ser gratis)

El cliente MCP se conectará por HTTPS. No basta con escribir una IP pública en un navegador: el nombre DNS y el certificado deben ser válidos.

- **Opción gratuita con IP dinámica:** registra un subdominio en [DuckDNS](https://www.duckdns.org/), por ejemplo `mi-relay.duckdns.org`. Haz que apunte a tu IPv4 pública y configura la actualización periódica **en el router** si soporta DuckDNS o mediante las instrucciones oficiales de DuckDNS en el equipo anfitrión. No compartas el token DDNS ni lo añadas al repositorio.
- **Opción con dominio propio:** crea un registro DNS A (y AAAA solo si realmente tienes IPv6 pública accesible). Configura DDNS si la IP cambia.
- **Para pruebas con IP pública estable:** un nombre del tipo `TU-IP-PUBLICA.sslip.io` puede resolver sin registrar una cuenta DNS. Si la IP cambia, el nombre anterior dejará de apuntar a tu casa; no sustituye DDNS. Evita usarlo como solución estable para clientes.

Comprueba la resolución del dominio desde fuera de tu red. No uses un dominio reservado como `example.com` para una instalación real.

## 3. Elige el PC anfitrión

**Linux** (recomendado para la primera instalación): Ubuntu o similar, con Docker Engine + Docker Compose Plugin, Git y Python 3. Se puede utilizar el mismo PC de trabajo, un mini-PC o una máquina doméstica dedicada.

**Windows 11**: es posible hacer pruebas con Docker Desktop y WSL2, Git y Python 3 (comando `py`), pero falta validar esta ruta completa con una instalación Windows limpia. El equipo no puede suspenderse mientras deba aceptar conexiones.

Reserva una **IP LAN fija** para el PC anfitrión en el DHCP del router, por ejemplo `192.168.1.50`. No uses una IP de ejemplo si no corresponde a tu red.

## 4. Configurar en el PC anfitrión

La candidata sigue en la rama de revisión, **no en `main` ni en una Release estable**. Para probarla ahora, usa una carpeta **nueva** sin copiar contraseñas ni sobrescribir tu relay existente:

```bash
git clone --branch feature/commercial-hardening https://github.com/Doradux/remoteDoradoCommander.git dorado-home-test
cd dorado-home-test
python3 scripts/setup_selfhost.py --home --domain mi-relay.duckdns.org
```

En Windows PowerShell, el último comando puede ejecutarse con `py scripts/setup_selfhost.py --home --domain mi-relay.duckdns.org`.

Genera únicamente:
- `.env`, contraseña de administrador criptográficamente aleatoria y configuración gratuita (no se muestra en pantalla ni se envía a nadie).
- `Caddyfile.home`, que configura HTTPS público y dirige el tráfico **dentro de Docker** a `mcp:8765`.

Ninguno de estos archivos se sobrescribe si existe. **El asistente no arranca contenedores ni realiza cambios en el router.**

En Linux, prepara el directorio de datos privado para el proceso sin privilegios de root:

```bash
mkdir -p data
sudo chown 10001:10001 data
sudo chmod 700 data
docker compose -f compose.yaml -f compose.home.yaml config --quiet
```

En Windows con Docker Desktop, crea primero la carpeta `data` si no existe y revisa los permisos del volumen; los comandos `chown` y `chmod` de Linux no son comandos PowerShell.

## 5. Abrir puertos en tu router

En **Port Forwarding / NAT / Reenvío de puertos**, crea estas dos reglas, dirigidas **al PC anfitrión**, no al PC remoto:

| Protocolo | Puerto externo | IP destino (ejemplo) | Puerto interno |
|---|---:|---|---:|
| TCP | 80 | 192.168.1.50 | 80 |
| TCP | 443 | 192.168.1.50 | 443 |

Abre únicamente esos puertos en el firewall del anfitrión; **no abras 8765** a Internet. La aplicación del relay se publica solo en `127.0.0.1:8765` del propio anfitrión, mientras que el proxy Caddy publica HTTPS por 443. El agente que se instala en cualquier PC abre una conexión saliente, por lo que **no tienes que abrir puertos en cada ordenador controlado**.

Caddy solicita y renueva automáticamente un certificado HTTPS para el dominio cuando el DNS y el acceso público a los puertos correspondientes funcionan. No desactives la validación TLS del cliente.

## 6. Arrancar voluntariamente

Cuando las reglas, DNS y firewall estén listos:

```bash
docker compose -f compose.yaml -f compose.home.yaml up -d --build
docker compose -f compose.yaml -f compose.home.yaml ps
docker compose -f compose.yaml -f compose.home.yaml logs caddy
curl http://127.0.0.1:8765/health
curl https://mi-relay.duckdns.org/.well-known/oauth-authorization-server
```

Desde una conexión **externa** (por ejemplo, el móvil con Wi-Fi desactivado) abre `https://mi-relay.duckdns.org/pair` y comprueba que carga con HTTPS válido. Las pruebas desde la LAN pueden fallar por falta de NAT loopback/hairpin incluso cuando funcionan desde Internet; verifica siempre desde una red realmente externa.

Para apagar los servicios de **esta instancia de prueba**, desde su carpeta:

```bash
docker compose -f compose.yaml -f compose.home.yaml down
```

No uses `down -v` salvo que realmente quieras **eliminar también los datos de certificados**. Mantén copias de seguridad de `data` y los volúmenes de Caddy; nunca compartas el fichero `.env`.

## 7. Vincular el propio PC o PCs adicionales

El anfitrión puede controlar **su propio sistema operativo**, pero solo cuando también se ejecuta allí el agente. Tener el contenedor MCP por sí solo **no da acceso al ordenador**.

- Instala `remote-dorado-agent` en **ese mismo PC** si quieres controlarlo; opcionalmente instálalo en los demás dispositivos.
- En la candidata sin Release pública, para pruebas con Python y un entorno virtual puedes instalar el agente desde este checkout. Las versiones estables tendrán un ejecutable desde GitHub Releases para Linux, Windows y macOS.
- Inicia el agente, introduce `https://mi-relay.duckdns.org`, elige nombre de equipo y los permisos `inspect` (solo lectura), `files` (editar archivos sin shell) o `full` (comandos y procesos). El primer asistente recomienda `inspect`.
- Autoriza la vinculación en el navegador de tu propio relay usando la contraseña privada de `.env` **sin enviársela a un agente ni pegarla en el chat**.

**Si el propio equipo no puede conectarse a su dominio público** por falta de NAT loopback, configura la resolución DNS local para que ese nombre apunte a su IP LAN o revisa la configuración del router. No sustituyas la URL pública del relay por `http://192.168...` para clientes externos.

## 8. Conectar ChatGPT u otro cliente MCP

Configura en el cliente compatible el endpoint:

`https://mi-relay.duckdns.org/mcp`

Autoriza mediante OAuth en el navegador y comprueba primero `list_devices` y `ping_device`. El cliente MCP accede al relay por HTTPS; el relay contacta con el agente mediante WebSocket autenticado. Los permisos del cliente de IA y sus limitaciones son independientes de Dorado Relay.

## Solución de problemas frecuentes

| Problema | Qué comprobar |
|---|---|
| Accede por LAN, pero no por Internet | CG-NAT, doble NAT, WAN pública, reglas TCP 80/443, firewall del anfitrión y DNS |
| No se expide certificado | DNS A/AAAA correcto, puertos 80/443 accesibles desde fuera, logs de Caddy |
| Funciona fuera, pero no desde casa | NAT loopback del router o DNS local |
| Tras cambiar de IP deja de funcionar | Actualizador de DDNS / registro A |
| ChatGPT conecta, pero no aparece el PC | El **agente** no está ejecutándose, usa otro relay o no ha sido autorizado |
| No puede ejecutar comandos | El PC está en permiso `inspect` o `files`; cambiarlo expresamente en el dispositivo |
| El equipo se apaga o duerme | Debe permanecer encendido para que el relay y el agente respondan |
| El router usa Starlink o WAN 100.64.x.x | CG-NAT; un reenvío IPv4 directo no funcionará sin otra solución |

**Referencias oficiales:** [Caddy HTTPS](https://caddyserver.com/docs/quick-starts/https), [DuckDNS](https://www.duckdns.org/), [Starlink IPv4/CG-NAT](https://starlink.com/ws/support/article/1192f3ef-2a17-31d9-261a-a59d215629f4).

**Estado:** montaje doméstico destinado a prueba supervisada. Antes de un lanzamiento comercial abierto todavía procede validación independiente de seguridad, pruebas en redes ajenas y la revisión de Agensi. No se garantiza que todos los routers/ISP permitan conexiones entrantes.
