# Dorado Relay — centro de facturación (piloto privado)

**Estado:** implementación y pruebas locales. No hay precios ni pagos activados, ni cuenta Stripe configurada. Nada se ha desplegado en la nube. NO apto aún para clientes reales.

El servicio `remote_dorado.control_plane` es totalmente distinto del MCP relay. Tiene su propia base de datos SQLite (un proceso/worker por ahora) y nunca recibe conexiones de los agentes.

## Modelo y cuotas

- Suscripción base: **2 dispositivos** (precio propuesto 6,90 €/mes + IVA).
- Cada dispositivo extra: **cantidad del segundo precio recurrente de Stripe** (propuesta 1,90 €/mes + IVA por unidad).
- Los precios de verdad los decide el Dashboard de Stripe. No hay llamadas que creen precios, clientes, pagos, suscripciones, checkout ni portal de facturación.
- Los clientes se registran **manualmente** en la base de datos central únicamente tras comprobar su identidad de Stripe. La suscripción y el cliente Stripe deben coincidir exactamente con lo registrado.
- Un webhook con firma Stripe válida provoca que el centro de control consulte el **estado actual** de la suscripción a la API de Stripe por HTTPS. La cantidad del webhook NO se usa directamente.
- `active`: se admite la base más extras solo si `latest_invoice.status == paid`, no hay `pending_update` y todos los ítems tienen exactamente los Price IDs esperados. **Para extras**, la última factura pagada debe contener una línea con el Price ID del dispositivo adicional y cantidad igual o superior a la contratada; si el prorrateo no permite demostrarlo automáticamente, se bloquea la ampliación hasta revisión.
- `trialing`: admite 2 dispositivos, pero no extras de pago (sin configurar pruebas gratuitas públicas).
- `past_due`, `unpaid`, `canceled` y similares: la cuota autorizada para **nuevas altas** es 0. Los agentes ya emparejados pueden seguir conectándose por seguridad y continuidad, en espera de una política de suspensión definida.
- Se comprueba `livemode` en webhook y respuesta Stripe. Se rechazan firmas inválidas, eventos de otro cliente, precios desconocidos, cantidades malformadas, facturas pendientes y cuotas mayores de 100.
- Los eventos son idempotentes por ID y se procesa cada uno consultando el estado vigente a Stripe para evitar orden incorrecto. El registro de IDs de eventos tiene purga a 90 días.
- La autorización de plazas NO está aún aplicada automáticamente al relay: requiere **exportación y entrega por el operador**. Esto evita activaciones accidentales antes del piloto.

**Advertencia de cobros:** verificar solo que la última factura está pagada puede no ser suficiente para todos los escenarios de cambio de cantidad (por ejemplo, prorroteos, cuotas en diferentes períodos o modalidades de pago). Antes de producción hay que fijar el comportamiento de actualización de Stripe (preferiblemente `pending_if_incomplete` donde corresponda), reconciliar eventos `invoice.paid` y hacer pruebas con Stripe Test Clocks, incluyendo 3DS, prorrateos y cambios de plan. Los extras no deben habilitarse sin prueba del cobro aplicable.

Documentación de referencia: https://docs.stripe.com/billing/subscriptions/webhooks y https://docs.stripe.com/api/subscriptions/update .

## Configuración exclusivamente en el servidor central

Variables **secretas** de ejemplo, nunca poner valores reales en GitHub:

```dotenv
DORADO_CONTROL_DB=/app/data/control.sqlite3
DORADO_CONTROL_ADMIN_TOKEN=GENERA_UN_TOKEN_LARGO_ALEATORIO
DORADO_STRIPE_WEBHOOK_SECRET=whsec_CAMBIA_ESTO
DORADO_STRIPE_SECRET_KEY=sk_test_CAMBIA_ESTO
DORADO_STRIPE_BASE_PRICE_ID=price_CAMBIA_ESTO
DORADO_STRIPE_EXTRA_PRICE_ID=price_CAMBIA_ESTO
DORADO_STRIPE_LIVEMODE=0
DORADO_ENTITLEMENT_MASTER_KEY=64_CARACTERES_HEXADECIMALES_DE_32_BYTES_ALEATORIOS
DORADO_CONTROL_BIND_HOST=0.0.0.0
DORADO_CONTROL_PORT=8790
```

- En producción: claves live, `DORADO_STRIPE_LIVEMODE=1`; no mezclar claves de prueba y producción.
- El master key solo reside en el centro de control/terminal de operador confiable, **nunca en los contenedores de los clientes**.
- El servicio almacena los IDs de Stripe y estado de facturación en una base de datos y directorio privados (0600/0700).
- El endpoint público único necesario es `POST /stripe/webhook`, con TLS y encabezado `Stripe-Signature`. La ruta `GET /admin/customers` requiere un token del operador y debe limitarse también por firewall/allowlist. `GET /health` solo responde ok.
- No iniciar con múltiples workers ni replicar SQLite: las reconciliaciones usan un lock por proceso. Para producción distribuida, migrar a PostgreSQL/cola idempotente.

