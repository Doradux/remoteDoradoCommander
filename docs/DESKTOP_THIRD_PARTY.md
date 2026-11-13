# Dorado Relay Desktop — terceros y licencias

Candidata privada de escritorio para Ubuntu y Windows. Las bibliotecas se distribuyen
junto a la aplicación en una carpeta `DoradoRelay/` (PyInstaller `--onedir`).

## Qt / PySide6 Essentials

- https://doc.qt.io/qtforpython-6/
- Fuente: https://code.qt.io/cgit/pyside/pyside-setup.git/
- PySide6 Essentials / Qt for Python: licencias **LGPLv3 / GPL** y licencia comercial de Qt (según sus archivos y componentes).
- La versión candidata utiliza las bibliotecas de Qt con enlace dinámico y las deja como DLL/`.so` dentro del directorio instalable, sin convertirlas en código propio ni hacer static linking.
- Los usuarios conservan los derechos que otorgue LGPL, incluida la sustitución de bibliotecas compartidas Qt bajo los términos aplicables.
- Antes de venta/redistribución final: incluir íntegros los avisos correspondientes del wheel Qt/PySide y los textos de las licencias para cada distribución; comprobar módulos Qt realmente incorporados, dependencias Linux de sistema y obligaciones de la LGPLv3.
- No afirmar que Qt/Qt Company patrocina Dorado Relay.

## Keyring

- https://github.com/jaraco/keyring
- Utiliza el Credential Manager de Windows o el servicio Secret Service del escritorio de Ubuntu.
- No persistimos contraseñas ni tokens en el JSON de preferencias de escritorio.
- La falta de un almacén de credenciales seguro bloquea el recuerdo automático: **no hay fallback en texto plano**.

## Otros componentes

- Python, httpx, websockets y las dependencias indirectas conservan sus propias licencias.
- El frontend **web** usa React + Animate UI bajo MIT + Commons Clause (avisos separados: `docs/THIRD_PARTY_NOTICES.md`).
- El escritorio **nativo** usa Qt (no incrusta la web React ni copia componentes Animate UI).

Antes de distribuir una versión pública, completar un inventario exacto de licencias/SBOM por sistema operativo, firmas de ejecutables y revisión de propiedad intelectual. Este aviso NO equivale a una revisión legal.
