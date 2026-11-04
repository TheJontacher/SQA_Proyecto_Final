# Reporte de corrección de la infraestructura de pruebas

## Alcance

Se corrigió la infraestructura y compatibilidad de las tres suites existentes y, en el seguimiento actual, se implementó el cierre de sesión POST-only solicitado. No se eliminaron pruebas ni se modificaron reglas clínicas o financieras. No se configuraron GitHub Actions, SonarCloud o Docker; tampoco se instalaron dependencias ni se ejecutaron migraciones contra la base de datos original.

La aplicación inicializa SQLite durante la importación de `app.py`. Por eso, las variables de entorno y las rutas de prueba deben configurarse antes de importar Flask. El runner ahora hace ese trabajo por suite y la ejecuta en un proceso independiente.

## Archivos modificados

| Archivo | Cambios |
|---|---|
| `tests/ejecutar.py` | Crea una raíz temporal independiente por suite, configura variables antes de iniciar el proceso, verifica que las rutas de DB y respaldos estén dentro del temporal y vacías, ejecuta cada módulo en un proceso separado y muestra el resultado por suite. Acepta nombres de módulos para ejecutar una suite individual. |
| `tests/test_2fa_y_ia.py` | Usa `CLINICA_TEST_ROOT` cuando está definido por el runner; configura la base, los respaldos y los secretos de prueba antes de importar `app`; elimina `DATABASE_URL` heredado y valida las rutas y constantes de conexión. |
| `tests/test_dinero_y_facturacion.py` | Usa la raíz temporal del runner, configura el entorno antes de importar la aplicación, comprueba que la base, respaldos y uploads queden dentro del temporal y valida que `database.DATABASE_NAME`, `comun.DATABASE` y `app.DATABASE` coincidan. |
| `tests/test_regressions.py` | Reemplaza la referencia obsoleta a `app.DATABASE_URL`, usa `CLINICA_ADMIN_PASSWORD`, establece las rutas antes de importar Flask y sincroniza/restaura las constantes de base entre pruebas. Actualiza datos de prueba y solicitudes que no correspondían al esquema o rutas actuales. |

En la corrección inicial de infraestructura no se modificaron `app.py`, `database.py`, `comun.py` ni los módulos funcionales. El seguimiento posterior de logout modificó únicamente `app.py` según se detalla a continuación.

### Seguimiento: logout POST-only

En una corrección posterior se implementó el cierre de sesión exclusivamente por POST:

| Archivo | Cambio |
|---|---|
| `app.py` | `/logout` acepta únicamente POST. Se conserva `@login_required`, `logout_user()` y la redirección al login. |
| `templates/layout.html` | El enlace GET de logout se reemplazó por un formulario POST con token `csrf_token()`. |
| `tests/test_regressions.py` | La prueba ahora verifica GET anónimo = 405, POST sin token rechazado sin cerrar la sesión y POST con token válido que la cierra. La prueba también alinea sus aserciones de cabeceras con los valores que realmente establece `app.py`: `SAMEORIGIN` y `Referrer-Policy: same-origin`. |

## Problemas encontrados y correcciones

### Inicialización y rutas compartidas

- La importación de `app.py` puede inicializar la base inmediatamente. Configurar `CLINICA_DB` después del import dejaba abierta la posibilidad de inicializar o consultar una ruta equivocada.
- La conexión de ejecución usa `comun.DATABASE`, mientras que el inicializador usa `database.DATABASE_NAME` y `app` conserva la constante importada `DATABASE`. Las suites fijan el entorno antes de importar y verifican que las tres constantes apunten al mismo SQLite temporal.
- `DATABASE_URL` no es una configuración activa para estas suites y se elimina del entorno heredado antes de importar la aplicación.
- El runner crea un subdirectorio temporal para cada proceso, establece `CLINICA_DB`, `CLINICA_BACKUP_DIR` y `CLINICA_TEST_ROOT`, y comprueba la contención de las rutas antes de ejecutar. También rechaza rutas iniciales preexistentes dentro de la raíz temporal.

### Configuración de administración y credenciales

- Se eliminó el uso obsoleto de `INITIAL_ADMIN_PASSWORD` en favor de `CLINICA_ADMIN_PASSWORD`, que es la variable reconocida por el sistema.
- Las suites utilizan valores ficticios y temporales; no se modificaron credenciales de producción.

### Compatibilidad de las regresiones

- La prueba de abonos ahora usa la ruta vigente de cuentas por cobrar (`/cuentas-por-cobrar/<id>/pagar`) y los campos que consume esa ruta. Ya no depende de la antigua ruta de abono por venta.
- La cancelación de venta envía el motivo obligatorio y el origen del reembolso, respetando las validaciones actuales.
- La prueba del documento acepta `inline` para el PDF: el código vigente permite visualizar los tipos autorizados en línea. La prueba sigue verificando que la descarga esté protegida por sesión.
- La prueba de papelera ahora crea una línea real en `ventas_detalle` asociada al producto. Antes solamente insertaba un movimiento de inventario, pero esperaba el mensaje reservado por la aplicación para ítems referenciados en ventas. La aserción de bloqueo y conservación se mantiene.
- Se conservaron las aserciones sobre CSRF y logout. En esta primera etapa de infraestructura quedó pendiente hacer que `/logout` fuera POST-only; el seguimiento posterior descrito en “Cierre de sesión POST-only” corrigió ese comportamiento y verificó la prueba.

