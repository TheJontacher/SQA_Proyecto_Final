# Reporte de mejora de calidad

## 1. Resumen ejecutivo

El proyecto partía de pruebas inestables o incompatibles con la configuración vigente, cobertura limitada a `app.py`, 91 hallazgos de Ruff y ausencia de un pipeline integrado. Se corrigió la infraestructura de pruebas, se consolidó Coverage.py, se limpió Ruff bajo reglas explícitas y se implementaron cuatro jobs en GitHub Actions con análisis SonarQube Cloud y protección de `main`.

La evidencia final consultada registra 91 pruebas aprobadas, 72,79% de cobertura local de líneas sobre nueve módulos, Ruff sin hallazgos y un run remoto en el que BUILD, TEST, ANALYZE y REPORT terminaron correctamente. SonarQube Cloud reportó Quality Gate `OK`, pero también mantiene una vulnerabilidad global abierta de severidad BLOCKER; por ello, el resultado no se interpreta como ausencia de problemas de seguridad.

## 2. Problemas identificados

- Suites compartían importaciones/estado global y podían inicializar la aplicación antes de definir la ruta de base de datos.
- La configuración de prueba no se aplicaba de forma uniforme: coexistían referencias obsoletas a `app.DATABASE_URL` y `INITIAL_ADMIN_PASSWORD`.
- Parte de las expectativas reflejaba nombres de columnas, tablas, rutas, campos o respuestas anteriores al código vigente.
- Logout necesitaba estar limitado a POST y conservar protección CSRF.
- Coverage previo medía `app.py` solamente (42,22%), sin quality gate consolidado.
- La línea base inicial de Ruff registraba 91 hallazgos.
- Faltaban GitHub Actions y SonarQube Cloud en el diagnóstico inicial.
- Se reprodujo una flake de conciliación por fechas implícitas en fixtures y una regresión de reporte sensible al día/zona horaria del host.
- Las salidas de prueba de CI filtraban demasiada información diagnóstica, aunque volcar la salida completa habría podido exponer datos de respuesta.

## 3. Correcciones implementadas

### Aislamiento y compatibilidad de pruebas

`tests/ejecutar.py` inicia cada suite en un proceso y directorio temporal propios. Fija las variables de entorno antes de importar Flask, valida que las rutas de la base y respaldos estén dentro del temporal y que no existan al iniciar. Las suites verifican que las constantes de `database.py`, `comun.py` y `app.py` coincidan. El runner elimina un `DATABASE_URL` heredado que no es el mecanismo de selección SQLite de runtime.

Se sustituyeron atributos/configuración obsoletos por `CLINICA_DB` y `CLINICA_ADMIN_PASSWORD`, y se actualizaron fixtures/solicitudes para reflejar esquema y rutas activos. No se cambiaron credenciales de producción, no se borraron pruebas ni se relajaron aserciones.

### Logout POST con CSRF

`/logout` acepta únicamente POST. GET devuelve 405 incluso en contexto anónimo; POST sin token CSRF válido se rechaza y conserva la sesión; POST válido usa Flask-Login para cerrar la sesión. La plantilla usa formulario POST con token CSRF.

### Estabilidad de fechas y conciliación

La incidencia se reprodujo en `test_60_la_conciliacion_cuadra_tras_todo_lo_anterior`. El fixture insertaba un pago legado con fecha implícita de `CURRENT_TIMESTAMP`, mientras que el corte de tesorería se inicializaba con precisión de segundos. Si ambas marcas caían en el mismo segundo, el movimiento podía compararse como posterior al corte. La corrección fija una fecha histórica inequívocamente anterior al corte y comprueba esa condición en el test; no cambia el algoritmo ni las reglas financieras.

También se reprodujo una diferencia de fecha entre el reloj UTC del runner y el calendario clínico UTC−6: el reporte filtraba usando `date.today()` del host. La regresión usa `comun.hoy_local()` para el periodo clínico. La prueba de edad mantiene el uso de fecha de sistema porque ese es el comportamiento que verifica en la función correspondiente.

## 4. Cobertura

Coverage.py se ejecuta por procesos con acumulación paralela y reporte combinado XML/HTML. `.coveragerc` limita las métricas a nueve módulos de runtime, no excluye funciones para inflar el porcentaje y deja fuera tests, entornos, respaldos, temporales y scripts de mantenimiento/migración independientes del runtime.

| Métrica | Resultado |
|---|---:|
| Cobertura histórica en `app.py` | 42,22% (586/1.388), solo ese archivo |
| Cobertura local consolidada | 72,79% (2.838/3.899), nueve módulos |
| Umbral obligatorio | 60% |
| Casos ejecutados | 91/91 |
| Suite 2FA/IA | 29/29 |
| Suite dinero/facturación | 44/44 |
| Suite regresiones | 18/18 |

La línea base de 42,22% no es directamente comparable con 72,79%, porque cambió el conjunto de módulos medidos. La cobertura de líneas tampoco equivale a cobertura de ramas.

## 5. Ruff

