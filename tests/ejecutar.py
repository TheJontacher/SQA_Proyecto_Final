"""Ejecuta cada archivo de pruebas en su propio proceso (cada uno usa su base temporal y la aplicación
se importa una sola vez por proceso).

    python tests/ejecutar.py
"""
import glob
import os
import subprocess
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
fallos = 0
for archivo in sorted(glob.glob(os.path.join(RAIZ, 'tests', 'test_*.py'))):
    modulo = 'tests.' + os.path.basename(archivo)[:-3]
    print(f'== {modulo}', flush=True)
    fallos += subprocess.call([sys.executable, '-W', 'ignore', '-m', 'unittest', modulo], cwd=RAIZ) != 0
sys.exit(1 if fallos else 0)
