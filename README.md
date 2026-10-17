# Sistema de la clínica

Flask + SQLite + Tailwind. Pacientes y expedientes, agenda, inventario, ventas, **caja por turnos, fondo,
cuentas por cobrar y por pagar, gastos, reportes financieros** y **facturación electrónica (FEL) con Digifact**.

## Archivos

| Archivo | Para qué sirve |
|---|---|
| `app.py` | Núcleo: usuarios, pacientes, expedientes, inventario, citas, ventas |
| `rutas_dinero.py` | Caja, fondo, cuentas por cobrar/pagar, gastos, proveedores y reportes |
| `rutas_facturacion.py` | Configuración fiscal, emitir/anular facturas, consulta de NIT |
| `finanzas.py` | Reglas del dinero (saldos, movimientos, conciliación, estado de resultados) |
| `digifact.py` | Cliente de la API de Digifact y armado de la factura (FACT) |
| `correo.py` | Envío por SMTP y códigos de verificación en 2 pasos |
| `rutas_ia.py` | Reportes con IA (OpenAI) y pantalla de Configuración (IA y correo) |
| `database.py` | Esquema y **migraciones** (nunca borra datos) |
| `comun.py` | Utilidades compartidas (hora local, dinero, permisos, auditoría) |
| `tests/` | Pruebas automáticas |

## Puesta en marcha

```bash
pip install -r requirements.txt
python app.py                      # desarrollo: http://127.0.0.1:5000
python tests/ejecutar.py           # cada archivo de pruebas en su propio proceso
```

Al arrancar, la app **crea o migra la base sola**. Si encuentra una base anterior, antes de tocarla guarda una
copia completa en `backups/clinica_antes_de_migrar_v0_<fecha>.db`. Se puede ejecutar cuantas veces se quiera.

### En el servidor (gunicorn)

1. Detén el servicio y haz una copia de `clinica.db` por si acaso.
2. `pip install -r requirements.txt` dentro del `venv` (antes no se usaban Flask-WTF, requests ni cryptography).
3. Variables de entorno del servicio:
   - `FLASK_ENV=production` → las cookies de sesión pasan a `Secure` (requiere HTTPS, que ya da Cloudflare).
   - Opcional: `SECRET_KEY` y `FISCAL_ENCRYPTION_KEY`. Si no se definen, se generan una vez y quedan en `instance/`
     (`.secret_key` y `.fiscal_key`). **Respalda `instance/`**: sin `.fiscal_key` hay que volver a escribir la
     contraseña de Digifact.
4. Inicia: `gunicorn app:app`. El primer arranque migra la base.
5. **Cambia la contraseña de `admin`** (Usuarios → "Cambiar mi contraseña"): seguía siendo la que venía escrita en el
   código. Al entrar con ella el sistema te lo recuerda.

Respaldo manual o programado: `python database.py --respaldo` (guarda en `backups/`).

## Qué pasó con los datos actuales

Todo se conserva y se reorganiza:

| Antes | Ahora |
|---|---|
| Ventas a crédito + abonos | **Cuentas por cobrar** (mismos saldos); los abonos siguen ligados a su cuenta |
| "Ctas. por pagar" (`gastos`, `abonos_gastos`) | **Cuentas por pagar** con **proveedores** reales (se crearon a partir de los nombres escritos) |
| Cierres de caja | **Turnos de caja** (historial intacto); el efectivo del turno abierto pasó a movimientos de caja |
| — | Nuevo: Fondo, Gastos con categorías, Facturación, Auditoría, NIT en pacientes |

La conciliación del dinero solo revisa lo registrado **desde el momento de la migración** (`configuracion.tesoreria_desde`);
lo anterior se considera historia. Las ventas antiguas se pueden cancelar sin mover dinero.

## Cómo se usa el dinero

1. **Primera vez:** Fondo → *Entra dinero → Saldo inicial* con lo que la clínica tiene fuera de la caja (banco y bóveda).
2. **Cada día:** Caja → abrir turno (el efectivo inicial sale del Fondo) → vender → cobrar abonos → cerrar contando el efectivo
   (si hay faltante o sobrante se explica en notas; el efectivo contado pasa al Fondo).