La evidencia inicial contiene 91 hallazgos; la ejecución final local con Ruff 0.17.3 produjo cero hallazgos. La configuración final conserva las reglas encontradas en la línea base y añade explícitamente `E402`. `E402` se usa para validar anotaciones `noqa: E402` en imports que las suites deben realizar después de preparar las variables de entorno. Por este cambio de selección, la comparación se presenta como antes/después del estado observado y su configuración, no como una ejecución idéntica bajo reglas totalmente invariantes.

Las correcciones aplicadas fueron controladas y no modificaron reglas clínicas o financieras. La evidencia original `ruff-inicial.json` se conserva.

## 6. Pipeline y SonarQube Cloud

`.github/workflows/ci.yml` implementa:

1. **BUILD:** Python 3.12, dependencias de desarrollo y análisis sintáctico sin importar la aplicación.
2. **TEST:** runner aislado, exactamente 91 pruebas, Coverage.py con mínimo 60% y artefactos de cobertura.
3. **ANALYZE:** Ruff y análisis SonarQube Cloud/Quality Gate cuando los secretos están disponibles.
4. **REPORT:** resumen de estados reales, publicación de evidencias permitidas y resultado no exitoso si una etapa obligatoria falla.

En el análisis SonarQube Cloud consultado, el Quality Gate fue `OK`, con cobertura reportada de 72,7%. Las métricas globales incluyeron 0 bugs, 1 vulnerabilidad, 107 code smells y 0 hotspots. El issue global `pythonsecurity:S5146` (BLOCKER) está relacionado con posible open redirect en `app.py:300`. El gate de código nuevo aprobado y el finding global son datos distintos; el gate no autoriza a concluir que no existen problemas de seguridad.

## 7. Protección de `main` y Pull Request

El ruleset activo `Proteccion-main` exige Pull Request, checks BUILD/TEST/ANALYZE/REPORT y resolución de conversaciones. No exige aprobación de reviewer. El [PR #1](https://github.com/TheJontacher/SQA_Proyecto_Final/pull/1) fue integrado a `main`.

El run [41921496430](https://github.com/TheJontacher/SQA_Proyecto_Final/actions/runs/41921496430) sirve de evidencia del estado fallido inicial. Los checks del PR están en [41960545202](https://github.com/TheJontacher/SQA_Proyecto_Final/actions/runs/41960545202), y el run verde en `main` posterior al merge está en [41961032413](https://github.com/TheJontacher/SQA_Proyecto_Final/actions/runs/41961032413).

## 8. Comparación de métricas

| Métrica | Inicial | Final observado | ¿Comparación directa? |
|---|---|---|---|
| Pruebas | 17 aprobadas, 61 fallidas, 29 errores de preparación en diagnóstico histórico; 70 subpruebas aprobadas | 91/91 aprobadas en run verde | No es una partición homogénea; los errores y subpruebas se reportaron aparte. |
| Cobertura | 42,22%, solo `app.py` (586/1.388) | 72,79%, nueve módulos (2.838/3.899) | No: el alcance cambió. |
| Ruff | 91 findings, `ruff-inicial.json` | 0 findings; la configuración final añade `E402` | Parcial: reglas finales incluyen E402 adicional. |
| CI | Sin workflow operativo en el análisis inicial | BUILD/TEST/ANALYZE/REPORT verdes en `main` | Evolución de capacidad, no una métrica porcentual. |
| SonarQube Cloud | No conectado en el corte inicial | Quality Gate OK; 1 vulnerabilidad global y 107 code smells | No comparable cuantitativamente en ausencia de medición inicial. |
| Protección de rama | No documentada inicialmente | Ruleset activo, PR y cuatro checks obligatorios | Estado de proceso. |

## 9. Riesgos y pendientes

- Triar y resolver o documentar formalmente el hallazgo de open redirect `pythonsecurity:S5146` reportado en `app.py:300`; confirmar la ruta de datos antes de cambiar comportamiento.
- Agregar pruebas priorizadas para funciones de baja cobertura, especialmente rutas de caja/finanzas, facturación y errores de correo/IA, sin llamadas externas reales.
- Probar, con credenciales de sandbox y autorización del responsable, integraciones externas reales antes de producción; estas no se validan en las suites actuales.
- Considerar cobertura de ramas, pruebas de carga y una estrategia de lockfile si forman parte de los criterios posteriores.
- No hay capturas finales incluidas en este repositorio; la guía de evidencias las deja identificadas como pendientes.

## 10. Conclusiones

La infraestructura pasó de suites con incompatibilidades y acceso potencial a estado compartido a una ejecución reproducible por proceso con bases temporales. Las métricas locales superan el gate académico de cobertura del 60%, Ruff no reporta findings bajo la configuración final y existe evidencia remota de CI/SonarQube Cloud integrada con Pull Requests.

La mejora del proceso no equivale a ausencia absoluta de defectos. Deben mantenerse visibles la vulnerabilidad global informada por Sonar, las funciones sin pruebas, los límites del coverage de líneas y la falta de validación contra proveedores externos.
