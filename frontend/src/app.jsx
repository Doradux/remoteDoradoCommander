import React from 'react'
import { Button } from '@animate-ui/react/primitives/buttons/button'
import { Tabs } from '@animate-ui/react/primitives/base-ui/tabs'
import { Dialog } from '@animate-ui/react/primitives/base-ui/dialog'
import { CountingNumber } from '@animate-ui/react/primitives/texts/counting-number'

const button = 'inline-flex min-h-10 cursor-pointer items-center justify-center gap-2 rounded-lg bg-amber-300 px-5 py-2.5 text-sm font-semibold text-slate-950 outline-none focus-visible:ring-2 focus-visible:ring-amber-200 disabled:opacity-40'
const outline = 'inline-flex min-h-10 cursor-pointer items-center justify-center gap-2 rounded-lg border border-white/15 bg-white/5 px-4 py-2 text-sm font-medium text-slate-100 hover:bg-white/10 focus-visible:ring-2 focus-visible:ring-amber-200'
const danger = 'inline-flex min-h-9 cursor-pointer items-center justify-center rounded-lg border border-rose-400/25 bg-rose-400/10 px-3 py-2 text-xs font-semibold text-rose-200 hover:bg-rose-400/20 focus-visible:ring-2 focus-visible:ring-rose-300'
const input = 'mt-2 block w-full rounded-xl border border-white/15 bg-black/20 px-4 py-3 text-base text-slate-100 outline-none placeholder:text-slate-500 focus:border-amber-300 focus:ring-2 focus:ring-amber-300/20'
const panel = 'rounded-2xl border border-white/10 bg-[#111722] p-5 shadow-sm shadow-black/10 sm:p-7'
const label = 'block text-sm font-medium text-slate-300'
const subtle = 'text-sm leading-6 text-slate-400'

function Brand({ description = 'Tus equipos, bajo tu control.' }) {
  return <div className="flex items-center gap-3">
    <div className="flex size-10 items-center justify-center rounded-xl border border-amber-300/30 bg-amber-300/10 text-xl font-black text-amber-200" aria-hidden="true">D</div>
    <div><div className="text-[15px] font-semibold tracking-tight">Dorado Relay</div><div className="text-xs text-slate-500">{description}</div></div>
  </div>
}

function Frame({ children, email, csrf, narrow = false }) {
  return <div className="min-h-screen bg-[#0a0c11] text-slate-100">
    <div className="mx-auto max-w-6xl px-5 pb-16 pt-6 sm:px-8">
      <header className="flex flex-wrap items-center justify-between gap-5 border-b border-white/10 pb-5">
        <Brand />
        {email ? <nav className="flex flex-wrap items-center gap-4 text-sm text-slate-400" aria-label="Cuenta">
          <span className="max-w-[180px] truncate sm:max-w-none">{email}</span>
          <a className="text-slate-200 hover:text-amber-200" href="/portal/security">Seguridad</a>
          <form action="/portal/logout" method="post"><input type="hidden" name="csrf" value={csrf} />
            <Button className={outline} type="submit">Cerrar sesión</Button></form>
        </nav> : <span className="text-xs text-slate-500">Conexión cifrada · acceso privado</span>}
      </header>
      <main className={narrow ? 'mx-auto max-w-[460px] pt-12 sm:pt-20' : 'pt-11'}>{children}</main>
      <footer className="mt-16 border-t border-white/10 pt-6 text-xs leading-6 text-slate-500">
        Dorado Relay · Autoriza únicamente ordenadores y asistentes en los que confíes.
      </footer>
    </div>
  </div>
}

function Heading({ eyebrow, title, description }) {
  return <div className="mb-7">
    <div className="mb-3 text-xs font-semibold uppercase tracking-[.19em] text-amber-200">{eyebrow}</div>
    <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">{title}</h1>
    <p className="mt-3 text-sm leading-7 text-slate-400">{description}</p>
  </div>
}

function Field({ name, title, type = 'text', autoComplete, defaultValue, minLength, maxLength, required = true }) {
  return <label className={label}>{title}
    <input className={input} id={name} name={name} type={type} autoComplete={autoComplete}
      defaultValue={defaultValue} minLength={minLength} maxLength={maxLength} required={required} />
  </label>
}

function ErrorMessage({ children }) {
  return <div role="alert" className="mb-5 rounded-xl border border-rose-500/25 bg-rose-500/10 p-4 text-sm leading-6 text-rose-200">{children}</div>
}

