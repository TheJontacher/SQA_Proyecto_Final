"""Ejecuta cada suite en su propio proceso y en un directorio temporal exclusivo.

    python tests/ejecutar.py
    python tests/ejecutar.py --coverage
"""
import argparse
import glob
import os
import re
import subprocess
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_COBERTURA = os.path.join(RAIZ, '.coveragerc')
archivos = sorted(glob.glob(os.path.join(RAIZ, 'tests', 'test_*.py')))
modulos = {
    'tests.' + os.path.basename(archivo)[:-3]: archivo
    for archivo in archivos
}
parser = argparse.ArgumentParser(description='Ejecuta suites aisladas de la clínica.')
parser.add_argument(
    '--coverage',
    action='store_true',
    help='acumula Coverage.py y genera reportes con quality gate de 60%%',
)
parser.add_argument('suites', nargs='*', help='módulos de suite a ejecutar')
opciones = parser.parse_args()
solicitados = opciones.suites
desconocidos = sorted(set(solicitados) - set(modulos))
if desconocidos:
    raise SystemExit(f'Suites desconocidas: {", ".join(desconocidos)}')
if solicitados:
    archivos = [modulos[modulo] for modulo in solicitados]
fallos = 0
resumenes = []


def texto_diagnostico_seguro(mensaje):
    """Reduce los cuerpos de respuesta y valores sensibles antes de imprimir fallos."""
    mensaje = re.sub(r'\x1b\[[0-9;]*m', '', mensaje)
    if '<html' in mensaje.lower() or '<!doctype html' in mensaje.lower():
        return 'la respuesta HTML no coincide; se omitió su contenido'
    if ' not found in ' in mensaje or ' not in ' in mensaje:
        return 'no se encontró el contenido esperado; se omitió la respuesta'
    for valor in (
        'isolated-test-admin-password',
        'Admin12345',
        'Cajera12345',
        'clave-de-prueba',
        'Wjt0ZfG4m9mS5y0y2o5l0tQ1w3e6r8t0y2u4i6o8pA=',
    ):
        mensaje = mensaje.replace(valor, '<redactado>')
    mensaje = re.sub(
        r'(?i)\b(password|passwd|secret|token|authorization|cookie|csrf)'
        r'([\'"]?\s*[:=]\s*[\'"]?)[^,\s\'"]+',
        r'\1\2<redactado>',
        mensaje,
    )
    return mensaje[:300] + ('…' if len(mensaje) > 300 else '')


def informar_fallos(modulo, salida):
    """Publica nombres y motivos resumidos, sin volcar datos de pruebas o respuestas."""
    encabezados = list(re.finditer(r'^(FAIL|ERROR): (.+)$', salida, re.MULTILINE))
    for indice, encabezado in enumerate(encabezados):
        fin = encabezados[indice + 1].start() if indice + 1 < len(encabezados) else len(salida)
        bloque = salida[encabezado.start():fin]
        print(f'TEST FAILURE: {encabezado.group(2)}', flush=True)
        marcos = re.findall(
            r'File "([^"]+)", line (\d+), in ([^\r\n]+)',
            bloque,
        )
        marcos_prueba = []
        for marco in marcos:
            try:
                raiz_comun = os.path.commonpath((RAIZ, os.path.realpath(marco[0])))
            except ValueError:
                continue
            if os.path.normcase(raiz_comun) == os.path.normcase(RAIZ):
                marcos_prueba.append(marco)
        if marcos_prueba:
            ruta, linea, funcion = marcos_prueba[-1]
            ruta_relativa = os.path.relpath(ruta, RAIZ).replace(os.sep, '/')
            print(f'TEST LOCATION: {ruta_relativa}:{linea} ({funcion})', flush=True)
        excepciones = re.findall(
            r'^\s*([A-Za-z_][\w.]*(?:Error|Exception))(?::\s*(.*))?$',
            bloque,
            re.MULTILINE,
        )
        if excepciones:
            tipo, mensaje = excepciones[-1]
            razon = texto_diagnostico_seguro(mensaje or '')
            print(
                f'TEST REASON: {tipo}'
                + (f': {razon}' if razon else ''),
                flush=True,
            )
        else:
            print('TEST REASON: no se pudo extraer el tipo de error', flush=True)

    if not encabezados:
        print(
            f'TEST FAILURE: {modulo} salió con error sin un registro FAIL/ERROR de unittest',
            flush=True,
        )
        excepciones = re.findall(
            r'^\s*([A-Za-z_][\w.]*(?:Error|Exception))(?::\s*(.*))?$',
            salida,
            re.MULTILINE,
        )
        if excepciones:
            tipo, mensaje = excepciones[-1]
            razon = texto_diagnostico_seguro(mensaje or '')
            print(
                f'TEST REASON: {tipo}'
                + (f': {razon}' if razon else ''),
                flush=True,
            )


