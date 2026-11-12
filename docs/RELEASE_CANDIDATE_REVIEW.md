# Dorado Relay 0.5.0rc1 — revisión previa al visto bueno

**Fecha de preparación:** 13 de noviembre de 2026
**Estado:** candidato técnico para **piloto autoalojado supervisado**; **NO autorizado para publicación, cobro o producción de terceros**.
**Rama:** `feature/commercial-hardening` en `Doradux/remoteDoradoCommander`. No se ha fusionado con `main`.
**Principio de lanzamiento:** la skill de Agensi utiliza una versión gratuita autoalojada **sin depender de Cloud**. Cloud sigue siendo una fase independiente.

## 1. Qué está implementado

| Componente | Estado |
| --- | --- |
| Relay MCP con OAuth PKCE, dispositivos múltiples y WebSocket | Funcional y probado localmente |
| Instalación gratuita guiada del relay (sin sobrescribir secretos) | Funcional y probado con Docker Compose |
| Instaladores Linux/macOS/Windows por GitHub Releases | Automatización preparada; faltan pruebas externas de Release en esos sistemas |
| Perfil local `inspect` / `files` / `full` | Funcional; nuevas vinculaciones recomiendan `inspect`; instalaciones antiguas conservan perfil anterior |
| Cola bounded de tareas en agentes (8 trabajadores, 24 pendientes) | Funcional; exceso de carga rechazada |
| Límite global de ejecución concurrente en relay (16) | Funcional; exceso de carga rechazado pronto |
| Shell puntual con salida y tiempo acotados; limpieza de grupo de procesos POSIX | Funcional en Linux, pruebas de timeout y procesos hijos |
| OAuth DCR, aprobación, tokens y revocación | Funcional, con límites de peticiones y limpieza de caducados |
| Portal de cliente aislado, login, activación, revocación y CSRF | Funcional; sin MFA ni recuperación automatizada |
| Frontend React exclusivamente con Animate UI + Tailwind | Integrado; siete pantallas renderizadas en pruebas SSR |
| Docker con usuario no privilegiado y activos estáticos incluidos | Compilado y comprobado |
| Skill de Agensi separada del repositorio | ZIP privado preparado y comprobado localmente, sin subir |
| Centro de control Stripe + cuotas por firma | Prototipo y simulaciones, **no comercializable aún** |

## 2. Pruebas realizadas

- Tests Python de autenticación, OAuth, token replay, emparejamiento, permisos por equipo, ejecución remota, límites HTTP, caducidad, sesiones, UI y facturación simulada.
- Test real de relay + agente + OAuth por TCP en `127.0.0.1` con credenciales y base de datos efímeras: vincular, leer, editar, denegar comandos no autorizados y revocar.
- Compilación PyInstaller real de Linux x64: binario autónomo construido en contenedor desechable, `--check-assets` OK (Animate UI y licencia).
- Wheel Python: el archivo contiene manifest, JS/CSS y licencia de Animate UI.
- `docker compose config` en un directorio descartable creado con el asistente de instalación gratuita.
- `npm audit --omit=dev`: sin vulnerabilidades reportadas en dependencias de producción al revisar.
- `pip-audit`: sin vulnerabilidades reportadas tras actualizar pip 26.3 en la imagen de prueba; las imágenes de runtime también lo fijan.
- QA visual inicial con Firefox headless de dashboard, login, vinculación y OAuth sobre la **vista previa estática**. No equivale a auditoría de accesibilidad ni a prueba de compra real.
- `git diff --check`, compilación Python, build frontend determinista.

Los tests no garantizan ausencia de fallos o vulnerabilidades. Verificar CI de GitHub para el último commit.

## 3. Límites y riesgos antes de publicar

**Bloqueadores de salida comercial para todos los usuarios:**

1. Revisión independiente de ejecución remota, amenazas de prompt injection, autorización OAuth, aislamiento por cuentas, dependencias, política de logs, abuso y límites.
2. Prueba manual de instalación limpia con PC real Windows y Linux (o macOS) y con un cliente MCP compatible conectado mediante HTTPS real.
3. Revisión de marca en EUIPO/OEPM/TMview y comprobación de derechos de propiedad intelectual/terceros. El uso de Animate UI requiere mantener su aviso MIT + Commons Clause.
4. Documentación legal de privacidad, condiciones, cookies, soporte, costes de alojamiento, tratamiento de datos y obligaciones fiscales según el público.
5. Publicar un repositorio/release verificable y validar binarios con SHA256 antes de presentar la skill como producto disponible.
6. Aprobar la identidad comercial, descripción, precio y pago de creador en Agensi; pasar su escaneo y revisión manual.

