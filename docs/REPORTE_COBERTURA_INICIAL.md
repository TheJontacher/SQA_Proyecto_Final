# Reporte inicial de cobertura

## Alcance y método

Se midió cobertura de líneas ejecutables de los nueve módulos de producción indicados, usando Coverage.py y las tres suites `unittest` existentes. No se agregaron pruebas ni se modificaron reglas clínicas o financieras. La medición no incluye cobertura de ramas (`branch = False`).

La configuración anterior en `coverage.xml` incluía únicamente `app.py` y registraba 586 de 1.388 líneas cubiertas (42,2%). Ese dato no es directamente comparable con la medición actual, que incorpora los nueve módulos de producción.

Coverage.py 7.17.2 ya estaba disponible en el entorno virtual existente. No se instalaron paquetes. Como Coverage.py no estaba declarado en `requirements.txt`, se agregó `requirements-dev.txt` para documentar la dependencia de desarrollo sin añadirla a las dependencias de runtime:

```text
-r requirements.txt
coverage>=7.0,<8
```

## Configuración

`.coveragerc` limita la medición a:

- `app`
- `database`
- `comun`
- `finanzas`
- `correo`
- `digifact`
- `rutas_dinero`
- `rutas_facturacion`
- `rutas_ia`

También omite `tests`, entornos virtuales, `backups`, rutas `temp`/`tmp`, `corregir_ventas_pasadas.py` y `migrate_sqlite_to_postgres.py`. No se excluyeron funciones de producción para mejorar artificialmente el porcentaje.

`tests/ejecutar.py --coverage` borra únicamente los datos previos de Coverage.py asociados a `.coverage.sqa`, ejecuta cada suite bajo `coverage run --parallel-mode` en un proceso separado, combina las tres mediciones y genera reportes consolidados. Al final ejecuta `coverage report --fail-under=60`; un porcentaje menor que el umbral produce un código de error. Si falla alguna prueba, el runner también devuelve código distinto de cero, aunque genera los reportes posibles para facilitar el diagnóstico.

## Comandos ejecutados

Medición reproducible, desde la raíz de la aplicación:

```powershell
Set-Location 'C:\SQA_Proyecto_Final\clinica_web_20260926_125213\clinica_web'
& 'C:\SQA_Proyecto_Final\.venv\Scripts\python.exe' 'tests\ejecutar.py' --coverage
```

Validación independiente del quality gate, después de la medición:

```powershell
& 'C:\SQA_Proyecto_Final\.venv\Scripts\python.exe' -m coverage report --fail-under=60
```

La validación independiente se ejecutó y terminó con código 0.

## Resultado de pruebas y cobertura

La ejecución completa por procesos aislados ejecutó y aprobó las **91 pruebas**:

| Suite | Ejecutadas | Aprobadas | Fallidas | Errores |
|---|---:|---:|---:|---:|
| `tests.test_2fa_y_ia` | 29 | 29 | 0 | 0 |
| `tests.test_dinero_y_facturacion` | 44 | 44 | 0 | 0 |
| `tests.test_regressions` | 18 | 18 | 0 | 0 |
| **Total** | **91** | **91** | **0** | **0** |

| Métrica consolidada | Resultado |
|---|---:|
| Líneas ejecutables | 3.899 |
| Líneas cubiertas | 2.838 |
| Líneas no cubiertas | 1.061 |
| Cobertura exacta por conteo | **72,79%** |
| Cobertura redondeada por Coverage.py | **73%** |
| Umbral obligatorio | 60% |
| Quality gate | **Aprobado** |

### Cobertura por módulo

| Módulo | Cubiertas / ejecutables | Cobertura |
|---|---:|---:|
| `comun.py` | 121 / 132 | 91,67% |
| `database.py` | 192 / 221 | 86,88% |
| `finanzas.py` | 246 / 303 | 81,19% |
| `rutas_ia.py` | 273 / 341 | 80,06% |
| `digifact.py` | 160 / 209 | 76,56% |
| `app.py` | 988 / 1.388 | 71,18% |
| `correo.py` | 73 / 105 | 69,52% |
| `rutas_facturacion.py` | 193 / 281 | 68,68% |
| `rutas_dinero.py` | 592 / 919 | 64,42% |

El módulo con menor cobertura es `rutas_dinero.py`, con 64,42%, seguido de `rutas_facturacion.py` (68,68%) y `correo.py` (69,52%). El total supera el objetivo de 60% en 12,79 puntos porcentuales.

## Archivos de reporte generados

- `coverage.xml`: reporte XML consolidado; se verificó que contiene los nueve módulos configurados y 2.838 de 3.899 líneas cubiertas.
- `htmlcov/index.html`: reporte navegable HTML consolidado.
- `.coverage.sqa`: base de datos de cobertura combinada usada por `coverage report --fail-under=60`.

El runner imprime cada suite, el directorio temporal de base y respaldos, el resumen consolidado de Coverage.py y el resultado del quality gate.

