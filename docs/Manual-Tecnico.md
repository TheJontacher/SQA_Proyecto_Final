# Manual técnico

## 1. Requisitos

- Python 3.12 para reproducir la versión utilizada por CI.
- `pip` y `venv`.
- SQLite, incluido en las distribuciones oficiales de Python; no se requiere un servidor de base de datos para el modo normal o las pruebas.
- Acceso a GitHub para consultar workflow/PR y acceso autorizado a SonarQube Cloud para el dashboard.
- Dependencias de runtime en `requirements.txt`; dependencias de desarrollo/calidad incluidas por `requirements-dev.txt`.

Las integraciones SMTP, IA y Digifact necesitan secretos y configuración propios para operación real. No se incluyen credenciales en este manual.

## 2. Estructura importante

| Archivo/carpeta | Responsabilidad |
|---|---|
| `app.py` | Inicializa Flask, extensiones, autenticación, CSRF y rutas principales. |
| `database.py` | Esquema SQLite, inicialización, migraciones y respaldos. |
| `comun.py` | Conexión compartida de base, utilidades de fecha/dinero, permisos y auditoría. |
| `finanzas.py` | Cálculos, movimientos, conciliación y reportes financieros. |
| `rutas_dinero.py` | Vistas/rutas de caja, fondo, CxC/CxP, gastos y reportes financieros. |
| `rutas_facturacion.py` / `digifact.py` | Configuración y rutas FEL, construcción/cliente de facturación. |
| `rutas_ia.py` | Configuración y reportes asistidos por IA. |
| `correo.py` | SMTP y códigos de verificación en dos pasos. |
| `templates/`, `static/` | Interfaz Jinja2, estilos y recursos del navegador. |
| `tests/` | Suites `unittest` y runner aislado. |
| `.coveragerc`, `ruff.toml`, `sonar-project.properties` | Alcance y parámetros de análisis de calidad. |
| `.github/workflows/ci.yml` | Pipeline de integración continua. |

## 3. Arquitectura y persistencia

La aplicación es un monolito Flask modular con renderizado de vistas Jinja2 del lado servidor. Las rutas llaman utilidades y módulos funcionales, los cuales acceden a SQLite mediante `comun.get_db()` y al inicializador de esquema `database.py`.

SQLite es el motor actual de runtime. La configuración conocida es `CLINICA_DB`; `database.DATABASE_NAME` y `comun.DATABASE` representan rutas de conexión distintas dentro del diseño y las pruebas alinean también `app.DATABASE`. `DATABASE_URL` no es el selector de SQLite empleado por el runner. No usarlo para suponer compatibilidad PostgreSQL de la aplicación; el migrador PostgreSQL independiente no convierte el runtime en una aplicación PostgreSQL.

La importación de `app.py` puede inicializar la base. Por ello, toda variable que deba influir en la base de pruebas se establece en el proceso hijo antes de importar Flask.

## 4. Instalación en Windows

Desde PowerShell, en la carpeta raíz del proyecto:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

Si la política local impide activar scripts, invocar directamente `.venv\Scripts\python.exe -m pip ...`.

## 5. Instalación en Linux

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

## 6. Configurar y ejecutar Flask

En desarrollo, desde la raíz:

```bash
python app.py
```

La aplicación usa `clinica.db` bajo la ruta de datos configurada por defecto. Antes de iniciar la aplicación contra datos reales, define secretos adecuados mediante el mecanismo de entorno/instancia ya soportado por el proyecto y protege sus archivos. No copies credenciales desde documentación o logs. `FLASK_ENV=production` y HTTPS deben configurarse coherentemente en el entorno de despliegue.

Para escoger una base SQLite alternativa, establece `CLINICA_DB` en el proceso que inicia la aplicación, antes de importar `app.py`. `CLINICA_BACKUP_DIR` permite separar la ruta de respaldos. No apuntes estas variables a carpetas compartidas si el objetivo es aislar pruebas.

## 7. Inicialización de pruebas segura

El runner realiza la configuración por suite; no es necesario exportar variables manualmente:

```bash
python tests/ejecutar.py
```

Para mantener el aislamiento:

- No ejecutar las suites mediante `unittest discover` directamente.
- No importar `app` antes de establecer `CLINICA_DB` si se ejecuta una comprobación manual.
- No ejecutar `database.py --reset`.
- No dirigir `CLINICA_DB` o `CLINICA_BACKUP_DIR` a `clinica.db`, `backups/` o datos de pacientes.
- No agregar bases, respaldos, uploads, `instance/` ni secretos a commits o artefactos.

Cada suite se ejecuta en su propio proceso y directorio temporal, con credenciales ficticias y conexión a SQLite temporal. El runner comprueba la contención de rutas antes de lanzar el proceso. Los tests sustituyen SMTP, IA y Digifact por dobles de prueba; no envían correo ni llaman a los proveedores reales.

## 8. Ejecutar las pruebas

```bash
python tests/ejecutar.py
```

Resultado esperado en la evidencia final consultada: 29 + 44 + 18 = 91 pruebas, sin fallos ni errores. El runner devuelve código distinto de cero cuando una suite falla.

## 9. Cobertura

```bash
python tests/ejecutar.py --coverage
```

El comando borra los datos previos del archivo de Coverage.py `.coverage.sqa`, ejecuta cada suite con `--parallel-mode`, combina los resultados y genera:

- `coverage.xml`
- `htmlcov/`
- `.coverage.sqa`
- Reporte de consola por módulo y total

Se aplica `coverage report --fail-under=60`. El resultado local documentado es 72,79% (2.838/3.899 líneas) sobre nueve módulos; el reporte Sonar consultado indica 72,7%. Son reportes de herramientas distintas y no se presentan con conteos idénticos. `.coveragerc` no habilita cobertura de ramas.