cobertura_env = os.environ.copy()
cobertura_env['COVERAGE_FILE'] = os.path.join(RAIZ, '.coverage.sqa')
if opciones.coverage:
    if not os.path.isfile(CONFIG_COBERTURA):
        raise SystemExit(f'No existe la configuración de cobertura: {CONFIG_COBERTURA}')
    borrado = subprocess.run(
        [sys.executable, '-m', 'coverage', 'erase'],
        cwd=RAIZ,
        env=cobertura_env,
        capture_output=True,
        text=True,
    )
    if borrado.returncode:
        raise SystemExit(
            'No se pudo limpiar la medición de cobertura anterior:\n'
            + borrado.stdout
            + borrado.stderr
        )

for archivo in archivos:
    modulo = 'tests.' + os.path.basename(archivo)[:-3]
    with tempfile.TemporaryDirectory(prefix='clinica_suite_') as raiz_temporal:
        db = os.path.join(raiz_temporal, 'preflight.sqlite')
        backups = os.path.join(raiz_temporal, 'backups')
        entorno = os.environ.copy()
        entorno.update({
            'CLINICA_TEST_ROOT': raiz_temporal,
            'CLINICA_DB': db,
            'CLINICA_BACKUP_DIR': backups,
            'CLINICA_ADMIN_PASSWORD': 'isolated-test-admin-password',
            'SECRET_KEY': 'clave-de-prueba',
            'FISCAL_ENCRYPTION_KEY': 'WjtZ0ZfG4m9mS5y0y2o5l0tQ1w3e6r8t0y2u4i6o8pA=',
            'FLASK_DEBUG': '0',
        })
        entorno.pop('DATABASE_URL', None)
        if opciones.coverage:
            entorno['COVERAGE_FILE'] = cobertura_env['COVERAGE_FILE']

        temporal = os.path.realpath(raiz_temporal)
        for ruta in (db, backups):
            if os.path.commonpath((temporal, os.path.realpath(ruta))) != temporal:
                raise RuntimeError(f'La ruta de prueba no está aislada: {ruta}')
        if os.path.exists(db) or os.path.exists(backups):
            raise RuntimeError(f'El directorio temporal de prueba no está vacío: {raiz_temporal}')

        print(f'== {modulo} (proceso aislado)', flush=True)
        print(f'   CLINICA_DB={db}', flush=True)
        print(f'   CLINICA_BACKUP_DIR={backups}', flush=True)
        comando = [sys.executable, '-W', 'ignore']
        if opciones.coverage:
            comando.extend([
                '-m', 'coverage', 'run', '--parallel-mode',
                '--rcfile', CONFIG_COBERTURA, '-m',
            ])
        else:
            comando.append('-m')
        comando.extend(['unittest', modulo, '-v'])
        resultado = subprocess.run(
            comando,
            cwd=RAIZ,
            env=entorno,
            capture_output=True,
            text=True,
        )
        salida = resultado.stdout + resultado.stderr
        coincidencia = re.search(r'^Ran (\d+) tests? in ', salida, re.MULTILINE)
        cantidad = int(coincidencia.group(1)) if coincidencia else None
        resumenes.append((modulo, cantidad, resultado.returncode))
        if cantidad is not None:
            print(f'Ran {cantidad} tests in isolated process', flush=True)
        if resultado.returncode:
            informar_fallos(modulo, salida)
            fallos += 1
            print('FAILED', flush=True)
        else:
            print('OK', flush=True)

print('\n== Resumen')
for modulo, pruebas, codigo in resumenes:
    cantidad = str(pruebas) if pruebas is not None else 'sin resumen unittest'
    print(f'{modulo}: {cantidad}; exit code {codigo}')

if opciones.coverage:
    for titulo, comando in (
        ('Combinar cobertura', [sys.executable, '-m', 'coverage', 'combine']),
        ('Reporte de cobertura / quality gate 60%', [
            sys.executable, '-m', 'coverage', 'report', '--fail-under=60', '-m',
        ]),
        ('Generar coverage.xml', [
            sys.executable, '-m', 'coverage', 'xml', '-o', 'coverage.xml',
        ]),
        ('Generar reporte HTML', [
            sys.executable, '-m', 'coverage', 'html', '-d', 'htmlcov',
        ]),
    ):
        resultado = subprocess.run(
            comando,
            cwd=RAIZ,
            env=cobertura_env,
            capture_output=True,
            text=True,
        )
        print(f'\n== {titulo}', flush=True)
        salida = resultado.stdout + resultado.stderr
        print(salida, end='' if salida.endswith('\n') else '\n', flush=True)
        if resultado.returncode:
            fallos += 1
            print(f'Comando falló con exit code {resultado.returncode}.', flush=True)

sys.exit(1 if fallos else 0)
