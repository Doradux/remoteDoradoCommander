# Dorado Relay — instrucciones para agentes de desarrollo

## Regla obligatoria de interfaz

**Toda interfaz web nueva o modificada de este repositorio DEBE usar exclusivamente componentes/primitivas de Animate UI para sus interacciones y animaciones.**

Referencias que deben consultarse antes de crear una pantalla:
- Instalación: https://animate-ui.com/docs/installation
- Catálogo oficial completo: https://animate-ui.com/docs/primitives
- Catálogo y usos específicos de este producto: [docs/ANIMATE_UI_CATALOG.md](docs/ANIMATE_UI_CATALOG.md)
- Accesibilidad y movimiento reducido: https://animate-ui.com/docs/accessibility

Implementación:
- React + `@animate-ui/react` + `motion` + Tailwind, en `frontend/`.
- La interfaz **no** se construye como HTML/CSS artesanal concatenado desde Python.
- No añadir Chakra, Mantine, Material UI, Ant Design ni otra librería UI/animaciones. No reinventar Dialog, Tabs, Button, menús, indicadores o transiciones que estén en Animate UI.
- Para elementos HTML básicos que Animate UI no incluye (campos de texto normales, formularios semánticos, layout), seguir el patrón de Tailwind/shadcn mostrado por la propia documentación; no convertirlos en una librería de componentes alternativa.
- Respetar `prefers-reduced-motion`, ARIA, navegación por teclado y confirmación en acciones destructivas.
- No usar CDN, scripts inline ni CSS inline en la shell de FastAPI. Los únicos scripts/estilos se sirven localmente bajo `/ui-assets/`.
- FastAPI conserva todas las decisiones de autenticación, OAuth, CSRF, límites de dispositivos y facturación.
- Conservar el aviso de licencia de Animate UI (MIT + Commons Clause) como parte del producto comercial; nunca revender los componentes por separado.

Antes de confirmar código:
1. `npm ci --prefix frontend`
2. `npm run test:ssr --prefix frontend` (no abre navegador)
3. `npm run build --prefix frontend`
4. `pytest -q` en Python 3.12, o las pruebas Docker de `tests/Dockerfile.test`.
5. Comprobar que `remote_dorado/static/` refleja el último build y que la CSP no habilita `unsafe-inline`.
6. Un cambio lógico por commit; trabajar en `feature/commercial-hardening` hasta que el usuario autorice fusionar.
7. No iniciar `npm run dev` automáticamente ni cambiar el relay personal de producción.

El enlace al catálogo debe revisarse al incorporar cada componente nuevo; Animate UI evoluciona y los nombres y props pueden cambiar.
