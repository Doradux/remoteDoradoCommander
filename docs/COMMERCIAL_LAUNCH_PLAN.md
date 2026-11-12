# Dorado Relay — commercial readiness and launch gates

Status: **NOT APPROVED FOR PUBLIC SALE OR SHARED HOSTING**. This is an engineering plan, not a legal opinion.

## Product separation

- **Free self-hosted relay**: distribute the existing MIT source and build artifacts, while preserving copyright and attribution notices. An MIT licence permits third parties to redistribute previously released copies too; a future licence change cannot revoke that permission.
- **Agensi skill**: sell a self-contained `SKILL.md` that teaches agent workflows using a relay the buyer already controls. Agensi does not run the MCP server, deliver executables, or install MCP clients. Agensi's current terms prohibit upselling external paid services from skill listings, packages, or sample outputs. Ask Agensi for explicit written guidance before using a paid hosted relay as a prerequisite.
- **Optional Dorado Relay Cloud**: independent subscription product with server hosting, onboarding, and device seats. Do not claim Agensi handles billing for it.

## IP and naming — blocking gate

1. Preserve git history and a source-component inventory. Compare every source asset and copied code fragment against Desktop Commander **local MIT** and **remote proprietary** sources. Do not copy remote proprietary source, docs, logo, branded UI, images, or screenshots without written authorization.
2. AI-assisted generation by itself does not prohibit distribution: OpenAI's terms assign its rights, if any, in generated Output to the user (as between the user and OpenAI). This does **not** clear third-party rights or guarantee originality. Review each file's provenance.
3. Rename customer-facing branding to **Dorado Relay** before release. Search EUIPO / OEPM trademark registers (and relevant countries) for the proposed name and possible confusion. Do not suggest affiliation with Desktop Commander or OpenAI.
4. Commission a patent freedom-to-operate search in intended markets if investment and sales justify it. A basic web search is *not* confirmation that no patents cover a particular technical implementation.
5. Verify all direct/transitive dependencies' licence obligations and carry notices where required. Repository's MIT declaration does not license third-party code that its owner did not authorize.
6. Spanish lawyer familiar with software/IP to review the final source inventory, product claims and terms before launch. Check employment/contract assignments if any code was developed under an employer's duties.

Primary references:
- https://github.com/desktop-commander/remote-desktop-commander/blob/main/LICENSE
- https://github.com/wonderwhy-er/DesktopCommanderMCP/blob/main/LICENSE
- https://openai.com/es-ES/policies/terms-of-use/
- https://www.boe.es/buscar/act.php?id=BOE-A-1996-8930
- https://www.epo.org/en/legal/guidelines-epc/2025/g_ii_3_6.html

## Proposed pricing (not active)

- Self-hosted: free, MIT; no licence seat restriction on source. Paid support optional later.
- Agensi skill: **$19 one-time** (70% of net sale price before any personal tax). Start with a free preview or sample only if it does not replace the product.
- Cloud pilot: **€6.90/month ex VAT includes 2 paired devices; +€1.90/month ex VAT for each additional paired device**, with a published fair-use policy before launch.
- Need to estimate time spent on onboarding/support, actual taxes, payments chargebacks, traffic, customer acquisition, abuse and backups before final public prices.
- Competitor Desktop Commander has a free remote plan and a $20/month Pro plan (as of Nov 2026): differentiation must be **developer-oriented workflows, clear customer-controlled privacy, reliable multi-machine sessions and transparent seat pricing**, not merely shell access.

Pricing references:
- https://desktopcommander.app/pricing/
- https://www.agensi.io/docs/pricing-your-skill
- https://www.agensi.io/terms

## Hosting path

### Phase 0: personal use

Keep the home relay and its secrets **private**. Never pair paying customers to this server. The server currently has one administrator password and one device namespace.

### Phase 1: isolated pilot per paying customer

A configuration generator and operator runbook are now available in [PILOT_DEPLOYMENT.md](PILOT_DEPLOYMENT.md). The generator only stages isolated configuration; it does not deploy to a VPS or integrate billing. The operator can now also revoke all credentials issued to an MCP OAuth client through protected admin endpoints. A first [single-owner customer portal](CUSTOMER_PORTAL.md) supports invite-only activation, login, device management, OAuth revocation, password changes and privacy-limited audit events. This is **per isolated relay**, not shared multi-tenancy or integrated billing.

On EU VPS, use Docker Compose / Caddy with one non-root relay container per paying customer, unique subdomain, private SQLite database volume, separate administrator password and agent credentials. Configure `DORADO_MAX_DEVICES=2` initially (per *relay*, not per user in a shared instance), `DORADO_COMMERCIAL_MODE=1`, and disable `DORADO_AGENT_TOKEN`. Use isolation/resource quotas, default-deny cross-container access and automatic backups.