## Aislamiento y efectos secundarios

Las ejecuciones finales mostraron rutas de esta forma:

```text
CLINICA_DB=C:\Users\...\AppData\Local\Temp\clinica_suite_<id>\preflight.sqlite
CLINICA_BACKUP_DIR=C:\Users\...\AppData\Local\Temp\clinica_suite_<id>\backups
```

Cada suite se lanzó en un proceso nuevo. Las suites verifican que las rutas efectivas de SQLite, respaldos y archivos de carga estén contenidas en la raíz temporal. La base migrada y sus respaldos se generan allí y el directorio se elimina al salir del contexto temporal.

Los tests sustituyen los clientes externos que utilizan (correo, IA y facturación electrónica); las pruebas de PostgreSQL usan dobles en vez de conectarse a un servidor real. No se instaló un bloqueo global de sockets: si se agrega en el futuro una integración externa nueva, deberá verificarse y simularse explícitamente antes de incluirla en las pruebas.

No se ejecutó `database.py --reset`. No se usó `clinica.db`, ni se apuntó a respaldos o archivos de pacientes existentes. No se capturó un hash previo/posterior de esos archivos; la evidencia de aislamiento disponible es la configuración temporal impresa por el runner y las guardas de rutas ejecutadas por las suites.

## Comandos ejecutados

Se usó el entorno virtual ya existente, sin instalar paquetes:

```powershell
Set-Location 'C:\SQA_Proyecto_Final\clinica_web_20260926_125213\clinica_web'
& 'C:\SQA_Proyecto_Final\.venv\Scripts\python.exe' 'tests\ejecutar.py' 'tests.test_regressions'
& 'C:\SQA_Proyecto_Final\.venv\Scripts\python.exe' 'tests\ejecutar.py' 'tests.test_dinero_y_facturacion'
& 'C:\SQA_Proyecto_Final\.venv\Scripts\python.exe' 'tests\ejecutar.py'
```

Durante el trabajo también se ejecutó la suite `tests.test_2fa_y_ia` de forma individual. El runner completo volvió a ejecutar las tres suites en procesos temporales independientes.

## Resultados

### Línea base disponible

El diagnóstico anterior proporcionado para el proyecto reportó 91 pruebas detectadas, 17 aprobadas, 61 fallidas, 29 errores de preparación y 70 subpruebas aprobadas. Esos datos son la línea base reportada, no una repetición independiente en esta fase.

Durante la corrección, antes de los últimos ajustes:

- Una ejecución temprana de regresiones tuvo 8 fallos y 5 errores.
- Tras corregir las fixtures y referencias obsoletas, regresiones quedó en 18 pruebas, 13 aprobadas y 5 fallidas, sin errores.
- Una ejecución completa inicial tuvo una falla transitoria en conciliación; al repetir la suite financiera individual y el runner completo, dicha falla no se reprodujo. No se alteró esa aserción.

### Última ejecución completa

| Suite | Ejecutadas | Aprobadas | Fallidas | Errores |
|---|---:|---:|---:|---:|
| `tests.test_2fa_y_ia` | 29 | 29 | 0 | 0 |
| `tests.test_dinero_y_facturacion` | 44 | 44 | 0 | 0 |
| `tests.test_regressions` | 18 | 18 | 0 | 0 |
| **Total** | **91** | **91** | **0** | **0** |

La ejecución completa más reciente terminó con código 0.

## Cierre de sesión POST-only

El fallo de logout identificado anteriormente quedó resuelto:

- **GET `/logout` anónimo:** 405, debido a que la ruta registra únicamente el método POST y Flask rechaza el método antes de invocar `@login_required`.
- **POST sin CSRF:** la protección existente rechaza la petición y la sesión autenticada permanece activa.
- **POST con CSRF válido:** `logout_user()` elimina la sesión autenticada y la respuesta redirige a `/login`.
- **Interfaz:** el botón “Cerrar Sesión” en el layout envía un formulario POST con token CSRF.

En dos ejecuciones completas intermedias se observó una falla intermitente en `test_60_la_conciliacion_cuadra_tras_todo_lo_anterior` (`Pago de cuenta por pagar #2: sin movimiento de dinero`). La última ejecución completa volvió a aprobar las tres suites, incluida esa verificación. No se modificó la lógica financiera ni esa prueba.

No quedan fallos en la ejecución completa final; la incidencia financiera intermitente se deja registrada como observación para seguimiento.

## Comando recomendado

Desde la carpeta del proyecto, ejecutar todas las suites aisladas:

```powershell
& 'C:\SQA_Proyecto_Final\.venv\Scripts\python.exe' 'tests\ejecutar.py'
```

Para una suite individual, añadir su nombre de módulo, por ejemplo:

```powershell
& 'C:\SQA_Proyecto_Final\.venv\Scripts\python.exe' 'tests\ejecutar.py' 'tests.test_regressions'
```

El runner completo devuelve un código distinto de cero cuando alguna suite falla. Con `--coverage`, también falla si la cobertura consolidada queda por debajo del umbral configurado.
