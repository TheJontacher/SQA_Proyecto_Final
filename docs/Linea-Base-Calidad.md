# Línea base y estado final de calidad

## Alcance temporal

Este documento separa el diagnóstico inicial, las mediciones locales y la evidencia remota. El archivo [ANALISIS_COMPLETO_PROYECTO_SQA.md](../ANALISIS_COMPLETO_PROYECTO_SQA.md) es una fotografía técnica anterior a la implementación del pipeline; su afirmación de que no había GitHub Actions/SonarQube es histórica y no describe el estado actual.

## Estado inicial documentado

| Área | Línea base | Alcance / interpretación |
|---|---|---|
| Ruff | 91 hallazgos guardados en `ruff-inicial.json`. | Resultado de la configuración de análisis de esa línea base. Incluye scripts y pruebas entre los archivos hallados. |
| Pruebas | Diagnóstico inicial reportado: 91 detectadas, 17 aprobadas, 61 fallidas, 29 errores de preparación y 70 subpruebas aprobadas. | Es un registro histórico proporcionado durante el proyecto; no es una partición aditiva de 91 casos, pues incluye errores de preparación y subpruebas. |
| Base de pruebas | Suite con rutas/globales compartidos e inicialización de aplicación antes de configurar entorno en algunos puntos. | Existía riesgo de usar distintas bases o inicializar antes de fijar la ruta temporal. |
| Compatibilidad | Referencias a `app.DATABASE_URL` y uso de `INITIAL_ADMIN_PASSWORD` no correspondían a la interfaz de configuración vigente. | Causaban errores de atributo/configuración y discrepancias de esquema/rutas. |
| Cobertura histórica | 586 de 1.388 líneas de `app.py`, 42,22%. | `coverage.xml` de ese corte medía únicamente `app.py`; no es directamente comparable con la cobertura consolidada posterior de nueve módulos. |
| CI/CD | El análisis técnico inicial no encontró workflow operativo ni proyecto SonarQube Cloud conectado en ese corte. | Situación previa a la configuración/ejecuciones reales descritas abajo. |

### Errores iniciales reportados

Se observaron `sqlite3.OperationalError: no such column: email`, `no such table: cuentas_por_cobrar`, `AttributeError: module 'app' has no attribute 'DATABASE_URL'`, además de respuestas 302/404 ante expectativas 200. Las causas investigadas incluyeron esquema/fixtures obsoletos, rutas actuales distintas, configuración de prueba aplicada después de importar la aplicación y expectativas antiguas de autenticación/rutas.

## Estado final verificado

### Pruebas y aislamiento

En el run verde posterior a la integración del PR #1, el job TEST reportó 29, 44 y 18 pruebas y verificó `Test cases executed: 91; expected: 91`. El run [41961032413](https://github.com/TheJontacher/SQA_Proyecto_Final/actions/runs/41961032413) terminó con BUILD, TEST, ANALYZE y REPORT exitosos.

`tests/ejecutar.py` prepara cada suite en un proceso y una raíz temporal distintos antes de importar Flask. Configura `CLINICA_DB` y `CLINICA_BACKUP_DIR`, valida que ambas rutas estén contenidas en la raíz temporal y rechaza ubicaciones preexistentes. Las suites verifican que `database.DATABASE_NAME`, `comun.DATABASE` y `app.DATABASE` se alineen. Los tests usan datos ficticios y dobles de integración; no requieren `clinica.db`, respaldos ni servicios externos reales.

### Cobertura

| Medición | Resultado | Alcance |
|---|---:|---|
| Línea base histórica | 586 / 1.388 = 42,22% | Solo `app.py`, archivo `coverage.xml` anterior. |
| Medición local consolidada | 2.838 / 3.899 = 72,79% | Nueve módulos configurados en `.coveragerc`, cobertura de líneas; `branch = False`. |
| Quality Gate local | ≥60%; aprobado | `coverage report --fail-under=60`. |
| SonarQube Cloud | 72,7% | Medición publicada por Sonar; su reporte muestra 1.063 líneas no cubiertas frente a 1.061 en la medición local. Se conserva cada métrica en el contexto de su herramienta/ejecución y no se fuerza equivalencia exacta. |