## Servicio central independiente en Docker

Existe una imagen de prueba en `docs/Dockerfile.control` y un Compose privado en `docs/compose.control.example.yaml`. En un VPS **separado del servidor doméstico**, copia `docs/control-plane.env.example` a `.env.control`, reemplaza los marcadores por secretos de Stripe Test Mode y claves aleatorias reales y protégelo con `chmod 600 .env.control`. El Compose publica únicamente el puerto local `127.0.0.1:8790`; el webhook necesitará un reverse proxy HTTPS y medidas de protección en el borde. **No arrancar en producción hasta completar la auditoría de seguridad, reconciliación de cobros y automatización**.

Los comandos de administración necesitan el entorno Python de la aplicación. Dentro del contenedor se pueden usar así (solo después de desplegar de forma autorizada):

```bash
docker compose -f docs/compose.control.example.yaml exec control \\
  python /app/scripts/control_plane_admin.py list
```

Para exportar un manifiesto desde Docker, escribe primero a un subdirectorio privado de `/app/data`, cópialo al host por un canal de operador y reemplaza el archivo destino con **renombrado atómico dentro del directorio montado**. Nunca montes el master key en los relays. Los comandos `python scripts/control_plane_admin.py ...` del resto de este documento suponen que tienes las dependencias instaladas en un entorno Python de operador.

## Desarrollo y pruebas sin cuenta Stripe

```bash
docker build -t dorado-product-test -f tests/Dockerfile.test .
docker run --rm --network none dorado-product-test
```

Las pruebas utilizan eventos Stripe **simulados y firmados** junto con un cliente Stripe simulado. No realizan compras ni llamadas externas.

## Alta manual de un cliente (después de validar su suscripción)

En el entorno del operador (con todas las variables centralizadas correctamente):

```bash
python scripts/control_plane_admin.py register \
  --customer-id alice \
  --email alice@example.com \
  --domain alice.relay.example.com \
  --stripe-customer-id cus_ID_REAL_DE_STRIPE \
  --stripe-subscription-id sub_ID_REAL_DE_STRIPE
```

El registro queda con cuota 0 hasta el primer webhook válido y la posterior reconciliación. Ninguna persona puede darse de alta introduciendo un ID de Stripe por una petición pública.

## Instancia separada con cuotas firmadas

Prepara una instalación mediante el mismo master key (se utiliza **solo** mientras se generan los archivos en la terminal del operador):

```bash
python scripts/provision_pilot.py \
  --base-dir /srv/dorado/customers \
  --customer-id alice \
  --domain alice.relay.example.com \
  --port 19001 \
  --signed-entitlements
```

El generador crea:
- `.env` (modo 0600), con un **derivado HMAC exclusivo para alice**, no el master.
- `entitlements/current.json` firmado y con **0 plazas**, estado `pending` inicial, para impedir altas antes del pago.
- Un montaje `./entitlements:/app/entitlements:ro` y `DORADO_ENTITLEMENT_FILE=/app/entitlements/current.json`. Un manifiesto actualizado se hace visible al relay sin reinicio, por montar el **directorio** y no el archivo individual.
- Configuración Docker/Caddy y manifiesto de instancia, sin desplegar nada.

El JSON firmado no contiene secretos; puede estar en modo 0644 para que lo lea el proceso no privilegiado del contenedor. El directorio de cliente en el host sigue siendo privado.

## Exportar una cuota después de reconciliar el pago

```bash
python scripts/control_plane_admin.py list

python scripts/control_plane_admin.py export-seats \
  --customer-id alice \
  --output /srv/dorado/customers/alice/entitlements/current.json \
  --replace
```

El exportador exige una verificación de Stripe realizada en las últimas **24 horas** y genera una firma para el relay de alice. El JSON caduca a las **48 horas**, por lo que, sin reconciliación y exportación periódica, **se bloquean nuevas vinculaciones**. Los agentes ya autorizados conservan su acceso; esto no elimina archivos ni mata procesos.

Todavía **NO** existe tarea de reconciliación periódica, renovación automática de los archivos, procesamiento distribuido ni un mecanismo de cobro: esos son requisitos antes de un lanzamiento real.

## Próximos requisitos indispensables

1. Reconciliación periódica desde Stripe con reintentos, webhooks + auditoría de facturas prorrateadas y suspensión fiable por impago.
2. Integración real con Stripe Checkout, Billing Portal y el flujo de compra de plazas adicionales, con precios configurados y pruebas en sandbox.
3. Automatizar exportación y entrega de cuotas usando un canal seguro por instancia, sin exponer el master key ni permitir que clientes alteren el fichero.
4. Pasar el centro de control a PostgreSQL/cola para alta disponibilidad; diseñar MFA, recuperación de cuentas y panel de operador.
5. Pruebas de carga, aislamiento entre instancias, gestión de secretos, copia/restauración de DB, auditoría legal y protección de datos.

**No iniciar ningún cobro ni contratar infraestructura sin aprobación del propietario.**