3. **Cada venta deja su movimiento:** efectivo → caja · tarjeta → *por consolidar* · transferencia/depósito/cheque → fondo ·
   crédito → cuenta por cobrar. Cuando el banco deposita las tarjetas: Fondo → *Por consolidar → Consolidar*.
4. **Pagos y gastos** salen de caja, fondo o por consolidar y exigen saldo suficiente. Nada se edita ni se borra: un error
   se corrige anulando con un motivo.

Permisos: el **administrador** ve todo; el **usuario** vende, cobra, abre/cierra caja y registra gastos, pero no entra a Fondo,
Por pagar, reportes financieros, configuración, usuarios ni auditoría, y no puede cancelar ventas.

## Facturación electrónica (FEL)

Administración → *Config. de facturación*: NIT, razón social, usuario y contraseña de Digifact, establecimiento y dirección fiscal.
1. Empieza en ambiente **prueba** y usa *Probar conexión*.
2. Emite una factura de prueba desde el detalle de una venta (*Emitir factura*). Sin NIT se factura a Consumidor Final (CF).
3. Cuando salga bien, cambia a **producción**.

Se puede configurar como **régimen general (GEN)**: factura FACT con IVA incluido en el precio; o como **pequeño contribuyente (PEQ)**: factura FPEQ, con IVA declarado en cero y la frase tipo 3 que exige Digifact (el total es el precio de venta). Además: el descuento se reparte por línea; los
servicios van como `Servicio` y los productos como `Bien`. La factura queda con la fecha y hora del momento de emitirla. Si la
red falla a mitad de camino, el documento queda *indeterminado* y no se reintenta solo (para no duplicarlo): verifícalo con
soporte de Digifact. No incluye notas de crédito.

> La integración está probada con respuestas simuladas; **falta una prueba real contra el ambiente de prueba de Digifact con
> tus credenciales** antes de usarla en producción.

## Verificación en 2 pasos por correo

- Al **crear un usuario** el correo es obligatorio. Al iniciar sesión, tras la contraseña se envía un código de 6 dígitos a ese
  correo (vale 10 minutos, se usa una vez, máximo 5 intentos, reenvío cada 60 s). Solo se guarda su huella, no el código.
- Los usuarios que ya existían no tienen correo: entran con su contraseña y el sistema les pide registrarlo y confirmarlo
  con un código (también en Usuarios → "Registrar o cambiar mi correo").
- **Se activa sola** cuando Administración → Configuración → Correo tiene un envío de prueba exitoso. Mientras el correo
  esté sin configurar o sin probar, el inicio de sesión sigue siendo solo con contraseña (así un SMTP mal puesto no deja a
  nadie fuera). Si ya está activo y el servidor de correo falla, **no se entra** hasta que vuelva a funcionar.
- Gmail: cuenta con verificación en 2 pasos de Google y una "contraseña de aplicación" (16 letras). Se escribe en
  Configuración; se guarda **cifrada** (clave en `instance/.fiscal_key`: respáldala junto a `clinica.db`).

## Reportes con IA

Administración → Configuración: token de OpenAI y modelo (lista o escribir otro). Reportes con IA: elegir el periodo, generar
y, si se quiere, enviar por correo (hasta 5 destinatarios). Cada reporte queda guardado en el historial.

- La IA recibe solo cifras: ventas, utilidad, gastos, flujo, cuentas por cobrar/pagar, inventario, por día/método/vendedor y los
  15 artículos principales. **Nunca nombres de pacientes ni expedientes.**
- Compara con el periodo anterior de la misma duración y responde con resumen, análisis, alertas y recomendaciones priorizadas.
- Una consulta a OpenAI puede tardar hasta ~1 minuto: en gunicorn usa `--timeout 150` (`gunicorn app:app --timeout 150`).
- Solo administradores. El token nunca se muestra ni se escribe en la auditoría.
