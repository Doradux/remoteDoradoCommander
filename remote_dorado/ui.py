"""Dependency-free, escaped HTML for the self-hosted relay's approval screens.

No external JavaScript, fonts or assets are needed for secure authorization.
"""
from html import escape

_STYLE = """
:root{color-scheme:dark;font-family:Inter,ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
--ink:#e9edf5;--muted:#98a3b5;--edge:#263245;--gold:#f3c46a;--bg:#0a101b}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(ellipse at top,#18283e 0,#0a101b 56%);
color:var(--ink);display:grid;place-items:center;padding:24px}
main{width:min(100%,472px);background:#111c2beF;border:1px solid var(--edge);border-radius:22px;
box-shadow:0 32px 90px #0007;overflow:hidden}
header{padding:28px 32px 18px;display:flex;align-items:center;gap:13px;border-bottom:1px solid #ffffff0b}
.mark{width:40px;height:40px;display:grid;place-items:center;border-radius:12px;background:#f3c46a1c;
color:var(--gold);font-weight:900;font-size:21px;border:1px solid #f3c46a44}
.brand{font-size:17px;font-weight:750;letter-spacing:-.3px}.sub{color:var(--muted);font-size:12px;margin-top:3px}
section{padding:26px 32px 32px}h1{font-size:24px;line-height:1.2;margin:0 0 12px;letter-spacing:-.6px}
p{font-size:14px;line-height:1.6;color:var(--muted);margin:0 0 22px}
label{display:block;font-size:13px;font-weight:650;margin:19px 0 8px}
input{width:100%;height:47px;border:1px solid #39475b;border-radius:11px;padding:0 13px;
background:#0a1322;color:var(--ink);font:inherit;outline:none}
input:focus{border-color:#f3c46a;box-shadow:0 0 0 3px #f3c46a22}
button{width:100%;height:48px;margin-top:25px;border:0;border-radius:11px;color:#101827;
background:var(--gold);font-size:14px;font-weight:800;cursor:pointer}
button:hover{background:#ffcf77}
.warning{border:1px solid #e9b36536;background:#e9b36512;border-radius:11px;padding:12px 14px;
color:#f5d9a2;font-size:13px;line-height:1.6;margin:18px 0 0}
.kv{padding:12px 14px;border:1px solid var(--edge);border-radius:11px;margin-bottom:17px}
.kv small{display:block;color:var(--muted);font-size:11px;margin-bottom:5px}
.kv strong{font-size:14px;overflow-wrap:anywhere}
footer{border-top:1px solid #ffffff10;padding:18px 32px;color:#7f8ca3;font-size:12px;line-height:1.5}
@media(max-width:480px){section{padding:24px}header,footer{padding-left:24px;padding-right:24px}}
"""


def _frame(title: str, main: str) -> str:
    return (
        "<!doctype html><html lang='es'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{escape(title)} | Dorado Relay</title>"
        f"<style>{_STYLE}</style></head><body><main>"
        "<header><div class='mark' aria-hidden='true'>D</div>"
        "<div><div class='brand'>Dorado Relay</div>"
        "<div class='sub'>Acceso seguro a tus equipos</div></div></header>"
        f"<section>{main}</section>"
        "<footer>Solo conecta ordenadores y asistentes de IA en los que confíes. "
        "La autorización permite operaciones con tus permisos locales.</footer>"
        "</main></body></html>"
    )


def pairing_page(code: str, device_name: str | None = None) -> str:
    device = (
        f"<div class='kv'><small>Ordenador solicitante</small>"
        f"<strong>{escape(device_name)}</strong></div>"
        if device_name else ""
    )
    main = (
        "<h1>Vincular ordenador</h1>"
        "<p>Comprueba que el código aparece en el equipo que quieres conectar.</p>"
        f"{device}"
        "<form action='/pair/approve' method='post'>"
        "<label for='user_code'>Código de vinculación</label>"
        f"<input id='user_code' name='user_code' required maxlength='20' "
        f"autocomplete='off' spellcheck='false' value='{escape(code, quote=True)}'>"
        "<label for='password'>Contraseña de tu servidor</label>"
        "<input id='password' type='password' name='password' required "
        "autocomplete='current-password'>"
        "<div class='warning'>Al aprobarlo, un asistente autorizado podrá leer archivos "
        "y ejecutar comandos con los permisos de tu usuario del sistema.</div>"
        "<button type='submit'>Autorizar ordenador</button></form>"
    )
    return _frame("Vincular ordenador", main)


def authorization_page(request_id: str, callback_origin: str) -> str:
    main = (
        "<h1>Autorizar conexión MCP</h1>"
        "<p>Un asistente de IA solicita acceso remoto a tus ordenadores.</p>"
        f"<div class='kv'><small>Cliente que recibirá el acceso</small>"
        f"<strong>{escape(callback_origin)}</strong></div>"
        "<form action='/oauth/approve' method='post'>"
        f"<input type='hidden' name='request_id' value='{escape(request_id, quote=True)}'>"
        "<label for='password'>Contraseña del servidor</label>"
        "<input id='password' name='password' type='password' required "
        "autocomplete='current-password'>"
        "<div class='warning'>Este cliente podrá ejecutar comandos y modificar archivos "
        "en todos los equipos vinculados a este servidor. Autoriza solo si lo reconoces.</div>"
        "<button type='submit'>Conceder acceso</button></form>"
    )
    return _frame("Autorizar conexión", main)
