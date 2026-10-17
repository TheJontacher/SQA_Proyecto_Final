"""Utilidades compartidas por toda la aplicación: base de datos, hora local,
dinero, permisos, auditoría y paginación."""
import csv
import io
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps

from flask import Response, flash, g, redirect, request, url_for
from flask_login import current_user

from database import DATABASE_NAME

DATABASE = DATABASE_NAME

# La base guarda todas las fechas en UTC (CURRENT_TIMESTAMP). Para mostrarlas y para decidir
# "qué día fue" se pasan a la hora de la clínica (Guatemala: UTC-6, sin horario de verano).
ZONA_HORARIA_HORAS = int(os.environ.get('ZONA_HORARIA_HORAS', '-6'))
DESFASE_LOCAL = timedelta(hours=ZONA_HORARIA_HORAS)
FORMATO_BD = '%Y-%m-%d %H:%M:%S'

CENTAVO = Decimal('0.01')
MONTO_MAXIMO = Decimal('9999999.99')
POR_PAGINA = 25


# =============================================================================
# --- BASE DE DATOS ---
# =============================================================================

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db


def close_db(exception=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


# =============================================================================
# --- HORA LOCAL ---
# =============================================================================

def ahora_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hoy_local():
    return (ahora_utc() + DESFASE_LOCAL).date()


def a_datetime(valor):
    """'2026-09-26 17:59:33' (o datetime) -> datetime, o None si no se puede leer."""
    if valor is None or valor == '':
        return None
    if isinstance(valor, datetime):
        return valor
    try:
        return datetime.strptime(str(valor)[:19], FORMATO_BD)
    except ValueError:
        try:
            return datetime.strptime(str(valor)[:10], '%Y-%m-%d')
        except ValueError:
            return None


def a_local(valor):
    momento = a_datetime(valor)
    return momento + DESFASE_LOCAL if momento else None


def inicio_utc(dia):
    """Primer instante (UTC, como texto de la base) del día local ``dia``."""
    return (datetime.combine(dia, datetime.min.time()) - DESFASE_LOCAL).strftime(FORMATO_BD)


def fin_utc(dia):
    """Último instante (UTC, como texto de la base) del día local ``dia``."""
    return (datetime.combine(dia, datetime.max.time()).replace(microsecond=0) - DESFASE_LOCAL).strftime(FORMATO_BD)


def dia_local_sql(columna):
    """Expresión SQL: el día local de una columna de fecha-hora guardada en UTC."""
    return f"date({columna}, '{ZONA_HORARIA_HORAS:+d} hours')"


def fecha_o(texto, por_defecto):
    try:
        return datetime.strptime(texto, '%Y-%m-%d').date() if texto else por_defecto
    except (ValueError, TypeError):
        return por_defecto


def rango_fechas(defecto='hoy'):
    """Lee ?desde=&hasta= (YYYY-MM-DD). Sin parámetros: hoy, o el mes en curso con defecto='mes'."""
    hoy = hoy_local()
    inicio = hoy.replace(day=1) if defecto == 'mes' else hoy
    desde = fecha_o(request.args.get('desde', ''), inicio)
    hasta = fecha_o(request.args.get('hasta', ''), hoy)
    if desde > hasta:
        desde, hasta = hasta, desde
    return desde, hasta


def contexto_fechas(endpoint):
    hoy = hoy_local()
    return {
        'endpoint': endpoint, 'hoy_iso': hoy.isoformat(),
        'hasta_menos_semana': (hoy - timedelta(days=6)).isoformat(),
        'inicio_mes_iso': hoy.replace(day=1).isoformat(),
    }


# =============================================================================
# --- DINERO ---
# =============================================================================

def D(valor):
    """Cualquier valor numérico de la base -> Decimal con centavos."""
    try:
        return Decimal(str(valor if valor is not None else 0)).quantize(CENTAVO, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return Decimal('0.00')


def leer_monto(valor, permitir_cero=False):
    """Texto -> Decimal con 2 decimales, o None si no es un monto válido."""
    try:
        monto = Decimal(str(valor if valor is not None else '').strip())
    except InvalidOperation:
        return None
    if not monto.is_finite():
        return None
    monto = monto.quantize(CENTAVO, rounding=ROUND_HALF_UP)
    if monto < 0 or (monto == 0 and not permitir_cero) or monto > MONTO_MAXIMO:
        return None
    return monto


def f(monto):
    """Decimal -> float para guardar en las columnas REAL."""
    return float(D(monto))


def q(valor):
    monto = D(valor)
    return f'{"−" if monto < 0 else ""}Q{abs(monto):,.2f}'


# =============================================================================
# --- PERMISOS ---
# =============================================================================

def es_admin():
    return current_user.is_authenticated and current_user.rol == 'admin'


def admin_required(vista):
    @wraps(vista)
    def decorada(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('login'))
        if current_user.rol != 'admin':
            flash('Acceso denegado: se requieren permisos de administrador.', 'error')
            return redirect(url_for('dashboard'))
        return vista(*args, **kwargs)
    return decorada


# =============================================================================
# --- AUDITORÍA ---
# =============================================================================

def ip_cliente():
    # Detrás del túnel de Cloudflare la IP real llega en esta cabecera; solo se usa para el registro.
    return (request.headers.get('CF-Connecting-IP') or request.remote_addr or '')[:45]


def registrar_auditoria(accion, modulo, descripcion, db=None, id_usuario=None):
    """Deja constancia de una acción sensible. No hace commit: queda en la misma
    transacción que la acción (si esa falla, la auditoría tampoco queda huérfana).
    Nunca recibe contraseñas ni tokens."""
    db = db or get_db()
    if id_usuario is None and current_user and current_user.is_authenticated:
        id_usuario = current_user.id
    db.execute(
        "INSERT INTO auditoria (id_usuario, accion, modulo, descripcion, ip) VALUES (?, ?, ?, ?, ?)",
        (id_usuario, accion[:50], modulo[:50], str(descripcion)[:255], ip_cliente() if request else None))


# =============================================================================
# --- PAGINACIÓN Y ARCHIVOS ---
# =============================================================================

def info_pagina(total, por_pagina=POR_PAGINA):
    paginas = max(1, -(-total // por_pagina))
    pagina = min(max(request.args.get('pagina', 1, type=int) or 1, 1), paginas)
    return {'pagina': pagina, 'paginas': paginas, 'total': total, 'por_pagina': por_pagina,
            'offset': (pagina - 1) * por_pagina}


def paginar_consulta(db, sql, params=(), por_pagina=POR_PAGINA):
    """Ejecuta ``sql`` (sin LIMIT) y devuelve (filas_de_la_pagina, info_pagina)."""
    total = db.execute(f'SELECT COUNT(*) FROM ({sql})', params).fetchone()[0]
    pag = info_pagina(total, por_pagina)
    filas = db.execute(f'{sql} LIMIT ? OFFSET ?', (*params, por_pagina, pag['offset'])).fetchall()
    return filas, pag


def paginar_lista(elementos, por_pagina=POR_PAGINA):
    pag = info_pagina(len(elementos), por_pagina)
    return elementos[pag['offset']:pag['offset'] + por_pagina], pag


def respuesta_csv(nombre, encabezados, filas):
    """CSV en UTF-8 con BOM: se abre directo en Excel con tildes y ñ correctas."""
    buffer = io.StringIO()
    escritor = csv.writer(buffer)
    escritor.writerow(encabezados)
    for fila in filas:
        escritor.writerow(fila)
    return Response(
        '﻿' + buffer.getvalue(), mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{nombre}-{hoy_local().isoformat()}.csv"'})


EXTENSIONES_PERMITIDAS = {
    'pdf', 'jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'heic', 'heif',
    'doc', 'docx', 'xls', 'xlsx', 'csv', 'txt', 'odt', 'ods',
}
EXTENSIONES_IMAGEN = {'jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp'}


def extension_de(nombre):
    return nombre.rsplit('.', 1)[1].lower() if nombre and '.' in nombre else ''


def archivo_permitido(nombre):
    return extension_de(nombre) in EXTENSIONES_PERMITIDAS