function AuthPage({ mode, error, next, email, csrf }) {
  const signup = mode === 'activate'
  const secure = mode === 'security'
  const title = signup ? 'Activa tu cuenta' : secure ? 'Seguridad de la cuenta' : 'Bienvenido de nuevo'
  const intro = signup
    ? 'Introduce la invitación que recibiste y establece una contraseña. El código solo se puede usar una vez.'
    : secure ? 'Cambia tu contraseña y cierra todas las sesiones abiertas.'
    : 'Accede para consultar tus dispositivos y administrar tus conexiones MCP.'
  const action = signup ? '/portal/activate' : secure ? '/portal/security' : '/portal/login'
  return <Frame email={secure ? email : undefined} csrf={csrf} narrow>
    <Heading eyebrow={signup ? 'Activa tu espacio' : 'Acceso al panel'} title={title} description={intro} />
    <section className={panel}>
      {error && <ErrorMessage>No se pudo completar la operación. Revisa tus datos o inténtalo más tarde.</ErrorMessage>}
      <form className="grid gap-5" action={action} method="post">
        {mode === 'login' && <input type="hidden" name="next" value={next || '/portal'} />}
        {secure && <input type="hidden" name="csrf" value={csrf || ''} />}
        {signup && <>
          <Field name="email" title="Correo de la invitación" type="email" autoComplete="email" maxLength={254} />
          <Field name="code" title="Código de activación" autoComplete="one-time-code" maxLength={120} />
        </>}
        {mode === 'login' && <Field name="email" title="Correo electrónico" type="email" autoComplete="username" maxLength={254} />}
        {secure && <Field name="current" title="Contraseña actual" type="password" autoComplete="current-password" />}
        <Field name={secure ? 'new' : 'password'} title={secure ? 'Contraseña nueva' : signup ? 'Crea una contraseña' : 'Contraseña'}
          type="password" autoComplete={secure || signup ? 'new-password' : 'current-password'}
          minLength={secure || signup ? 12 : undefined} />
        {(signup || secure) && <Field name="repeat" title="Repite la contraseña" type="password" autoComplete="new-password" minLength={12} />}
        <Button type="submit" className={button}>{signup ? 'Activar cuenta' : secure ? 'Guardar contraseña' : 'Iniciar sesión'}</Button>
      </form>
    </section>
    <p className="mt-5 text-center text-xs leading-6 text-slate-500">
      {signup ? 'Tu contraseña no se comparte con el operador del servicio.' :
        secure ? <a className="text-amber-200" href="/portal">Volver a mis dispositivos</a> :
          'Acceso exclusivo para propietarios con una instancia autorizada.'}
    </p>
  </Frame>
}

function ApprovalPage({ kind, code = '', deviceName, requestId, callbackOrigin, csrf, loggedIn }) {
  const pairing = kind === 'pair'
  return <Frame narrow>
    <Heading eyebrow={pairing ? 'Nuevo dispositivo' : 'Permiso de acceso MCP'}
      title={pairing ? 'Vincular ordenador' : 'Autorizar asistente'}
      description={pairing
        ? 'Comprueba que el código de esta página coincide con el que muestra la aplicación del ordenador.'
        : 'Comprueba el origen de la solicitud antes de permitirle controlar tus equipos.'} />
    <section className={panel}>
      <div className="mb-6 rounded-xl border border-amber-300/20 bg-amber-300/5 p-4">
        <div className="mb-1 text-xs font-medium text-slate-400">{pairing ? 'Equipo solicitante' : 'Cliente que solicita acceso'}</div>
        <div className="break-all text-sm font-semibold text-slate-100">{pairing ? (deviceName || 'Pendiente de identificar') : callbackOrigin}</div>
      </div>
      <form className="grid gap-5" action={pairing ? '/pair/approve' : '/oauth/approve'} method="post">
        {pairing ? <Field name="user_code" title="Código de vinculación" defaultValue={code} maxLength={20} autoComplete="off" /> :
          <input type="hidden" name="request_id" value={requestId || ''} />}
        {loggedIn ? <input type="hidden" name="csrf" value={csrf || ''} /> :
          <Field name="password" title="Contraseña de administración del relay" type="password" autoComplete="current-password" />}
        <div className="rounded-xl border border-amber-400/20 bg-amber-300/5 px-4 py-3 text-xs leading-6 text-amber-100">
          Esta autorización puede permitir ejecutar comandos y modificar archivos en los dispositivos accesibles. No apruebes solicitudes desconocidas.
        </div>
        <Button className={button} type="submit">{pairing ? 'Autorizar ordenador' : 'Conceder acceso MCP'}</Button>
      </form>
    </section>
    {loggedIn && <p className="mt-5 text-center text-sm"><a className="text-amber-200" href="/portal">Volver al panel</a></p>}
  </Frame>
}