**Cloud de pago NO forma parte de este lanzamiento candidato:** todavía necesita pruebas Stripe Test Mode y pagos reales, Checkout, Portal de facturación, reconciliación automática de cuotas, renovación de manifiestos, correo verificado, MFA/passkeys, recuperación, infraestructura aislada por cliente, TLS, copias de seguridad/restauraciones, observabilidad y políticas GDPR. No prometer acceso Cloud ni cobrar antes de completar esas tareas.

**Limitación de seguridad:** `full` permite comandos arbitrarios como el usuario local, con acceso a sus archivos y red; ningún control de directorio es una sandbox de sistema operativo. `inspect` impide escrituras y shell, pero **también puede leer datos sensibles** a los que tenga acceso la identidad local. Evitar cuentas administrativas, perfiles con secretos y sistemas productivos para los primeros pilotos.

## 4. Montaje de prueba autoalojada (después del visto bueno)

**Requisitos elegidos por el propietario:** dominio/subdominio propio, servidor Linux controlado, DNS apuntando al servidor, puertos 80/443 en el firewall para Caddy, Docker y Docker Compose. Nunca usar la máquina doméstica para clientes externos.

Primero, **tras autorizar la fusión y publicar la versión**:

```bash
git clone https://github.com/Doradux/remoteDoradoCommander.git
cd remoteDoradoCommander
python3 scripts/setup_selfhost.py --domain mcp.TU-DOMINIO.com
```

El asistente crea `.env` con contraseña fuerte no mostrada y `Caddyfile.dorado`, sin arrancar nada. Los archivos existentes se respetan. Ajustar DNS, configurar Caddy o un proxy TLS equivalente y revisar la configuración (no copiar el password al chat).

```bash
mkdir -p data
sudo chown 10001:10001 data
docker compose up -d --build
curl http://127.0.0.1:8765/health
curl https://mcp.TU-DOMINIO.com/.well-known/oauth-authorization-server
```

Debe haber una sola ruta pública segura HTTPS al relay; el contenedor principal solo publica `127.0.0.1:8765` al host. **No expongas ese HTTP directamente**. Configura protección de login, proxy, firewall, monitorización y copia/restauración.

En el PC cliente, descarga el instalador del **GitHub Release aprobado** y comprueba SHA256. Inicia `remote-dorado-agent`, elige nombre, dirección `https://mcp.TU-DOMINIO.com`, perfil `inspect` al principio y confirma el código de vinculación en el navegador. Cambia al perfil `files` o `full` **solo** en ese PC cuando necesites esas capacidades.

En el cliente MCP configura `https://mcp.TU-DOMINIO.com/mcp`, completa OAuth y prueba `list_devices` y `ping_device` antes de acciones sensibles.

**Skill Agensi:** el borrador y el ZIP privados están en `/home/marcos/dorado-relay-private` del equipo de desarrollo. Revisar `LISTING_DRAFT.md` y `agensi-skill-REVIEW-v3.zip`, activar el perfil de creador y el método de pago en Agensi y, **solo tras aprobación**, subir la skill. Evitar cualquier promoción de productos de pago externos en la ficha o la skill. Agensi exige su propia aprobación y scan.

## 5. Qué debe aprobar el propietario

- [ ] Nombre «Dorado Relay», dominio/logo y estrategia de propiedad intelectual.
- [ ] Lanzamiento inicial de **relay gratuito + skill Agensi** antes de Cloud, o preferencia por esperar a tener Cloud.
- [ ] Precio de la skill (**19 USD, pago único**, aún propuesto).
- [ ] Texto de la ficha/ZIP y capturas de pantalla.
- [ ] Proveedor/servidor para un piloto real, coste, DNS, políticas de backups y soporte.
- [ ] Autorización explícita para fusionar a `main` (el flujo de Releases puede **publicar automáticamente**).
- [ ] Evaluación legal y auditoría externa requeridas antes de un lanzamiento público no supervisado.

**No realizado:** fusión a main, creación de Release público, publicación en Agensi, configuración Stripe, contratación VPS, exposición de servidor doméstico, cobros o envío de datos de clientes.
