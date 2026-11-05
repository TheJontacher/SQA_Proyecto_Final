
# Demostración del pipeline CI/CD

## Objetivo

Verificar que GitHub Actions ejecute las etapas de
aseguramiento de calidad antes de integrar cambios
a la rama principal.

## Controles implementados

- BUILD: validación de sintaxis.
- TEST: 91 pruebas y cobertura mínima del 60%.
- ANALYZE: Ruff y SonarQube Cloud.
- REPORT: generación de resultados y evidencias.

## Resultado esperado

El Pull Request podrá integrarse únicamente cuando
las verificaciones obligatorias hayan aprobado.