## 10. Ruff

```bash
python -m ruff check .
```

La versión usada en el proyecto es Ruff 0.17.3, fijada en `requirements-dev.txt`. La configuración `ruff.toml` establece Python objetivo 3.12, longitud de línea y selección explícita de reglas. El estado local final verificado fue cero hallazgos; `ruff-inicial.json` conserva 91 hallazgos de la línea base. La configuración final incluye E402 adicional para revisar imports intencionales en el entorno de pruebas.

## 11. GitHub Actions

El workflow [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) se ejecuta en push a `main` y Pull Request hacia `main`.

| Job | Función |
|---|---|
| BUILD | Instala dependencias y valida sintaxis sin importar la app ni inicializar SQLite. |
| TEST | Ejecuta `python tests/ejecutar.py --coverage`, verifica 91 pruebas, coverage ≥60% y publica reportes como artefacto. |
| ANALYZE | Ejecuta Ruff, descarga coverage XML y corre SonarQube Cloud/Quality Gate si el evento tiene acceso a los secretos. |
| REPORT | Resume estados y conserva evidencias seguras; no informa éxito global si una etapa obligatoria falla. |

Run verde verificado en `main`: [41961032413](https://github.com/TheJontacher/SQA_Proyecto_Final/actions/runs/41961032413). El run del PR está en [41960545202](https://github.com/TheJontacher/SQA_Proyecto_Final/actions/runs/41960545202).

## 12. SonarQube Cloud y secretos

`sonar-project.properties` define los módulos fuente, tests, exclusiones y la importación de `coverage.xml`. El workflow necesita las variables de repositorio `SONAR_ORGANIZATION`, `SONAR_PROJECT_KEY` y el secreto `SONAR_TOKEN`. El token se entrega a la acción como secreto de GitHub; nunca debe escribirse en archivos, logs o documentación.

El análisis configurado corresponde al proyecto [Sistema de Gestion Clinica](https://sonarcloud.io/project/overview?id=TheJontacher_SQA_Proyecto_Final). Se observó Quality Gate `OK` en la ejecución citada. Para Pull Requests desde forks, GitHub no expone secretos del repositorio al workflow; SonarCloud puede omitirse en ese evento. No deben habilitarse secretos para código no confiable como workaround.

## 13. Quality Gates y protección de `main`

- BUILD: sintaxis Python válida.
- TEST: las 91 pruebas pasan y se conserva el retorno de error del runner.
- Coverage.py: mínimo obligatorio 60%.
- Ruff: cero findings bajo la configuración versionada.
- SonarQube Cloud: Quality Gate remoto procesado y aprobado para el análisis.
- REPORT: comunica resultados reales y falla si falla una etapa obligatoria.
- Ruleset `Proteccion-main`: exige PR, checks BUILD/TEST/ANALYZE/REPORT y resolución de conversaciones. No exige aprobación de reviewer según la configuración consultada.

El PR [#1](https://github.com/TheJontacher/SQA_Proyecto_Final/pull/1) fue integrado a `main`. Este flujo no despliega automáticamente.

## 14. Reportes y evidencia

- [`REPORTE_CORRECCION_PRUEBAS.md`](REPORTE_CORRECCION_PRUEBAS.md)
- [`REPORTE_COBERTURA_INICIAL.md`](REPORTE_COBERTURA_INICIAL.md)
- [`IMPLEMENTACION_CICD.md`](IMPLEMENTACION_CICD.md)
- [`Linea-Base-Calidad.md`](Linea-Base-Calidad.md)
- [`Matriz-Pruebas.md`](Matriz-Pruebas.md)
- [`GUIA_EVIDENCIAS.md`](GUIA_EVIDENCIAS.md)

No se deben adjuntar `clinica.db`, respaldos, uploads, secretos, `instance/` ni datos clínicos. Los artifacts de Actions tienen retención limitada; guardar capturas académicas desidentificadas según la guía.

## 15. Problemas frecuentes

| Síntoma | Causa probable / resolución segura |
|---|---|
| `app.DATABASE_URL` no existe | La configuración actual de pruebas usa `CLINICA_DB`; no introducir `DATABASE_URL` como atributo inventado. |
| Error de tabla/columna durante prueba | Revisar fixture y esquema vigente en DB temporal; no migrar ni alterar la base real para satisfacer un test antiguo. |
| Suite afecta otra suite | Usar `tests/ejecutar.py`, que separa proceso, ruta SQLite y directorio temporal por suite. |
| 302 en ruta protegida | Confirmar sesión y rol que exige el código; no cambiar autorización solo para obtener 200. |
| 400 por POST | Revisar token CSRF de la petición de prueba. |
| Coverage bajo 60% | Consultar `htmlcov/` y cubrir comportamientos no probados; no excluir módulos/funciones arbitrariamente. |
| Sonar no corre en PR externo | El workflow no recibe secretos de forks; debe mantenerse omitido o ejecutarse desde contexto confiable. |
| Open redirect reportado por Sonar | Revisar el flujo de destino externo en `app.py`; no ignorar el finding solo porque otro test de redirección pasa. |
| Diferencia de fecha local/CI | Usar helpers de calendario de clínica para filtros de negocio y fixtures con fechas explícitas. |

## 16. Seguridad y operación

Este manual no autoriza operaciones sobre datos reales. No ejecutar scripts destructivos, migraciones, respaldos ni reset de base durante las pruebas. Antes de cualquier operación de mantenimiento productiva, usar el proceso operativo aprobado, respaldo verificado y revisión humana; dichas tareas no forman parte del pipeline descrito.
