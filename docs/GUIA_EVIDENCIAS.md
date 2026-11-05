# Guía de evidencias para informe y presentación

## Reglas de captura

- Capturar evidencia del repositorio y del run indicados; mantener visible la URL o el identificador de ejecución.
- No mostrar tokens, secretos, cookies, valores de configuración sensibles, datos clínicos, archivos SQLite, respaldos, uploads o rutas privadas de usuarios.
- Recortar o difuminar nombres de cuenta que no sean necesarios.
- Las capturas aún no están guardadas en el repositorio. Cada elemento se marca como `[PENDIENTE: captura]`; los enlaces permiten localizar la fuente, pero no sustituyen una imagen si la rúbrica la exige.
- No fabricar ni reconstruir una pantalla histórica. Si una vista transitoria ya no está disponible, marcarla como no recuperable.

## Capturas requeridas

| # | Evidencia/captura | Fuente y contenido que debe quedar visible | Estado |
|---:|---|---|---|
| 1 | Las 91 pruebas aprobadas | TEST del [run verde #41961032413](https://github.com/TheJontacher/SQA_Proyecto_Final/actions/runs/41961032413); mostrar 29 + 44 + 18 y `Test cases executed: 91; expected: 91`. | [PENDIENTE: captura] |
| 2 | Cobertura total | `coverage.xml`/`htmlcov` generado por el runner o artefacto del run; indicar 72,79% local y umbral 60%, junto con el alcance de nueve módulos. | [PENDIENTE: captura] |
| 3 | Cobertura por módulo | Tabla de Coverage.py/HTML mostrando los nueve módulos y porcentajes del [reporte inicial](REPORTE_COBERTURA_INICIAL.md). No mezclar con métricas Sonar. | [PENDIENTE: captura] |
| 4 | Ruff inicial | Abrir `ruff-inicial.json`/reporte preservado y mostrar 91 hallazgos, sin exponer rutas privadas si aparecen. | [PENDIENTE: captura] |
| 5 | Ruff final | Log verde ANALYZE o terminal con `ruff check .` y `All checks passed!`; registrar versión y configuración. | [PENDIENTE: captura] |
| 6 | Cuatro jobs aprobados | Vista completa del run [#41961032413](https://github.com/TheJontacher/SQA_Proyecto_Final/actions/runs/41961032413) con BUILD, TEST, ANALYZE y REPORT en verde. | [PENDIENTE: captura] |
| 7 | SonarQube Quality Gate | Vista del proyecto [Sistema de Gestion Clinica](https://sonarcloud.io/project/overview?id=TheJontacher_SQA_Proyecto_Final), mostrando `OK` y análisis correspondiente. Agregar nota escrita de que hay una vulnerabilidad global reportada; no recortar para sugerir cero hallazgos. | [PENDIENTE: captura] |
| 8 | Ruleset de `main` | Settings → Rules → Rulesets, regla `Proteccion-main`, rama objetivo `main`, PR requerido y checks obligatorios. Ocultar información de configuración no pertinente. | [PENDIENTE: captura] |
| 9 | PR con checks pendientes | PR #1 o un PR de demostración en estado previo a completar el workflow; debe mostrar checks `Pending` durante ejecución, no una pantalla inventada. | [PENDIENTE: captura histórica; comprobar si puede recuperarse. Si no, indicar no disponible.] |
| 10 | PR con checks aprobados | PR [#1](https://github.com/TheJontacher/SQA_Proyecto_Final/pull/1), checks completados y estado aprobatorio enlazado al [run #41960545202](https://github.com/TheJontacher/SQA_Proyecto_Final/actions/runs/41960545202). | [PENDIENTE: captura] |
| 11 | PR integrado | PR #1 mostrando estado merged a `main` y SHA del merge. | [PENDIENTE: captura] |

## Evidencia auxiliar y enlaces

- Run fallido inicial para explicar el ciclo de mejora: [Actions #41921496430](https://github.com/TheJontacher/SQA_Proyecto_Final/actions/runs/41921496430). Etiquetarlo como histórico/fallido, no como estado final.
- Run con checks del Pull Request: [Actions #41960545202](https://github.com/TheJontacher/SQA_Proyecto_Final/actions/runs/41960545202).
- Run verde posterior al merge: [Actions #41961032413](https://github.com/TheJontacher/SQA_Proyecto_Final/actions/runs/41961032413).
- Pull Request integrado: [#1](https://github.com/TheJontacher/SQA_Proyecto_Final/pull/1).
- Dashboard SonarQube Cloud: [proyecto](https://sonarcloud.io/project/overview?id=TheJontacher_SQA_Proyecto_Final).

## Lista de control antes de entregar

- [ ] Cada captura permite identificar ejecución, proyecto y resultado sin revelar secretos.
- [ ] La evidencia de cobertura identifica que se trata de nueve módulos y cobertura de líneas.
- [ ] El antes/después de Ruff cita la diferencia de selección `E402`.
- [ ] La captura de Sonar no oculta ni contradice la vulnerabilidad global abierta.
- [ ] La evidencia de branch protection muestra checks y requisito PR, sin afirmar revisión obligatoria si no está configurada.
- [ ] La pantalla de checks pendientes es auténtica; si no puede recuperarse, se deja el marcador pendiente.
- [ ] No se incluyen archivos de bases de datos, respaldos ni datos clínicos.
