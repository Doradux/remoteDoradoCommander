# Dorado Relay — catálogo obligatorio Animate UI (v2.4)

**Fuente única para todas las interfaces de Dorado Relay:** https://animate-ui.com/docs/primitives

Install: https://animate-ui.com/docs/installation

Esta lista reúne **todas las familias y componentes del índice oficial consultado el 12/11/2026**, para elegir los adecuados antes de añadir cualquier pantalla o interacción. No significa que debamos añadirlos todos a la app: usarlos indiscriminadamente empeoraría la experiencia. Tras actualizaciones de Animate UI, revisar su índice y changelog.

## Componentes (Primitives)

Accordion · Alert Dialog · Carousel · Checkbox · Code Block · Collapsible · Combobox · Context Menu · Dialog · Drawer · Dropdown Menu · Hover Card · Input OTP · Menubar · Navigation Menu · Pagination · Popover · Preview Link Card · Progress · Questionnaire · Radio Group · Select · Sheet · Sidebar · Slider · Switch · Tabs · Toast · Toggle · Toggle Group · Tooltip

## Animate UI

Avatar Group · Bottom Navigation · Cursor · GitHub Stars · Motion Grid · Page Transition · Pinned List · Scroll Progress · Spring

## Botones

Button · Copy Button · Flip Button · GitHub Stars Button · Icon Button · Liquid Button · Ripple Button · Theme Toggler Button

## Textos

Gradient Text · Highlight Text · Morphing Text · Rolling Text · Rotating Text · Shimmering Text · Sliding Text · Splitting Text · Swapping Text · Typing Text

## Números

Counting Number · Scrolling Number · Sliding Number

## Efectos

Auto Height · Blur · Click · Effect · Fade · Highlight · Image Zoom · Magnetic · Particles · Scroll Fade · Shine · Slide · Theme Toggler · Tilt · Zoom

## Fondos

Bubble Background · Fireworks Background · Glyph Background · Gradient Background · Gravity Stars Background · Hexagon Background · Hole Background · Stars Background · Topography Background

## Iconos

Lucide Icons · Hugeicons

## Uso escogido para el producto

- **Acceso, alta por invitación, seguridad:** Animate UI Button; campos HTML semánticos con el enfoque Tailwind utilizado por la documentación de Animate UI. No hay Input de texto genérico en el catálogo de primitivas, solo Input OTP. Para versiones posteriores, los campos podrán usar el componente shadcn Input, como hace la documentación oficial.
- **Panel:** Animate UI Tabs para las secciones, Animate UI Counting Number para las métricas.
- **Revocar equipos o sesiones MCP:** Animate UI Dialog con confirmación explícita y formularios autenticados del backend.
- **Vinculación y OAuth:** Animate UI Button + formularios accesibles; no usar diálogos encima de la autorización sensible, pues esta debe poderse revisar con claridad.
- **Interacciones futuras:** Toast para feedback de acciones, Tooltip para texto secundario, Sheet/Sidebar para navegación en móvil, Theme Toggler Button si añadimos tema claro/oscuro, Input OTP para códigos de un solo uso, Progress para onboarding, Copy Button para un código no sensible, Page Transition solamente si introducimos routing cliente.

## Reglas de diseño y seguridad

1. Todos los **componentes interactivos animados** deben importarse de `@animate-ui/react` o instalarse desde su registro oficial `@animate-ui/...`; no instalar bibliotecas alternativas de componentes visuales.
2. La apariencia y composición usan **Tailwind CSS**, como en la documentación de Animate UI. HTML semántico es necesario para formularios y layout; el catálogo no incluye todos los elementos básicos del navegador.
3. Usar `AnimateUIProvider` y respetar automáticamente `prefers-reduced-motion`, teclado, foco y ARIA; no implementar animaciones personalizadas encima.
4. Los formularios cambian de aspecto, **no de contrato**: el backend sigue verificando cookies de sesión, CSRF, permisos OAuth y propiedad del dispositivo.
5. No cargar recursos de un CDN externo en las pantallas de aprobación. JavaScript y CSS deben ser locales, con CSP restrictiva.
6. Mantener las acciones destructivas detrás de un Dialog y confirmación separada.
7. No mezclar ni restaurar las plantillas HTML/CSS construidas a mano en Python. El backend solo entrega un documento mínimo con datos JSON escapados y archivos estáticos React/Vite.
8. La dependencia `@animate-ui/react` usa **MIT + Commons Clause**: está permitido usarla comercialmente como parte de un producto/web, pero **no revender los componentes como producto independiente**. Conservar el aviso de copyright y la licencia en `remote_dorado/static/licenses/animate-ui-LICENSE.md`.
9. Cuando aparezcan nuevos componentes, consultar `https://animate-ui.com/docs/primitives` y reevaluar esta matriz.

## Referencias exactas de componentes implementados

- Button: https://animate-ui.com/docs/primitives/animate-ui/button
- Dialog: https://animate-ui.com/docs/primitives/base-ui/dialog
- Tabs: https://animate-ui.com/docs/primitives/base-ui/tabs
- Counting Number: https://animate-ui.com/docs/primitives/animate-ui/counting-number
- Accesibilidad y movimiento reducido: https://animate-ui.com/docs/accessibility

**Alcance:** esta rama sustituye las páginas del portal, inicio de sesión, activación, autorizaciones MCP y vinculación de dispositivo. Todavía no hay panel central de facturación de clientes; cuando se cree, estará sujeto a las mismas reglas.