**Do not** place two customers in one existing Dorado relay. Current database, OAuth access tokens, hub map and approval password have no tenant IDs. Changing the device number alone does not make a multiuser app.

Administrative `GET /admin/devices` and `POST /admin/devices/revoke` are per-relay pilot APIs, not a billing integration or customer dashboard. Both require `Authorization: Bearer <relay-admin-password>`, must be used over HTTPS, and must be protected against brute force at the gateway.

Example provider benchmark: Hetzner CX23 2 vCPU, 4GB RAM was listed at €5.49/month ex VAT for EU regions following the June 2026 change; price and availability may change, and public IPv4, backups and taxes may add cost:
https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/

**Capacity is unknown** until measured. Do not promise customer counts, latency SLA or unlimited requests based on an untested VPS. Load-test idle WebSocket sessions, large outputs, burst MCP calls, data transfer, reconnects, and failure recovery.

### Phase 2: managed SaaS

Add explicit user accounts, MFA/passkeys, tenant-aware auth and device ownership, self-service device management, tested per-tenant authorization, central control plane with Stripe Billing verified webhooks, and per-tenant entitlements. Never trust a plan name or price sent by browser; reconcile billing server-side. Gate new pairings and preserve device safety on payment failure; avoid destroying users' data immediately.

For horizontal scaling: PostgreSQL for identities/entitlements/tokens, a Redis or equivalent message bus to route requests to the node owning each live WebSocket, separate edge/API and relay processes, bounded queues/timeouts, backpressure and autoscaling. SQLite + in-memory `hub.devices` as currently implemented is **single-process** and cannot simply be replicated.

Do not process payment card numbers yourself; use hosted Stripe Checkout/Portal. Stripe Billing charges (2026): usage-based 0.7% of Billing volume in addition to applicable payment fees:
https://stripe.com/es/billing/pricing

## Security and compliance — launch blockers

- Full command execution is high-risk and not sandboxed. The current `DORADO_ALLOWED_ROOTS` only constrains some file-tool paths and command *starting directories*, NOT what arbitrary shell commands can access. Make safer per-device permissions/explicit root selection and read-only mode; independent OS account recommended.
- Add per-agent concurrency limits and per-tenant rate limits; command argument size caps are not enough. Audit process kill, runaway subprocesses, memory and filesystem abuse.
- Build device **revoke**, OAuth client/token revoke, account logout, expiring credentials and session management. Add 2FA/passkeys and login brute-force controls before shared service.
- Add structured security audit events (without command output/secrets by default), minimal data retention, encrypted backups, disaster recovery and an abuse-response workflow.
- Sign binaries and provide verified checksums. Distribute an install wizard and user-start service without embedding a residential IP, shared token or administrator password.
- Spanish/EU legal: privacy notice, terms, data processing agreement, provider/processor roles, GDPR basis and deletion, cookies where relevant, business invoicing and tax advice; EU B2C supply may require OSS depending on the actual model. Agensi says it is merchant of record for skill sales, not for independent hosting.
- Verify reverse-proxy TLS, security headers, network egress restrictions, log redaction, CVE scanning, dependency upgrade policy, penetration tests and backups restore tests.

GDPR / EU tax starting points:
- https://www.aepd.es/
- https://sede.agenciatributaria.gob.es/Sede/iva/iva-comercio-electronico/regimenes-especiales-ventanilla-unica.html

## What this branch actually implements

- Optional **per-relay device cap** (`DORADO_MAX_DEVICES`; default 0 = no cap; commercial pilot set to 2).
- Seat check at request and atomic credential exchange, so parallel claims cannot exceed the cap.
- Bearer-password-protected per-relay inventory and immediate device revocation, including active sockets.
- Commercial mode rejects the legacy shared device token.
- Branded, responsive local pairing and OAuth approval screens, without remote web assets.
- A draft Agensi skill for human review and local testing.
- Regression tests; **none of the items above activate billing, create customer accounts or safely provide shared multi-tenancy**.

## Launch checklist

- [ ] Resolve legal provenance and brand checks, get legal sign-off.
- [ ] Add safe agent access modes, per-device scope enforcement and secondary confirmation for dangerous operations.
- [ ] Fix authentication, rate limiting and account isolation for chosen sales model.
- [ ] Add secure dashboards, billing, subscription webhooks, device entitlements and self-service support.
- [ ] Package installers with clean user experience, code signing, release/update channel.
- [ ] Load test with representative customers and benchmark resource usage.
- [ ] Test account isolation, authorization bypass, incident recovery and backups.
- [ ] Publish legal policies, support contact, refund/cancellation rules and clear product limitations.
- [ ] Ask Agensi whether pairing its skill with a separately billed external hosted relay complies with its anti-upsell terms.
- [ ] Obtain explicit owner approval before deploying to production, uploading to Agensi, buying infrastructure or collecting payments.