function ConfirmAction({ title, description, action, field, value, csrf, text }) {
  return <Dialog.Root>
    <Dialog.Trigger className={danger}>{text}</Dialog.Trigger>
    <Dialog.Portal>
      <Dialog.Backdrop className="fixed inset-0 z-40 bg-black/75 backdrop-blur-sm" />
      <Dialog.Popup className="fixed left-1/2 top-1/2 z-50 w-[calc(100%-2rem)] max-w-md -translate-x-1/2 -translate-y-1/2 rounded-2xl border border-white/15 bg-[#171d28] p-6 shadow-2xl outline-none">
        <Dialog.Title className="text-xl font-semibold tracking-tight">{title}</Dialog.Title>
        <Dialog.Description className="mt-3 text-sm leading-6 text-slate-400">{description}</Dialog.Description>
        <div className="mt-6 flex flex-wrap justify-end gap-3">
          <Dialog.Close className={outline}>Cancelar</Dialog.Close>
          <form action={action} method="post">
            <input type="hidden" name="csrf" value={csrf} />
            <input type="hidden" name={field} value={value} />
            <Button className={danger} type="submit">Confirmar revocación</Button>
          </form>
        </div>
      </Dialog.Popup>
    </Dialog.Portal>
  </Dialog.Root>
}

function Stat({ count, title, description }) {
  return <div className={panel}>
    <CountingNumber number={count} className="text-4xl font-semibold tabular-nums tracking-tight text-slate-100" inView />
    <div className="mt-2 text-sm font-medium text-slate-200">{title}</div>
    <p className="mt-2 text-xs text-slate-500">{description}</p>
  </div>
}

function Empty({ text }) {
  return <div className="rounded-xl border border-dashed border-white/10 bg-white/[.015] px-5 py-10 text-center text-sm text-slate-400">{text}</div>
}

