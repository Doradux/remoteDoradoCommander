# Dorado Relay — Portal de cliente (piloto por instancia)

Estado: **funcional en pruebas, no apto todavía para venta ni alojamiento multiusuario compartido**.

**Frontend:** sustituido por React + Animate UI v2.4 + Motion + Tailwind (ver [catálogo completo](ANIMATE_UI_CATALOG.md)). Se han eliminado los formularios HTML y el CSS artesanales que se generaban en Python. La página FastAPI solamente entrega datos JSON escapados y archivos estáticos de origen local, con Content Security Policy restrictiva. Construir con `npm ci --prefix frontend && npm run build --prefix frontend`; los artefactos de producción se guardan en `remote_dorado/static` y se distribuyen con el paquete Python. El JavaScript frontend es obligatorio para visualizar las pantallas web.

## Qué permite

Cada *relay aislado* admite un propietario, con su correo y contraseña independientes de la clave de administración del servidor:

- Página de alta por invitación `/portal/activate`: no existe registro abierto.
- Página de acceso `/portal/login`: hash `scrypt` con sal aleatoria, límite de intentos y sesión en cookie HttpOnly, SameSite=Lax y Secure sobre HTTPS.
- Panel `/portal`: equipos online/offline, ocupación de los 2 dispositivos incluidos (o el límite configurado), conexiones MCP registradas y metadatos de las últimas acciones.
- Desvincular dispositivo: elimina su credencial y desconecta el WebSocket activo.
- Revocar cliente MCP: invalida los tokens OAuth de acceso y de refresco asociados.
- Cambiar contraseña del portal: cierra todas las sesiones del navegador.
- Los formularios que modifican datos requieren sesión y token CSRF.
- En modo comercial, **la vinculación de equipos y la aprobación OAuth ya no requieren introducir la contraseña del operador**: utilizan la sesión y CSRF del propietario. El modo personal mantiene el flujo anterior.

La contraseña de administrador del operador permanece en la configuración privada del relay para operaciones de emergencia y administración; no se entrega al cliente.

## Alta de un cliente — manual, sin pagos ni registro público

1. Desplegar un servidor **exclusivo** para el cliente, con su propia BD y dominio, siguiendo `PILOT_DEPLOYMENT.md`.
2. Comprobar que `DORADO_COMMERCIAL_MODE=1` y que no existe `DORADO_AGENT_TOKEN`.
3. En una terminal interactiva de **ese contenedor** (nunca en el servidor doméstico):

   ```bash
   docker compose exec relay python -m remote_dorado.portal --invite
   ```

4. Introducir el correo del propietario. El sistema muestra **una sola vez** un código de activación aleatorio con validez de 24 horas. En la base de datos se guarda solo su hash.
5. Entregar el código por un canal privado, idealmente vinculado al correo comprobado del comprador. No pegarlo en documentación, repositorios, tickets, logs o canales públicos.
6. El cliente abre `https://DOMINIO-DE-SU-RELAY/portal/activate`, introduce correo, código y crea su contraseña.
7. Tras activarse, inicia sesión en `/portal/login` y vincula los equipos desde el programa local.

Una invitación nueva invalida la anterior. Una vez creado el propietario, el CLI no permite generar otra invitación ni sobrescribir su cuenta.

### Autoalojamiento técnico opcional

`docker compose exec relay python -m remote_dorado.portal --create-owner` crea una cuenta mediante contraseña introducida localmente (nunca como argumento CLI). Para el piloto con compradores, preferir invitaciones.

## Límites y seguridad

- Una cuenta **por proceso y base de datos**. **No admite varios clientes en el mismo relay.** No debe convertirse en una aplicación multiusuario agregando simplemente nuevas cuentas a `portal_owner`.
- No hay Stripe, renovación automática, recuperación por correo, 2FA/passkeys, cambio de correo verificado, eliminación automática por baja ni panel del operador central.
- Las cuentas todavía no se verifican por correo automáticamente. El operador debe comprobar el destinatario de la invitación.
- El cierre de sesión revoca su cookie; el cambio de contraseña invalida todas las sesiones. Token de sesión aleatorio y expiración de 8 horas.
- Un relay comercial sin propietario activado rechaza la aprobación de nuevos equipos y clientes OAuth.
- Los metadatos de actividad contienen fecha/hora UTC, nombre de herramienta, equipo y resultado; **no se guardan el contenido de comandos, rutas de archivo, argumentos ni salidas**. Retención en escritura: 30 días. Una política completa de logs/borrado está pendiente.
- Contraseñas en tránsito siempre con TLS, en repositorio o logs nunca. Los ataques de denegación de servicio y el rate limiting en el gateway necesitan revisión antes de producción.
- Los comandos remotos siguen pudiendo ejecutarse con los privilegios locales del usuario de cada agente. El portal no es un sandbox de sistema operativo.
- El cifrado de volúmenes, recuperación de desastres, copias de seguridad y aislamiento de contenedores aún necesitan validación en VPS.

## Validación automatizada

`docker build -t dorado-product-test -f tests/Dockerfile.test .`

`docker run --rm --network none dorado-product-test`

Incluye regresiones para acceso, cierre de sesión, CSRF, cambio de contraseña, invitación de un solo uso, vencimiento, bloqueo por intentos erróneos, aprobación MCP, revocación de tokens y compatibilidad con el modo personal.

## Próximo hito

Crear el **control plane** central separado del relay para registrar clientes, asignar subdominios, provisionar instancias, gestionar renovaciones de Stripe con webhooks verificados y reconciliar límites de dispositivos sin permitir que un cliente cambie su cuota por sí mismo. Integrar recuperación y verificación de correo, MFA y alertas antes de comercializar.
