import { strict as assert } from 'node:assert'
import React from 'react'
import { renderToString } from 'react-dom/server'
import { createServer } from 'vite'

const vite = await createServer({ server: { middlewareMode: true }, appType: 'custom', optimizeDeps: { noDiscovery: true, entries: [] }, logLevel: 'silent' })
try {
  const { App } = await vite.ssrLoadModule('/src/app.jsx')
  const cases = [
    [{ page: 'login', next: '/portal', error: true }, ['Bienvenido de nuevo', 'Iniciar sesión']],
    [{ page: 'activate', error: false }, ['Activa tu cuenta', 'Código de activación']],
    [{ page: 'security', email: 'owner@example.com', csrf: 'test-token' }, ['Seguridad de la cuenta', 'Contraseña actual']],
    [{ page: 'pair', code: 'ABCD-EFGH-JKLM', deviceName: 'desktop', loggedIn: true, csrf: 'csrf-token' }, ['Vincular ordenador', 'desktop']],
    [{ page: 'oauth', requestId: 'request-id', callbackOrigin: 'https://client.example', loggedIn: false }, ['Autorizar asistente', 'https://client.example']],
    [{ page: 'dashboard', email: 'owner@example.com', csrf: 'csrf-token', maxDevices: 2,
      devices: [{ name: 'desktop', online: true }, { name: 'laptop', online: false }],
      clients: [{ id: 'client-identifier', redirectUris: ['https://client.example'] }],
      activity: [{ when: '12/11/2026 12:00 UTC', tool: 'read_file', device: 'desktop', status: 'success' }],
    }, ['Tus dispositivos, siempre a mano', 'Dispositivos', 'En línea']],
    [{ page: 'success', title: 'Equipo autorizado', message: 'Vuelve a tu PC' }, ['Equipo autorizado']],
  ]
  for (const [props, expected] of cases) {
    const html = renderToString(React.createElement(App, { initial: props }))
    for (const fragment of expected) {
      assert.ok(html.includes(fragment), `Page ${props.page} missing ${fragment}`)
    }
    assert.ok(!html.includes('javascript:'), 'Unexpected unsafe link in SSR')
  }
  process.stdout.write(`Animate UI server-side smoke check: ${cases.length} pages rendered successfully\n`)
} finally {
  await vite.close()
}