La diferencia entre 42,22% y 72,79% no es una comparación de mejora directa: cambió el conjunto de código medido, de un archivo a nueve módulos. La medición actual tampoco equivale a cobertura de ramas ni demuestra que cada comportamiento esté correctamente probado.

### Análisis estático

`ruff-inicial.json` contiene 91 hallazgos. La verificación local final de Ruff 0.17.3 ejecutó `ruff check .` y respondió `All checks passed!`. La configuración final de `ruff.toml` selecciona las reglas de la línea base y agrega explícitamente `E402`, relacionado con imports intencionales después de establecer entorno de pruebas; por tanto, se documenta cero hallazgos finales y una diferencia explícita en selección, no una equivalencia byte por byte de configuraciones.

### CI, Pull Request y SonarQube Cloud

- El workflow público ejecutó los cuatro jobs en verde: [run principal posterior al merge #41961032413](https://github.com/TheJontacher/SQA_Proyecto_Final/actions/runs/41961032413).
- El PR [#1](https://github.com/TheJontacher/SQA_Proyecto_Final/pull/1) fue integrado en `main`; la ejecución de checks del PR está en [run #41960545202](https://github.com/TheJontacher/SQA_Proyecto_Final/actions/runs/41960545202).
- Un run anterior fallido, útil como evidencia del diagnóstico y corrección de la inestabilidad, es [#41921496430](https://github.com/TheJontacher/SQA_Proyecto_Final/actions/runs/41921496430). No debe presentarse como estado final.
- El ruleset activo `Proteccion-main` exige Pull Request, los checks BUILD, TEST, ANALYZE y REPORT, y resolución de conversaciones. La regla no exige aprobación de un reviewer.
- El proyecto [Sistema de Gestion Clinica en SonarQube Cloud](https://sonarcloud.io/project/overview?id=TheJontacher_SQA_Proyecto_Final) tuvo Quality Gate `OK` en el análisis consultado.

### Métricas globales publicadas por SonarQube Cloud

| Métrica global | Valor consultado |
|---|---:|
| Bugs | 0 |
| Vulnerabilidades abiertas | 1 |
| Code smells | 107 |
| Security hotspots | 0 |
| Cobertura | 72,7% |
| Duplicación | 0% |
| Reliability rating | A |
| Maintainability rating | A |
| Security rating | E |
| Quality Gate | OK |

Sonar reporta el issue `pythonsecurity:S5146`, severidad BLOCKER, en `app.py:300`, relacionado con un posible open redirect por parámetro de URL en `_entrar()`. Es un hallazgo del análisis estático que requiere revisión; esta documentación no afirma que exista explotación confirmada. Un Quality Gate global `OK` no elimina este hallazgo.

En la vista de nuevo código consultada, Sonar reportó cero bugs, cero vulnerabilidades y cero code smells nuevos, y 100% de hotspots nuevos revisados. La API consultada no proporcionó un valor verificable de `new_coverage`; no se publica una cifra para esa métrica.

## Fuentes de evidencia

- [Reporte inicial de cobertura](REPORTE_COBERTURA_INICIAL.md): comandos, cobertura por módulo, salidas e aislamiento local.
- [Reporte de corrección de pruebas](REPORTE_CORRECCION_PRUEBAS.md): compatibilidad del runner, aislamiento y resultados de pruebas.
- [Implementación CI/CD](IMPLEMENTACION_CICD.md): workflow, jobs y operación.
- [Run Actions #41961032413](https://github.com/TheJontacher/SQA_Proyecto_Final/actions/runs/41961032413): verificación remota completa.
- [PR #1](https://github.com/TheJontacher/SQA_Proyecto_Final/pull/1): revisión e integración a `main`.
- [SonarQube Cloud](https://sonarcloud.io/project/overview?id=TheJontacher_SQA_Proyecto_Final): Quality Gate y métricas del análisis consultado.

## Limitaciones

La evidencia remota corresponde a ejecuciones identificadas y al commit evaluado; no garantiza que cambios futuros pasen. No hay pruebas de servicios reales ni cobertura de ramas. No se compara directamente el reporte histórico de `app.py` con la medición consolidada. Las capturas visuales no están guardadas en el repositorio y deben prepararse según la [guía de evidencias](GUIA_EVIDENCIAS.md).