## Evidencia de aislamiento

Las tres suites se ejecutaron en procesos independientes y mostraron raíces temporales distintas:

| Suite | `CLINICA_DB` observado | `CLINICA_BACKUP_DIR` observado |
|---|---|---|
| 2FA/IA | `C:\Users\jjcar\AppData\Local\Temp\clinica_suite_lowz83so\preflight.sqlite` | `C:\Users\jjcar\AppData\Local\Temp\clinica_suite_lowz83so\backups` |
| Dinero/facturación | `C:\Users\jjcar\AppData\Local\Temp\clinica_suite_lhlgma9u\preflight.sqlite` | `C:\Users\jjcar\AppData\Local\Temp\clinica_suite_lhlgma9u\backups` |
| Regresiones | `C:\Users\jjcar\AppData\Local\Temp\clinica_suite_wpje6rbo\preflight.sqlite` | `C:\Users\jjcar\AppData\Local\Temp\clinica_suite_wpje6rbo\backups` |

Antes de arrancar cada proceso, el runner comprueba que las rutas de base de datos y respaldos estén dentro de la raíz temporal y no existan ya. Las propias suites verifican que las constantes de conexión apunten a la base temporal; los archivos de carga también se configuran bajo directorios temporales. No se ejecutó `database.py --reset`, no se usó la base clínica original ni se modificaron bases de pacientes existentes.

## Funcionalidades con líneas no cubiertas

La cobertura de líneas indica ejecución, no suficiencia de aserciones, cobertura de todas las ramas ni ausencia de defectos. Estas funciones tienen cuerpos ejecutables completamente sin cubrir en esta corrida:

| Archivo | Funciones sin cuerpo cubierto (línea inicial) |
|---|---|
| `app.py` | `api_inventario_buscar` (697), `procesar_formulario_inventario` (572), `expediente_editar` (922), `alternar_usuario` (446), `api_citas_historial_buscar` (764), `restablecer_password` (471), `inventario_editar` (1141), `cambiar_mi_password` (490), `eliminar_documento_permanente` (1791), `documentos_eliminados` (1765), `atender_cita` (1277), `api_horas_ocupadas` (752) |
| `correo.py` | `enviar_correo` (52) |
| `digifact.py` | `leer_o_crear_secreto` (95) |
| `rutas_dinero.py` | `caja_movimiento` (219), `gasto_editar` (930), `fondo_movimiento` (326), `cxp_anular` (805), `cxc_anular` (522), `proveedor_editar` (597), `gastos_categoria_editar` (1020), `cxp_editar` (735), `proveedor_alternar` (622), `cxc_editar` (456), `gastos_categoria_alternar` (1043), `_leer_proveedor` (561) |
| `rutas_facturacion.py` | `probar_conexion` (136) |
| `rutas_ia.py` | `reporte_ia_enviar` (392), `configuracion_ia_probar` (448) |

La tabla se obtuvo asociando las líneas reportadas por Coverage.py con los bloques de función del código; la declaración `def` no cuenta como prueba de que el cuerpo se ejecutó.

## Recomendaciones

El umbral del curso ya se cumple con la cobertura real observada, por lo que no se agregaron pruebas en esta etapa. Para robustecer y mantener el resultado:

1. Priorizar pruebas aisladas para las funciones no cubiertas de `rutas_dinero.py`, especialmente anulación y edición de cuentas por cobrar/pagar, movimientos de caja/fondo y validaciones de proveedores. Verificar efectos contables y estados en SQLite temporal, sin cambiar reglas de negocio.
2. Cubrir `rutas_facturacion.probar_conexion` y `rutas_ia.configuracion_ia_probar` con dobles de red/API y casos de éxito, credenciales inválidas, timeout y errores de proveedor; no hacer llamadas externas reales.
3. Cubrir `correo.enviar_correo` mediante un servidor SMTP falso o mock de transporte, comprobando fallos sin enviar mensajes reales.
4. Probar `digifact.leer_o_crear_secreto` con rutas temporales separadas, incluyendo primera creación, lectura posterior y errores de permisos.
5. Ampliar flujos de inventario, expedientes y permisos en `app.py`, usando pacientes ficticios y uploads temporales. Evitar tocar datos clínicos reales.
6. Agregar medición de ramas posteriormente si la rúbrica la exige; el 72,79% de este reporte es cobertura de líneas, no de ramas.
7. Mantener el quality gate de 60% y elevarlo gradualmente solo después de incorporar pruebas estables para los flujos críticos.

## Incidencias durante la medición

La ejecución con cobertura reportada las 91 pruebas aprobadas. En una ejecución completa anterior, sin esta medición, se observó una falla transitoria en la conciliación de dinero que no se reprodujo en las ejecuciones posteriores. En la medición actual no hubo fallas ni errores; la incidencia previa no se ocultó ni se cambió su aserción.
