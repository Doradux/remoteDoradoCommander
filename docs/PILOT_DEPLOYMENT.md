# Isolated Cloud pilot (operator runbook)

**Not a public SaaS or a tested production deployment.** This describes how an operator could create a staging/pilot relay **without ever connecting customers to the home installation**.

## Architecture and trust boundary

- Every paying pilot customer receives **one distinct Dorado server process**, one distinct SQLite volume, one random administrator password, one independent OAuth issuer URL, and its own device namespace.
- Ports are bound to **127.0.0.1 only** on the VPS host and reachable to the outside world through a TLS reverse proxy (Caddy). Device agents connect outbound to that customer's subdomain.
- No two customers may share a process, database, token store, administrator password or device name namespace.
- No billing or account management is supplied here. Changing a plan's number of seats is still a manual, authorized operator action.
- These container settings restrict resources and reduce exposure, but **do not guarantee cryptographic isolation against a malicious container**. A penetration test and hardened VPS configuration remain launch blockers.

## Staging preparation on a separate EU VPS

1. Secure and patch the Ubuntu host, configure an operator-only SSH key and firewall, provision nonpublic backups and a domain (e.g., `alice.relay.yourdomain.com`) pointing at the VPS. Allow inbound 443 and, for certificate issuance, 80 as required by the chosen TLS method. Never publish the backend port.
2. Build an image from an independently audited release commit in this repository, rather than pulling a mutable image from an untrusted source:

   ```sh
   docker build -t dorado-relay:pilot .
   ```

   Prefer an immutable image **digest** for real production rollout and validate the build provenance.

3. Generate a **separate** staging tenant; use an unused localhost port for each:

   ```sh
   python3 scripts/provision_pilot.py \
     --base-dir /srv/dorado/customers \
     --customer-id alice \
     --domain alice.relay.example.com \
     --port 19001 \
     --seats 2
   ```

   This creates a mode-0600 `.env` with a unique random password, a mode-0600 Caddy fragment, a Compose file and an inspectable nonsecret `manifest.json`. The script does **not** deploy, buy infrastructure, start services, modify DNS, or update a real reverse proxy.

4. Review the output. **Do not** run a container with `DORADO_AGENT_TOKEN` set or use the home relay's credentials, hostname or production SQLite database. Provision only when the domain/port is allocated and the customer account has been confirmed by the operator.

5. For controlled staging deployment, run the instance from its directory:

   ```sh
   cd /srv/dorado/customers/alice
   docker compose config --quiet
   docker compose up -d
   ```

   Docker Compose uses a project-specific named volume `data`. Check the running process can write to `/app/data`, and that `http://127.0.0.1:19001/health` is accessible **only from the VPS host**.

6. Add the Caddy fragment **manually** after checking DNS and tenant ownership, e.g. place its contents under the operator-managed Caddy configuration directory and validate before reload:

   ```sh
   sudo install -m 0644 /srv/dorado/customers/alice/Caddyfile.fragment /etc/caddy/conf.d/alice.caddy
   sudo caddy validate --config /etc/caddy/Caddyfile
   sudo systemctl reload caddy
   ```

   Your main `/etc/caddy/Caddyfile` must import that directory. The fragment itself is not a standalone Caddy deployment. Adjust to the actual OS and Caddy installation, which may differ. A private source directory prevents an unprivileged Caddy user from reading the original directly.

7. Verify externally over HTTPS, validate OAuth discovery, test an unauthorized MCP call returns HTTP 401, and enroll only a test device with a disposable OS identity. Never attach a customer's device to the operator's personal relay.

## Cuotas firmadas (piloto experimental)

Puedes generar una instancia con `--signed-entitlements` si el operador dispone de `DORADO_ENTITLEMENT_MASTER_KEY` (64 caracteres hexadecimales aleatorios): esa instancia arranca con **0 plazas para nuevas vinculaciones** y solo acepta manifiestos firmados para ese cliente. Después de verificar un pago en Stripe, el operador puede exportar una nueva cuota y copiarla a `entitlements/current.json` (el directorio está montado en solo lectura dentro del contenedor, y los agentes ya emparejados no pierden acceso si caduca una cuota). Consulta [BILLING_CONTROL_PLANE.md](BILLING_CONTROL_PLANE.md). Esta fase **no automatiza cobros, Checkout ni renovaciones**.

## Portal del cliente y activación

Después de arrancar el relay aislado, genera una invitación de un solo uso con `docker compose exec relay python -m remote_dorado.portal --invite`. El comprador recibirá el código por un canal privado y elegirá su contraseña en `https://SU-SUBDOMINIO/portal/activate`; luego administrará sus equipos en `/portal`. En modo comercial, la aprobación de vinculaciones y OAuth se autoriza desde su sesión, no con tu contraseña maestra. Consulta [CUSTOMER_PORTAL.md](CUSTOMER_PORTAL.md). Aún no hay registro libre, facturación ni soporte multiusuario en un mismo proceso.

## Operator-only administration

All admin APIs require the **specific tenant's administrator password**, read securely from that tenant's secret store; do not log it, put it in URLs or pass it in command-line arguments.

```sh
python3 scripts/device_admin.py oauth-list --relay https://alice.relay.example.com
python3 scripts/device_admin.py oauth-revoke CLIENT_ID --relay https://alice.relay.example.com
python3 scripts/device_admin.py list --relay https://alice.relay.example.com
python3 scripts/device_admin.py revoke DEVICE_NAME --relay https://alice.relay.example.com
```

The script prompts for the admin password without echoing. Revoking an OAuth client immediately invalidates access/refresh tokens and pending authorization codes for that client. Revoking a device invalidates its connection and frees its seat. **Neither action deletes user files on the device**.

## Backups, upgrades and subscription handling

- Snapshot/backup each customer's named Docker volume **separately**, encrypt it, limit access, define a retention policy and test restoration. Never copy a database to another customer's namespace.
- Implement structured logs with secret redaction, alerts, resource monitoring and a disaster recovery exercise before accepting real customers.
- Upgrade the container image on a canary customer's staging instance first. Check schema compatibility and database restore.
- Do not promise the service automatically suspends/restores paid seats: there is **no Stripe integration**, no webhooks, no customer dashboard and no automated entitlement reconciliation.
- A subscription price is only a proposal until measured support, VPS resource consumption, egress, payment fees and taxes support it.
- One SQLite-backed process per customer is suitable for evaluating isolation with a small pilot but **not** a scalable horizontally sharded architecture. If load tests justify it, build a separate tenant-aware control plane and distributed message routing.

## Minimum manual acceptance test

- [ ] Customer A cannot see or operate customer B's devices or sessions, even after attempting to reuse a device name.
- [ ] A's password, OAuth token and agent credential cannot authenticate to B's relay.
- [ ] A third device is refused with 2 paid seats; re-pairing an existing device succeeds.
- [ ] Owner revocation takes effect immediately for an already-connected WebSocket.
- [ ] Owner revocation of an MCP OAuth client invalidates both its access and refresh tokens.
- [ ] Multiple idle WebSockets, reconnection, bursts and large outputs remain stable within memory/CPU budgets.
- [ ] Backups restore correctly to a completely separate staging container.
- [ ] No residential IP or shared credential appears in installer or release artifacts.
- [ ] Legal/IP, brand, security and data protection reviews completed before paid launch.

**No steps in this document have been executed against any public VPS.**