function Dashboard({ data }) {
  const { devices = [], clients = [], activity = [], email = '', csrf = '', maxDevices, billingUnavailable = false, billingSigned = false } = data
  const online = devices.filter(x => x.online).length
  return <Frame email={email} csrf={csrf}>
    <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
      <Heading eyebrow="Panel de control" title="Tus dispositivos, siempre a mano"
        description="Gestiona tus ordenadores y los permisos de tus asistentes desde un único espacio." />
      <div className="mb-7 rounded-full border border-white/10 bg-white/5 px-4 py-2 text-xs text-slate-300">
        {billingUnavailable ? 'Verificación de suscripción pendiente' : billingSigned ? 'Cuota verificada' : 'Relay privado'}
      </div>
    </div>
    {billingUnavailable && <ErrorMessage>La cuota no se puede verificar ahora mismo. Tus equipos existentes siguen accesibles, pero no podrás añadir otros nuevos.</ErrorMessage>}
    <div className="mb-9 grid gap-4 sm:grid-cols-3">
      <Stat count={online} title="En línea" description="Ordenadores disponibles ahora" />
      <Stat count={devices.length} title="Vinculados" description="Equipos dados de alta" />
      <Stat count={maxDevices == null ? devices.length : maxDevices} title={maxDevices == null ? 'Equipos' : 'Límite de equipos'} description={billingUnavailable ? 'La cuota se encuentra pendiente' : 'Plazas disponibles en tu plan'} />
    </div>
    <Tabs.Root defaultValue="devices" className="block">
      <Tabs.List className="mb-5 inline-flex w-full max-w-lg gap-1 rounded-xl border border-white/10 bg-white/5 p-1 sm:w-auto" aria-label="Secciones del panel">
        {[['devices', 'Dispositivos'], ['assistants', 'Asistentes MCP'], ['activity', 'Actividad']].map(([value, text]) =>
          <Tabs.Tab key={value} value={value} className="cursor-pointer rounded-lg px-4 py-2 text-sm text-slate-300 outline-none data-[active]:bg-amber-300 data-[active]:font-semibold data-[active]:text-slate-950 focus-visible:ring-2 focus-visible:ring-amber-200">{text}</Tabs.Tab>)}
      </Tabs.List>
      <Tabs.Panel value="devices" className={panel}>
        <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-lg font-semibold">Ordenadores vinculados</h2>
          <span className={subtle}>La vinculación se inicia desde la aplicación instalada.</span>
        </div>
        {devices.length === 0 ? <Empty text="Todavía no has vinculado ningún ordenador." /> :
          <ul className="divide-y divide-white/10">{devices.map(device =>
            <li key={device.name} className="flex flex-wrap items-center justify-between gap-4 py-4">
              <div className="flex items-center gap-3">
                <span className={'size-2.5 rounded-full ' + (device.online ? 'bg-emerald-400' : 'bg-slate-500')} aria-hidden />
                <div><p className="text-sm font-medium">{device.name}</p><p className="text-xs text-slate-500">{device.online ? 'Conectado' : 'Desconectado'}</p></div>
              </div>
              <ConfirmAction title={'Desvincular ' + device.name} description="Este equipo dejará de aceptar nuevas conexiones. Deberás vincularlo otra vez desde su aplicación si quieres recuperar el acceso." action="/portal/devices/revoke" field="device_name" value={device.name} csrf={csrf} text="Desvincular" />
            </li>)}</ul>}
      </Tabs.Panel>
      <Tabs.Panel value="assistants" className={panel}>
        <div className="mb-5"><h2 className="text-lg font-semibold">Asistentes autorizados</h2>
          <p className={'mt-2 ' + subtle}>Revoca el acceso de cualquier cliente MCP que ya no utilices.</p></div>
        {clients.length === 0 ? <Empty text="No hay clientes MCP registrados." /> :
          <ul className="divide-y divide-white/10">{clients.map(client =>
            <li key={client.id} className="flex flex-wrap items-center justify-between gap-4 py-4">
              <div className="min-w-0"><div className="text-xs font-semibold text-slate-200">Cliente MCP</div>
                <div className="max-w-[500px] break-all font-mono text-xs text-slate-500">{client.id}</div>
                <div className="mt-1 break-all text-xs text-slate-500">{(client.redirectUris || []).join(', ')}</div></div>
              <ConfirmAction title="Revocar acceso del asistente" description="Todos los tokens de acceso de este cliente dejarán de funcionar. Podrás volver a conectarlo mediante autorización OAuth." action="/portal/oauth/revoke" field="client_id" value={client.id} csrf={csrf} text="Revocar acceso" />
            </li>)}</ul>}
      </Tabs.Panel>
      <Tabs.Panel value="activity" className={panel}>
        <div className="mb-5"><h2 className="text-lg font-semibold">Actividad reciente</h2>
          <p className={'mt-2 ' + subtle}>Solo metadatos de las operaciones, nunca comandos completos ni resultados. Purga durante el uso.</p></div>
        {activity.length === 0 ? <Empty text="Aún no hay operaciones registradas." /> :
          <div className="overflow-x-auto"><table className="w-full min-w-[560px] text-left text-sm">
            <thead className="border-b border-white/10 text-xs text-slate-500"><tr><th className="py-3">Fecha (UTC)</th><th>Acción</th><th>Equipo</th><th>Resultado</th></tr></thead>
            <tbody>{activity.map((row, i) => <tr key={i} className="border-b border-white/10 last:border-b-0">
              <td className="py-3 pr-3 text-xs text-slate-400">{row.when}</td><td className="pr-3">{row.tool}</td><td className="pr-3">{row.device}</td>
              <td className={row.status === 'success' ? 'text-emerald-300' : 'text-rose-300'}>{row.status === 'success' ? 'Correcta' : 'Error'}</td>
            </tr>)}</tbody></table></div>}
      </Tabs.Panel>
    </Tabs.Root>
    <aside className="mt-6 rounded-2xl border border-amber-400/20 bg-amber-300/5 p-5 text-sm leading-7 text-slate-300">
      <span className="font-semibold text-amber-200">Añadir un ordenador</span>
      <p className="mt-1 text-slate-400">Instala Dorado Relay en el equipo, inicia la vinculación y confirma su código desde esta cuenta. No necesitas compartir tus contraseñas ni tokens.</p>
    </aside>
  </Frame>
}

export function App({ initial }) {
  const page = initial?.page || 'login'
  if (page === 'dashboard') return <Dashboard data={initial} />
  if (page === 'pair' || page === 'oauth') return <ApprovalPage kind={page} {...initial} />
  if (['login', 'activate', 'security'].includes(page)) return <AuthPage mode={page} error={initial.error} next={initial.next} email={initial.email} csrf={initial.csrf} />
  if (page === 'success') return <Frame narrow><Heading eyebrow="Operación completada" title={initial.title || 'Listo'} description={initial.message || 'Puedes cerrar esta pestaña.'} /><a className="text-sm text-amber-200" href="/portal">Volver al panel</a></Frame>
  return <Frame narrow><Heading eyebrow="Dorado Relay" title="Página no disponible" description="Vuelve al panel principal." /></Frame>
}
