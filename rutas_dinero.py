"""Rutas del dinero: caja por turnos, fondo, cuentas por cobrar y por pagar, gastos,
proveedores y reportes. La lógica de saldos y movimientos está en finanzas.py."""
import os
import sqlite3
import uuid
from datetime import timedelta
from decimal import Decimal

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

import finanzas as fin
from comun import (CENTAVO, D, a_local, admin_required, ahora_utc, contexto_fechas, dia_local_sql,
                   extension_de, f, fecha_o, fin_utc, get_db, hoy_local, inicio_utc, leer_monto, paginar_consulta,
                   paginar_lista, q, rango_fechas, registrar_auditoria, respuesta_csv)
from digifact import es_consumidor_final, normalize_nit

bp = Blueprint('dinero', __name__)


def _ahora_texto():
    return ahora_utc().strftime('%Y-%m-%d %H:%M:%S')


def _guardar_comprobante(archivo):
    """Guarda el comprobante (PDF o imagen) y devuelve el nombre; (None, error) si no es válido."""
    if not archivo or not archivo.filename:
        return None, None
    if extension_de(archivo.filename) not in {'pdf', 'jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp'}:
        return None, 'El comprobante debe ser un PDF o una imagen.'
    nombre = f'{uuid.uuid4().hex}_{secure_filename(archivo.filename)}'
    archivo.save(os.path.join(current_app.config['EXPENSE_UPLOAD_FOLDER'], nombre))
    return nombre, None


def _fecha_opcional(texto):
    """'2026-10-01' -> la misma cadena si es una fecha válida; None si está vacía o no es fecha."""
    return texto if texto and fecha_o(texto, None) else None


# =============================================================================
# --- CAJA (TURNOS) ---
# =============================================================================

GRUPO_MEDIO = {'Efectivo': 'efectivo', 'Tarjeta': 'tarjeta', 'Transferencia': 'banco', 'Deposito': 'banco',
               'Cheque': 'banco', fin.CREDITO: 'credito'}


def movimientos_del_turno(db, turno):
    """Todo lo que pasó en el turno, sea efectivo o no. Devuelve (filas, por_medio).

    Cada fila: fecha, tipo, concepto, naturaleza, medio, monto, entrada, afecta_efectivo, anulada, usuario.
    Solo el efectivo mueve el total esperado de la caja. ``por_medio`` suma lo cobrado en el turno
    por medio de pago (las ventas canceladas no cuentan)."""
    desde = turno['fecha_apertura']
    hasta = turno['fecha_cierre'] or (ahora_utc() + timedelta(minutes=1)).strftime('%Y-%m-%d %H:%M:%S')
    filas = []

    for m in db.execute("""SELECT m.*, u.nombre AS usuario FROM movimientos_caja m LEFT JOIN usuarios u ON u.id = m.id_usuario
                           WHERE m.id_caja = ?""", (turno['id'],)):
        filas.append({'fecha': m['creado_en'], 'tipo': fin.CAJA_TIPO_ETIQUETA.get(m['tipo'], m['tipo']), 'concepto': m['concepto'],
                      'naturaleza': m['naturaleza'], 'medio': 'efectivo', 'monto': D(m['monto']),
                      'entrada': m['tipo'] in fin.CAJA_INGRESOS, 'afecta_efectivo': True, 'anulada': False,
                      'usuario': m['usuario'] or ''})

    por_medio = {'efectivo': Decimal('0'), 'tarjeta': Decimal('0'), 'banco': Decimal('0'), 'credito': Decimal('0')}
    # Ventas del turno: las de tarjeta, transferencia o crédito quedan registradas aunque no muevan el efectivo.
    for v in db.execute("""SELECT v.*, p.nombre, p.apellido, u.nombre AS usuario FROM ventas v
                           LEFT JOIN pacientes p ON p.id = v.id_paciente LEFT JOIN usuarios u ON u.id = v.id_usuario
                           WHERE v.id_caja = ?""", (turno['id'],)):
        anulada = v['estado'] == 'Cancelada'
        grupo = GRUPO_MEDIO.get(v['metodo_pago'], 'banco')
        if not anulada:
            por_medio[grupo] += D(v['monto_total'])
        if grupo == 'efectivo':
            continue  # ya aparece como movimiento de caja
        detalle = f'Venta #{v["id"]} — {v["nombre"] or ""} {v["apellido"] or ""}'.strip()
        if v['referencia_pago']:
            detalle += f' · Ref. {v["referencia_pago"]}'
        filas.append({'fecha': v['fecha_venta'], 'tipo': 'venta', 'concepto': detalle, 'naturaleza': None,
                      'medio': grupo, 'monto': D(v['monto_total']), 'entrada': True, 'afecta_efectivo': False,
                      'anulada': anulada, 'usuario': v['usuario'] or ''})

    # Abonos de cuentas por cobrar del turno.
    for a in db.execute("""SELECT a.*, p.nombre, p.apellido, u.nombre AS usuario FROM abonos a
                           LEFT JOIN cuentas_por_cobrar c ON c.id = a.id_cuenta LEFT JOIN pacientes p ON p.id = c.id_paciente
                           LEFT JOIN usuarios u ON u.id = a.id_usuario
                           WHERE a.fecha_abono >= ? AND a.fecha_abono <= ?""", (desde, hasta)):
        grupo = GRUPO_MEDIO.get(a['metodo_pago'], 'banco')
        por_medio[grupo] += D(a['monto'])
        if grupo == 'efectivo':
            continue
        detalle = 'Abono de cuenta por cobrar' + (f' · {a["nombre"]} {a["apellido"]}' if a['nombre'] else '')
        if a['referencia_pago']:
            detalle += f' · Ref. {a["referencia_pago"]}'
        filas.append({'fecha': a['fecha_abono'], 'tipo': 'cobro cxc', 'concepto': detalle, 'naturaleza': None, 'medio': grupo,
                      'monto': D(a['monto']), 'entrada': True, 'afecta_efectivo': False, 'anulada': False, 'usuario': a['usuario'] or ''})

    # Gastos, pagos a proveedores y devoluciones que salieron del Fondo y no de la caja.
    marcas = ','.join('?' * 3)
    for m in db.execute(f"""SELECT m.*, u.nombre AS usuario FROM movimientos_fondo m LEFT JOIN usuarios u ON u.id = m.id_usuario
                            WHERE m.tipo IN ({marcas}) AND m.creado_en >= ? AND m.creado_en <= ?""",
                        ('gasto', 'pago_cxp', 'anulacion_venta', desde, hasta)):
        filas.append({'fecha': m['creado_en'], 'tipo': fin.FONDO_TIPO_ETIQUETA.get(m['tipo'], m['tipo']).lower(), 'concepto': m['concepto'],
                      'naturaleza': None, 'medio': m['cuenta'], 'monto': D(m['monto']), 'entrada': False,
                      'afecta_efectivo': False, 'anulada': False, 'usuario': m['usuario'] or ''})

    filas.sort(key=lambda r: r['fecha'], reverse=True)
    # El monto inicial es parte del efectivo esperado: se ve como el primer renglón del turno.
    filas.append({'fecha': turno['fecha_apertura'], 'tipo': 'apertura', 'concepto': 'Monto inicial del turno', 'naturaleza': None,
                  'medio': 'efectivo', 'monto': D(turno['monto_inicial']), 'entrada': True, 'afecta_efectivo': True,
                  'anulada': False, 'apertura': True, 'usuario': ''})
    return filas, por_medio


@bp.route('/caja')
@login_required
def caja():
    db = get_db()
    turno = fin.turno_abierto(db)
    saldos = {'fondo': fin.saldo_cuenta(db, 'fondo'), 'por_consolidar': fin.saldo_cuenta(db, 'por_consolidar')}
    if turno is None:
        return render_template('caja/abrir.html', active_page='caja', saldos=saldos)
    movimientos, por_medio = movimientos_del_turno(db, turno)
    entradas, salidas = fin.desglose_efectivo(db, turno['id'])
    abrio = db.execute('SELECT nombre FROM usuarios WHERE id = ?', (turno['id_usuario_apertura'],)).fetchone()
    return render_template(
        'caja/index.html', active_page='caja', turno=turno, abrio=abrio['nombre'] if abrio else None, movimientos=movimientos,
        por_medio=por_medio, entradas=entradas, salidas=salidas, esperado=fin.esperado_caja(db, turno),
        n_movimientos=sum(1 for m in movimientos if not m.get('apertura')), saldos=saldos,
        naturalezas_ingreso=fin.NATURALEZAS_INGRESO, naturalezas_egreso=fin.NATURALEZAS_EGRESO)


@bp.route('/caja/abrir', methods=['POST'])
@login_required
def caja_abrir():
    db = get_db()
    if fin.turno_abierto(db) is not None:
        flash('Ya hay un turno de caja abierto.', 'error')
        return redirect(url_for('dinero.caja'))
    monto_inicial = leer_monto(request.form.get('monto_inicial') or '0', permitir_cero=True)
    if monto_inicial is None:
        flash('Ingresa un monto inicial válido.', 'error')
        return redirect(url_for('dinero.caja'))

    # El dinero con el que arranca la caja tiene que salir de algún lado: se descuenta del Fondo
    # (o de Por consolidar) y queda registrado.
    origen = request.form.get('origen_apertura', '')
    if monto_inicial > 0:
        if origen not in ('fondo', 'por_consolidar'):
            flash('Indica de dónde sale el dinero inicial: Fondo o Por consolidar.', 'error')
            return redirect(url_for('dinero.caja'))
        error = fin.validar_salida(db, origen, monto_inicial)
        if error:
            flash(error + ' Registra primero ese dinero en el Fondo (por ejemplo, como saldo inicial o aporte de capital).', 'error')
            return redirect(url_for('dinero.caja'))
    try:
        cur = db.execute("INSERT INTO cierres_caja (monto_inicial, estado, id_usuario_apertura) VALUES (?, 'Abierta', ?)",
                         (f(monto_inicial), current_user.id))
    except sqlite3.IntegrityError:
        db.rollback()
        flash('Ya hay un turno de caja abierto.', 'error')
        return redirect(url_for('dinero.caja'))
    id_caja = cur.lastrowid
    if monto_inicial > 0:
        fin.mov_fondo(db, origen, 'apertura_caja', monto_inicial, f'Apertura de caja #{id_caja}', current_user.id, f'apertura-caja-{id_caja}')
    registrar_auditoria('abrir_caja', 'caja', f'Abrió la caja #{id_caja} con {q(monto_inicial)}'
                        f'{" desde " + fin.ORIGENES_NOMBRE.get(fin.forma_a_origen(origen), origen) if monto_inicial > 0 else ""}.', db=db)
    db.commit()
    flash('Caja abierta. ¡A vender!', 'success')
    return redirect(url_for('venta_nueva'))


@bp.route('/caja/cerrar', methods=['POST'])
@login_required
def caja_cerrar():
    db = get_db()
    turno = fin.turno_abierto(db)
    if turno is None:
        flash('No hay un turno de caja abierto.', 'error')
        return redirect(url_for('dinero.caja'))
    esperado = fin.esperado_caja(db, turno)
    # Si se deja el monto contado vacío, se toma el total esperado (cierre rápido).
    if (request.form.get('monto_contado') or '').strip() == '':
        contado = esperado
    else:
        contado = leer_monto(request.form.get('monto_contado'), permitir_cero=True)
    if contado is None:
        flash('Ingresa el monto contado en caja.', 'error')
        return redirect(url_for('dinero.caja'))
    diferencia = contado - esperado
    notas = request.form.get('notas', '').strip()
    # Una diferencia (faltante o sobrante) debe quedar explicada.
    if diferencia != 0 and len(notas) < 5:
        tipo = 'sobrante' if diferencia > 0 else 'faltante'
        flash(f'Hay un {tipo} de {q(abs(diferencia))} entre lo esperado ({q(esperado)}) y lo contado. '
              'Explica el motivo en las notas para poder cerrar.', 'error')
        return redirect(url_for('dinero.caja'))

    efectivo, _ = fin.desglose_efectivo(db, turno['id'])
    otros = D(db.execute("""SELECT COALESCE(SUM(monto_total), 0) FROM ventas WHERE id_caja = ? AND estado <> 'Cancelada'
                            AND metodo_pago NOT IN ('Efectivo', ?)""", (turno['id'], fin.CREDITO)).fetchone()[0])
    db.execute("""UPDATE cierres_caja SET fecha_cierre = CURRENT_TIMESTAMP, total_ventas_efectivo = ?, total_ventas_otros = ?,
                  monto_final_calculado = ?, monto_final_real = ?, diferencia = ?, estado = 'Cerrada',
                  id_usuario_cierre = ?, notas = ? WHERE id = ?""",
               (f(efectivo), f(otros), f(esperado), f(contado), f(diferencia), current_user.id, notas or None, turno['id']))
    # El efectivo contado sale de la caja y entra al Fondo.
    if contado > 0:
        fin.mov_fondo(db, 'fondo', 'cierre_caja', contado, f'Cierre de caja #{turno["id"]}', current_user.id, f'cierre-{turno["id"]}')
    registrar_auditoria('cerrar_caja', 'caja', f'Cerró la caja #{turno["id"]}: esperado {q(esperado)}, contado {q(contado)}, diferencia {q(diferencia)}.', db=db)
    db.commit()
    flash('Caja cerrada. El efectivo contado pasó al Fondo.', 'success')
    return redirect(url_for('dinero.caja_historial'))


@bp.route('/caja/movimiento', methods=['POST'])
@login_required
def caja_movimiento():
    """Ingreso o egreso manual de efectivo en el turno abierto. Exige concepto y naturaleza
    financiera: el dinero que entra o sale sin ser una venta o un pago ya registrado debe quedar explicado."""
    db = get_db()
    turno = fin.turno_abierto(db)
    if turno is None:
        flash('Abre una caja antes de registrar movimientos.', 'error')
        return redirect(url_for('dinero.caja'))
    tipo = request.form.get('tipo', '')
    monto = leer_monto(request.form.get('monto'))
    concepto = request.form.get('concepto', '').strip()
    naturaleza = request.form.get('naturaleza', '')
    catalogo = fin.NATURALEZAS_INGRESO if tipo == 'ingreso' else fin.NATURALEZAS_EGRESO
    if tipo not in ('ingreso', 'egreso') or monto is None:
        flash('Indica el tipo de movimiento y un monto válido.', 'error')
    elif len(concepto) < 3:
        flash('El concepto es obligatorio: explica por qué entra o sale ese dinero.', 'error')
    elif naturaleza not in catalogo or (tipo == 'ingreso' and naturaleza == 'saldo_inicial'):
        flash('Selecciona la naturaleza financiera del movimiento.', 'error')
    else:
        if tipo == 'egreso':
            error = fin.validar_salida(db, 'caja', monto)
            if error:
                flash(error, 'error')
                return redirect(url_for('dinero.caja'))
        fin.mov_caja(db, turno['id'], tipo, monto, concepto, current_user.id, naturaleza=naturaleza)
        registrar_auditoria(f'{tipo}_manual_caja', 'caja', f'Registró un {tipo} manual de {q(monto)} en caja ({catalogo[naturaleza]}): {concepto}.', db=db)
        db.commit()
        flash('Movimiento registrado.', 'success')
    return redirect(url_for('dinero.caja'))


@bp.route('/caja/historial')
@login_required
def caja_historial():
    db = get_db()
    filas, pag = paginar_consulta(db, """
        SELECT c.*, ua.nombre AS abrio, uc.nombre AS cerro,
               (SELECT COUNT(*) FROM ventas v WHERE v.id_caja = c.id AND v.estado <> 'Cancelada') AS n_ventas
        FROM cierres_caja c LEFT JOIN usuarios ua ON ua.id = c.id_usuario_apertura LEFT JOIN usuarios uc ON uc.id = c.id_usuario_cierre
        ORDER BY c.id DESC""")
    return render_template('caja/historial.html', active_page='caja', turnos=filas, pag=pag)


@bp.route('/caja/<int:id_caja>')
@login_required
def caja_turno(id_caja):
    db = get_db()
    turno = db.execute("""SELECT c.*, ua.nombre AS abrio, uc.nombre AS cerro FROM cierres_caja c
                          LEFT JOIN usuarios ua ON ua.id = c.id_usuario_apertura LEFT JOIN usuarios uc ON uc.id = c.id_usuario_cierre
                          WHERE c.id = ?""", (id_caja,)).fetchone()
    if turno is None:
        abort(404)
    movimientos, por_medio = movimientos_del_turno(db, turno)
    ventas = db.execute("""SELECT v.id, v.fecha_venta, v.monto_total, v.metodo_pago, v.estado, p.nombre, p.apellido FROM ventas v
                           LEFT JOIN pacientes p ON p.id = v.id_paciente WHERE v.id_caja = ? ORDER BY v.fecha_venta""", (id_caja,)).fetchall()
    tiene_libro = db.execute("SELECT 1 FROM movimientos_caja WHERE id_caja = ? LIMIT 1", (id_caja,)).fetchone() is not None
    return render_template('caja/turno.html', active_page='caja', turno=turno, movimientos=movimientos, por_medio=por_medio,
                           ventas=ventas, tiene_libro=tiene_libro, naturalezas_ingreso=fin.NATURALEZAS_INGRESO,
                           naturalezas_egreso=fin.NATURALEZAS_EGRESO)


# =============================================================================
# --- FONDO (LIBRO DE TESORERÍA) ---
# =============================================================================

@bp.route('/fondo')
@login_required
@admin_required
def fondo():
    db = get_db()
    cuenta = request.args.get('cuenta', 'fondo')
    if cuenta not in fin.CUENTAS_FONDO:
        cuenta = 'fondo'
    hoy = hoy_local()
    desde = fecha_o(request.args.get('desde', ''), hoy.replace(day=1))
    hasta = fecha_o(request.args.get('hasta', ''), hoy)
    if desde > hasta:
        desde, hasta = hasta, desde
    ini, fin_dt = inicio_utc(desde), fin_utc(hasta)

    marcas = ','.join('?' * len(fin.FONDO_ENTRADAS))
    corriente = D(db.execute(f"SELECT COALESCE(SUM(CASE WHEN tipo IN ({marcas}) THEN monto ELSE -monto END), 0) FROM movimientos_fondo WHERE cuenta = ? AND creado_en < ?",
                             (*fin.FONDO_ENTRADAS, cuenta, ini)).fetchone()[0])
    filas, entradas, salidas = [], Decimal('0'), Decimal('0')
    for m in db.execute("""SELECT m.*, u.nombre AS usuario FROM movimientos_fondo m LEFT JOIN usuarios u ON u.id = m.id_usuario
                           WHERE m.cuenta = ? AND m.creado_en BETWEEN ? AND ? ORDER BY m.creado_en, m.id""", (cuenta, ini, fin_dt)):
        es_entrada = m['tipo'] in fin.FONDO_ENTRADAS
        monto = D(m['monto'])
        corriente += monto if es_entrada else -monto
        entradas += monto if es_entrada else 0
        salidas += 0 if es_entrada else monto
        filas.append({'m': m, 'es_entrada': es_entrada, 'monto': monto, 'saldo': corriente})
    saldo_actual = fin.saldo_cuenta(db, cuenta)
    filas.reverse()
    pagina, pag = paginar_lista(filas)
    sin_movimientos = db.execute("SELECT 1 FROM movimientos_fondo LIMIT 1").fetchone() is None
    return render_template(
        'fondo/index.html', active_page='fondo', cuenta=cuenta, cuentas=fin.CUENTAS_FONDO, filas=pagina, pag=pag, desde=desde, hasta=hasta,
        entradas=entradas, salidas=salidas, resumen=fin.resumen_dinero(db), conciliacion=fin.conciliar_dinero(db),
        etiquetas=fin.FONDO_TIPO_ETIQUETA, naturalezas_ingreso=fin.NATURALEZAS_INGRESO, naturalezas_egreso=fin.NATURALEZAS_EGRESO,
        saldo_cuenta_actual=saldo_actual, sin_movimientos=sin_movimientos)


@bp.route('/fondo/movimiento', methods=['POST'])
@login_required
@admin_required
def fondo_movimiento():
    """Ingreso o egreso manual en el Fondo. Exige concepto y naturaleza financiera."""
    db = get_db()
    cuenta = request.form.get('cuenta', '')
    tipo = request.form.get('tipo', '')
    monto = leer_monto(request.form.get('monto'))
    concepto = request.form.get('concepto', '').strip()
    naturaleza = request.form.get('naturaleza', '')
    catalogo = fin.NATURALEZAS_INGRESO if tipo == 'ingreso' else fin.NATURALEZAS_EGRESO
    destino = redirect(url_for('dinero.fondo', cuenta=cuenta if cuenta in fin.CUENTAS_FONDO else 'fondo'))
    if cuenta not in fin.CUENTAS_FONDO or tipo not in ('ingreso', 'egreso') or monto is None:
        flash('Indica la cuenta, el tipo de movimiento y un monto válido.', 'error')
    elif len(concepto) < 3:
        flash('El concepto es obligatorio: explica por qué entra o sale ese dinero.', 'error')
    elif naturaleza not in catalogo:
        flash('Selecciona la naturaleza financiera del movimiento.', 'error')
    else:
        if tipo == 'egreso':
            error = fin.validar_salida(db, cuenta, monto)
            if error:
                flash(error, 'error')
                return destino
        fin.mov_fondo(db, cuenta, 'ingreso_manual' if tipo == 'ingreso' else 'egreso_manual', monto, concepto, current_user.id, naturaleza=naturaleza)
        registrar_auditoria(f'{tipo}_manual_fondo', 'fondo', f'Registró un {tipo} manual de {q(monto)} en {fin.CUENTAS_FONDO[cuenta]} ({catalogo[naturaleza]}): {concepto}.', db=db)
        db.commit()
        flash('Movimiento registrado en el libro.', 'success')
    return destino


@bp.route('/fondo/consolidar', methods=['POST'])
@login_required
@admin_required
def fondo_consolidar():
    """Pasa de 'Por consolidar' al Fondo las ventas con tarjeta que el banco ya liquidó."""
    db = get_db()
    monto = leer_monto(request.form.get('monto'))
    notas = request.form.get('notas', '').strip()
    destino = redirect(url_for('dinero.fondo', cuenta='por_consolidar'))
    if monto is None:
        flash('Ingresa un monto válido a consolidar.', 'error')
        return destino
    error = fin.validar_salida(db, 'por_consolidar', monto)
    if error:
        flash(error, 'error')
        return destino
    referencia = f'consolidacion-{uuid.uuid4().hex[:16]}'
    concepto = 'Consolidación de tarjeta' + (f': {notas}' if notas else '')
    fin.mov_fondo(db, 'por_consolidar', 'consolidacion_enviada', monto, concepto, current_user.id, referencia)
    fin.mov_fondo(db, 'fondo', 'consolidacion_recibida', monto, concepto, current_user.id, referencia)
    registrar_auditoria('consolidar', 'fondo', f'Consolidó {q(monto)} de Por consolidar al Fondo.', db=db)
    db.commit()
    flash(f'{q(monto)} pasaron de Por consolidar al Fondo.', 'success')
    return destino


# =============================================================================
# --- CUENTAS POR COBRAR ---
# =============================================================================

@bp.route('/cuentas-por-cobrar')
@login_required
def cxc_lista():
    db = get_db()
    estado = request.args.get('estado', '')
    texto = request.args.get('q', '').strip()
    condiciones, params = [], []
    if estado in (fin.PENDIENTE, fin.PARCIAL, fin.PAGADA, fin.ANULADA):
        condiciones.append('c.estado = ?')
        params.append(estado)
    elif estado == 'abiertas':
        condiciones.append("c.estado IN ('Pendiente','Parcial')")
    else:
        condiciones.append("c.estado <> 'Anulada'")
        estado = ''
    if texto:
        condiciones.append("(p.nombre LIKE ? OR p.apellido LIKE ? OR (p.nombre || ' ' || p.apellido) LIKE ?)")
        params += [f'%{texto}%'] * 3
    donde = ' AND '.join(condiciones)
    filas, pag = paginar_consulta(db, f"""SELECT c.*, p.nombre, p.apellido, p.telefono FROM cuentas_por_cobrar c
                                          JOIN pacientes p ON p.id = c.id_paciente WHERE {donde}
                                          ORDER BY CASE WHEN c.estado IN ('Pendiente','Parcial') THEN 0 ELSE 1 END, c.creado_en DESC""", params)
    total = D(db.execute(f"""SELECT COALESCE(SUM(c.saldo_pendiente), 0) FROM cuentas_por_cobrar c JOIN pacientes p ON p.id = c.id_paciente
                             WHERE {donde} AND c.estado IN ('Pendiente','Parcial')""", params).fetchone()[0])
    hoy = hoy_local().isoformat()
    return render_template('cxc/lista.html', active_page='cxc', cuentas=filas, pag=pag, estado=estado, texto=texto,
                           total_por_cobrar=total, hoy=hoy)


@bp.route('/cuentas-por-cobrar/nueva', methods=['GET', 'POST'])
@login_required
@admin_required
def cxc_nueva():
    db = get_db()
    if request.method == 'POST':
        paciente = db.execute('SELECT id, nombre, apellido FROM pacientes WHERE id = ? AND is_deleted = 0', (request.form.get('id_paciente', type=int),)).fetchone()
        monto = leer_monto(request.form.get('monto'))
        if paciente is None or monto is None:
            flash('Selecciona un paciente y un monto válido.', 'error')
        else:
            cur = db.execute("INSERT INTO cuentas_por_cobrar (id_paciente, monto, saldo_pendiente, fecha_vencimiento, notas, id_usuario) VALUES (?,?,?,?,?,?)",
                             (paciente['id'], f(monto), f(monto), _fecha_opcional(request.form.get('fecha_vencimiento')),
                              request.form.get('notas', '').strip() or None, current_user.id))
            registrar_auditoria('crear_cxc', 'cuentas_por_cobrar', f'Registró una cuenta por cobrar de {q(monto)} a {paciente["nombre"]} {paciente["apellido"]}.', db=db)
            db.commit()
            flash('Cuenta por cobrar registrada.', 'success')
            return redirect(url_for('dinero.cxc_detalle', id_cuenta=cur.lastrowid))
    return render_template('cxc/form.html', active_page='cxc')


def _cuenta_cobrar(db, id_cuenta):
    cuenta = db.execute("""SELECT c.*, p.nombre, p.apellido, p.telefono FROM cuentas_por_cobrar c
                           JOIN pacientes p ON p.id = c.id_paciente WHERE c.id = ?""", (id_cuenta,)).fetchone()
    if cuenta is None:
        abort(404)
    return cuenta


@bp.route('/cuentas-por-cobrar/<int:id_cuenta>')
@login_required
def cxc_detalle(id_cuenta):
    db = get_db()
    cuenta = _cuenta_cobrar(db, id_cuenta)
    abonos = db.execute("SELECT a.*, u.nombre AS usuario FROM abonos a LEFT JOIN usuarios u ON u.id = a.id_usuario WHERE a.id_cuenta = ? ORDER BY a.fecha_abono DESC, a.id DESC", (id_cuenta,)).fetchall()
    return render_template('cxc/detalle.html', active_page='cxc', cuenta=cuenta, abonos=abonos, metodos=fin.METODOS_COBRO,
                           caja_abierta=fin.turno_abierto(db) is not None, hoy=hoy_local().isoformat())


@bp.route('/cuentas-por-cobrar/<int:id_cuenta>/editar', methods=['GET', 'POST'])
@login_required
@admin_required
def cxc_editar(id_cuenta):
    db = get_db()
    cuenta = _cuenta_cobrar(db, id_cuenta)
    if cuenta['estado'] in (fin.PAGADA, fin.ANULADA):
        flash('Esta cuenta ya está cerrada y no se puede editar.', 'error')
        return redirect(url_for('dinero.cxc_detalle', id_cuenta=id_cuenta))
    if request.method == 'POST':
        vence = request.form.get('fecha_vencimiento', '')
        if vence and not fecha_o(vence, None):
            flash('Fecha de vencimiento inválida.', 'error')
        else:
            db.execute("UPDATE cuentas_por_cobrar SET fecha_vencimiento = ?, notas = ? WHERE id = ?",
                       (vence or None, request.form.get('notas', '').strip() or None, id_cuenta))
            registrar_auditoria('editar_cxc', 'cuentas_por_cobrar', f'Editó la cuenta por cobrar #{id_cuenta}.', db=db)
            db.commit()
            flash('Cuenta por cobrar actualizada.', 'success')
            return redirect(url_for('dinero.cxc_detalle', id_cuenta=id_cuenta))
    return render_template('cxc/editar.html', active_page='cxc', cuenta=cuenta)


@bp.route('/cuentas-por-cobrar/<int:id_cuenta>/pagar', methods=['POST'])
@login_required
def cxc_pagar(id_cuenta):
    db = get_db()
    cuenta = _cuenta_cobrar(db, id_cuenta)
    volver = redirect(url_for('dinero.cxc_detalle', id_cuenta=id_cuenta))
    if cuenta['estado'] in (fin.PAGADA, fin.ANULADA):
        flash('Esta cuenta ya está cerrada.', 'error')
        return volver
    metodo = request.form.get('metodo_pago', '')
    monto = leer_monto(request.form.get('monto'))
    referencia = request.form.get('referencia_pago', '').strip()[:100]
    saldo = D(cuenta['saldo_pendiente'])
    if metodo not in fin.METODOS_COBRO or monto is None:
        flash('Monto y método de cobro son obligatorios.', 'error')
        return volver
    if monto > saldo:
        flash('El abono no puede ser mayor al saldo pendiente.', 'error')
        return volver
    if metodo in fin.METODOS_CON_REFERENCIA and not referencia:
        flash('La referencia (número de autorización o boleta) es obligatoria para este método de pago.', 'error')
        return volver
    destino = fin.destino_de_cobro(metodo)
    if destino == 'caja' and fin.turno_abierto(db) is None:
        flash('Debes abrir caja antes de registrar un cobro en efectivo.', 'error')
        return volver

    nuevo_saldo = saldo - monto
    abono = db.execute("""INSERT INTO abonos (id_cuenta, id_venta, monto, metodo_pago, referencia_pago, saldo_actual_snapshot, notas, id_usuario)
                          VALUES (?,?,?,?,?,?,?,?)""",
                       (id_cuenta, cuenta['id_venta'], f(monto), metodo, referencia, f(nuevo_saldo),
                        request.form.get('notas', '').strip() or None, current_user.id))
    db.execute("UPDATE cuentas_por_cobrar SET saldo_pendiente = ?, estado = ? WHERE id = ?", (f(nuevo_saldo), fin.estado_tras_pago(nuevo_saldo), id_cuenta))
    if cuenta['id_venta']:
        fin.sincronizar_venta_con_cuenta(db, cuenta['id_venta'])
    fin.registrar_entrada(db, destino, monto, f'Cobro CxC #{id_cuenta} — {cuenta["nombre"]} {cuenta["apellido"]}', current_user.id,
                          tipo_caja='cobro_cxc', tipo_fondo='cobro_cxc', referencia=f'cobro-cxc-{abono.lastrowid}')
    registrar_auditoria('cobrar_cxc', 'cuentas_por_cobrar', f'Registró un abono de {q(monto)} ({metodo}) a la cuenta por cobrar #{id_cuenta}.', db=db)
    db.commit()
    flash('Abono registrado.', 'success')
    return volver


@bp.route('/cuentas-por-cobrar/<int:id_cuenta>/anular', methods=['POST'])
@login_required
@admin_required
def cxc_anular(id_cuenta):
    db = get_db()
    cuenta = _cuenta_cobrar(db, id_cuenta)
    volver = redirect(url_for('dinero.cxc_detalle', id_cuenta=id_cuenta))
    motivo = request.form.get('motivo', '').strip()
    hay_abonos = db.execute("SELECT 1 FROM abonos WHERE id_cuenta = ? LIMIT 1", (id_cuenta,)).fetchone()
    if cuenta['estado'] == fin.ANULADA:
        flash('Esta cuenta ya está anulada.', 'error')
    elif hay_abonos:
        flash('Esta cuenta ya tiene abonos cobrados: no se puede anular sin devolver ese dinero.', 'error')
    elif cuenta['id_venta']:
        flash('Esta cuenta nace de una venta a crédito: cancela la venta para anularla.', 'error')
    elif len(motivo) < 5:
        flash('Explica el motivo de la anulación (mínimo 5 caracteres).', 'error')
    else:
        db.execute("UPDATE cuentas_por_cobrar SET estado = ?, saldo_pendiente = 0, motivo_anulacion = ? WHERE id = ?", (fin.ANULADA, motivo[:255], id_cuenta))
        registrar_auditoria('anular_cxc', 'cuentas_por_cobrar', f'Anuló la cuenta por cobrar #{id_cuenta}. Motivo: {motivo}.', db=db)
        db.commit()
        flash('Cuenta por cobrar anulada.', 'success')
        return redirect(url_for('dinero.cxc_lista'))
    return volver


# =============================================================================
# --- PROVEEDORES ---
# =============================================================================

@bp.route('/proveedores')
@login_required
@admin_required
def proveedores_lista():
    db = get_db()
    ver_inactivos = request.args.get('inactivos') == '1'
    proveedores = db.execute(f"""SELECT p.*, COALESCE((SELECT SUM(saldo_pendiente) FROM cuentas_por_pagar c
                                                       WHERE c.id_proveedor = p.id AND c.estado IN ('Pendiente','Parcial')), 0) AS debe
                                 FROM proveedores p WHERE p.activo = ? ORDER BY p.nombre""", (0 if ver_inactivos else 1,)).fetchall()
    return render_template('proveedores/lista.html', active_page='proveedores', proveedores=proveedores, ver_inactivos=ver_inactivos)


def _leer_proveedor(form):
    nombre = form.get('nombre', '').strip()
    nit_raw = form.get('nit', '')
    nit = None if es_consumidor_final(nit_raw) else normalize_nit(nit_raw)
    if not nombre:
        return None, 'El nombre del proveedor es obligatorio.'
    if nit_raw.strip() and not es_consumidor_final(nit_raw) and (not nit or len(nit) > 12):
        return None, 'El NIT no es válido.'
    return {'nombre': nombre[:150], 'nit': nit or None, 'telefono': form.get('telefono', '').strip()[:30] or None}, None


@bp.route('/proveedores/nuevo', methods=['GET', 'POST'])
@login_required
@admin_required
def proveedor_nuevo():
    db = get_db()
    if request.method == 'POST':
        datos, error = _leer_proveedor(request.form)
        if error:
            flash(error, 'error')
        else:
            try:
                db.execute("INSERT INTO proveedores (nombre, nit, telefono) VALUES (?,?,?)", (datos['nombre'], datos['nit'], datos['telefono']))
                registrar_auditoria('crear_proveedor', 'proveedores', f'Creó el proveedor "{datos["nombre"]}".', db=db)
                db.commit()
                flash('Proveedor creado.', 'success')
                return redirect(url_for('dinero.proveedores_lista'))
            except sqlite3.IntegrityError:
                db.rollback()
                flash('Ya existe un proveedor con ese nombre.', 'error')
    return render_template('proveedores/form.html', active_page='proveedores', proveedor=None, form=request.form)


@bp.route('/proveedores/<int:id_proveedor>/editar', methods=['GET', 'POST'])
@login_required
@admin_required
def proveedor_editar(id_proveedor):
    db = get_db()
    proveedor = db.execute("SELECT * FROM proveedores WHERE id = ?", (id_proveedor,)).fetchone()
    if proveedor is None:
        abort(404)
    if request.method == 'POST':
        datos, error = _leer_proveedor(request.form)
        if error:
            flash(error, 'error')
        else:
            try:
                db.execute("UPDATE proveedores SET nombre = ?, nit = ?, telefono = ? WHERE id = ?", (datos['nombre'], datos['nit'], datos['telefono'], id_proveedor))
                registrar_auditoria('editar_proveedor', 'proveedores', f'Editó el proveedor "{datos["nombre"]}".', db=db)
                db.commit()
                flash('Proveedor actualizado.', 'success')
                return redirect(url_for('dinero.proveedores_lista'))
            except sqlite3.IntegrityError:
                db.rollback()
                flash('Ya existe otro proveedor con ese nombre.', 'error')
    return render_template('proveedores/form.html', active_page='proveedores', proveedor=proveedor, form=request.form)


@bp.route('/proveedores/<int:id_proveedor>/activar', methods=['POST'])
@login_required
@admin_required
def proveedor_alternar(id_proveedor):
    db = get_db()
    proveedor = db.execute("SELECT * FROM proveedores WHERE id = ?", (id_proveedor,)).fetchone()
    if proveedor is None:
        abort(404)
    if proveedor['activo']:
        debe = db.execute("SELECT COUNT(*) FROM cuentas_por_pagar WHERE id_proveedor = ? AND estado IN ('Pendiente','Parcial')", (id_proveedor,)).fetchone()[0]
        if debe:
            flash('Este proveedor tiene cuentas por pagar pendientes: págalas o anúlalas antes de desactivarlo.', 'error')
            return redirect(url_for('dinero.proveedores_lista'))
    nuevo = 0 if proveedor['activo'] else 1
    db.execute("UPDATE proveedores SET activo = ? WHERE id = ?", (nuevo, id_proveedor))
    registrar_auditoria('activar_proveedor' if nuevo else 'desactivar_proveedor', 'proveedores', f'{"Activó" if nuevo else "Desactivó"} al proveedor "{proveedor["nombre"]}".', db=db)
    db.commit()
    flash('Proveedor activado.' if nuevo else 'Proveedor desactivado; su historial se conserva.', 'success')
    return redirect(url_for('dinero.proveedores_lista'))


# =============================================================================
# --- CUENTAS POR PAGAR (facturas de proveedores) ---
# =============================================================================

def _proveedores_activos(db):
    return db.execute("SELECT id, nombre FROM proveedores WHERE activo = 1 ORDER BY nombre").fetchall()


@bp.route('/cuentas-por-pagar')
@login_required
@admin_required
def cxp_lista():
    db = get_db()
    estado = request.args.get('estado', 'abiertas')
    texto = request.args.get('q', '').strip()
    proximos = request.args.get('proximos') == '1'
    condiciones, params = [], []
    if estado == 'abiertas':
        condiciones.append("c.estado IN ('Pendiente','Parcial')")
    elif estado in (fin.PENDIENTE, fin.PARCIAL, fin.PAGADA, fin.ANULADA):
        condiciones.append('c.estado = ?')
        params.append(estado)
    elif estado == 'todas':
        condiciones.append("c.estado <> 'Anulada'")
    else:
        estado = 'abiertas'
        condiciones.append("c.estado IN ('Pendiente','Parcial')")
    if texto:
        condiciones.append('(c.descripcion LIKE ? OR pr.nombre LIKE ?)')
        params += [f'%{texto}%'] * 2
    if proximos:
        hoy = hoy_local()
        condiciones.append("c.estado IN ('Pendiente','Parcial') AND c.fecha_vencimiento BETWEEN ? AND ?")
        params += [hoy.isoformat(), (hoy + timedelta(days=15)).isoformat()]
    donde = ' AND '.join(condiciones)
    filas, pag = paginar_consulta(db, f"""SELECT c.*, pr.nombre AS proveedor FROM cuentas_por_pagar c LEFT JOIN proveedores pr ON pr.id = c.id_proveedor
                                          WHERE {donde} ORDER BY CASE WHEN c.estado IN ('Pendiente','Parcial') THEN 0 ELSE 1 END,
                                          c.fecha_vencimiento IS NULL, c.fecha_vencimiento, c.id DESC""", params)
    total = D(db.execute(f"""SELECT COALESCE(SUM(c.saldo_pendiente), 0) FROM cuentas_por_pagar c LEFT JOIN proveedores pr ON pr.id = c.id_proveedor
                             WHERE {donde} AND c.estado IN ('Pendiente','Parcial')""", params).fetchone()[0])
    return render_template('cxp/lista.html', active_page='cxp', cuentas=filas, pag=pag, estado=estado, texto=texto, proximos=proximos,
                           total_por_pagar=total, hoy=hoy_local().isoformat())


@bp.route('/cuentas-por-pagar/nueva', methods=['GET', 'POST'])
@login_required
@admin_required
def cxp_nueva():
    db = get_db()
    if request.method == 'POST':
        proveedor = db.execute("SELECT id, nombre FROM proveedores WHERE id = ? AND activo = 1", (request.form.get('id_proveedor', type=int),)).fetchone()
        descripcion = request.form.get('descripcion', '').strip()
        monto = leer_monto(request.form.get('monto'))
        emision, vence = request.form.get('fecha_emision', ''), request.form.get('fecha_vencimiento', '')
        comprobante, error_archivo = _guardar_comprobante(request.files.get('comprobante'))
        if proveedor is None or monto is None or len(descripcion) < 3:
            flash('Selecciona un proveedor, escribe la descripción y un monto válido.', 'error')
        elif (emision and not fecha_o(emision, None)) or (vence and not fecha_o(vence, None)):
            flash('Alguna de las fechas no es válida.', 'error')
        elif error_archivo:
            flash(error_archivo, 'error')
        else:
            cur = db.execute("""INSERT INTO cuentas_por_pagar (id_proveedor, descripcion, monto, saldo_pendiente, fecha_emision, fecha_vencimiento,
                                comprobante_ruta, id_usuario) VALUES (?,?,?,?,?,?,?,?)""",
                             (proveedor['id'], descripcion[:300], f(monto), f(monto), emision or None, vence or None, comprobante, current_user.id))
            registrar_auditoria('crear_cxp', 'cuentas_por_pagar', f'Registró una cuenta por pagar de {q(monto)} a "{proveedor["nombre"]}".', db=db)
            db.commit()
            flash('Cuenta por pagar registrada.', 'success')
            return redirect(url_for('dinero.cxp_detalle', id_cuenta=cur.lastrowid))
    return render_template('cxp/form.html', active_page='cxp', proveedores=_proveedores_activos(db), form=request.form, hoy=hoy_local().isoformat())


def _cuenta_pagar(db, id_cuenta):
    cuenta = db.execute("""SELECT c.*, pr.nombre AS proveedor FROM cuentas_por_pagar c LEFT JOIN proveedores pr ON pr.id = c.id_proveedor
                           WHERE c.id = ?""", (id_cuenta,)).fetchone()
    if cuenta is None:
        abort(404)
    return cuenta


@bp.route('/cuentas-por-pagar/<int:id_cuenta>')
@login_required
@admin_required
def cxp_detalle(id_cuenta):
    db = get_db()
    cuenta = _cuenta_pagar(db, id_cuenta)
    pagos = db.execute("SELECT p.*, u.nombre AS usuario FROM pagos_cxp p LEFT JOIN usuarios u ON u.id = p.id_usuario WHERE p.id_cuenta = ? ORDER BY p.fecha_pago DESC, p.id DESC", (id_cuenta,)).fetchall()
    saldos = {'efectivo': fin.saldo_origen(db, 'caja'), 'fondo': fin.saldo_cuenta(db, 'fondo'), 'por_consolidar': fin.saldo_cuenta(db, 'por_consolidar')}
    return render_template('cxp/detalle.html', active_page='cxp', cuenta=cuenta, pagos=pagos, formas=fin.ORIGENES_NOMBRE, saldos=saldos,
                           caja_abierta=fin.turno_abierto(db) is not None, hoy=hoy_local().isoformat())


@bp.route('/cuentas-por-pagar/<int:id_cuenta>/editar', methods=['GET', 'POST'])
@login_required
@admin_required
def cxp_editar(id_cuenta):
    db = get_db()
    cuenta = _cuenta_pagar(db, id_cuenta)
    if cuenta['estado'] in (fin.PAGADA, fin.ANULADA):
        flash('Esta cuenta ya está cerrada y no se puede editar.', 'error')
        return redirect(url_for('dinero.cxp_detalle', id_cuenta=id_cuenta))
    if request.method == 'POST':
        vence = request.form.get('fecha_vencimiento', '')
        descripcion = request.form.get('descripcion', '').strip()
        if (vence and not fecha_o(vence, None)) or len(descripcion) < 3:
            flash('Revisa la descripción y la fecha de vencimiento.', 'error')
        else:
            db.execute("UPDATE cuentas_por_pagar SET fecha_vencimiento = ?, descripcion = ? WHERE id = ?", (vence or None, descripcion[:300], id_cuenta))
            registrar_auditoria('editar_cxp', 'cuentas_por_pagar', f'Editó la cuenta por pagar #{id_cuenta}.', db=db)
            db.commit()
            flash('Cuenta por pagar actualizada.', 'success')
            return redirect(url_for('dinero.cxp_detalle', id_cuenta=id_cuenta))
    return render_template('cxp/editar.html', active_page='cxp', cuenta=cuenta)


@bp.route('/cuentas-por-pagar/<int:id_cuenta>/pagar', methods=['POST'])
@login_required
@admin_required
def cxp_pagar(id_cuenta):
    db = get_db()
    cuenta = _cuenta_pagar(db, id_cuenta)
    volver = redirect(url_for('dinero.cxp_detalle', id_cuenta=id_cuenta))
    if cuenta['estado'] in (fin.PAGADA, fin.ANULADA):
        flash('Esta cuenta ya está cerrada.', 'error')
        return volver
    forma = request.form.get('forma_pago', '')
    monto = leer_monto(request.form.get('monto'))
    saldo = D(cuenta['saldo_pendiente'])
    comprobante, error_archivo = _guardar_comprobante(request.files.get('comprobante'))
    if forma not in fin.ORIGENES_NOMBRE or monto is None:
        flash('Monto y origen del pago son obligatorios.', 'error')
        return volver
    if monto > saldo:
        flash('El pago no puede ser mayor al saldo pendiente.', 'error')
        return volver
    if error_archivo:
        flash(error_archivo, 'error')
        return volver
    # El pago sale de la caja, del fondo o de por consolidar, con saldo suficiente.
    origen = fin.forma_a_origen(forma)
    error = fin.validar_salida(db, origen, monto)
    if error:
        flash(error, 'error')
        return volver

    nuevo_saldo = saldo - monto
    pago = db.execute("""INSERT INTO pagos_cxp (id_cuenta, monto, metodo_pago, referencia_pago, saldo_actual_snapshot, notas, id_usuario)
                         VALUES (?,?,?,?,?,?,?)""",
                      (id_cuenta, f(monto), fin.ORIGENES_NOMBRE[forma], request.form.get('referencia_pago', '').strip()[:100],
                       f(nuevo_saldo), request.form.get('notas', '').strip() or None, current_user.id))
    estado = fin.estado_tras_pago(nuevo_saldo)
    db.execute("""UPDATE cuentas_por_pagar SET saldo_pendiente = ?, estado = ?, fecha_pago = CASE WHEN ? = 'Pagada' THEN ? ELSE fecha_pago END,
                  comprobante_ruta = COALESCE(?, comprobante_ruta) WHERE id = ?""",
               (f(nuevo_saldo), estado, estado, hoy_local().isoformat(), comprobante, id_cuenta))
    fin.registrar_salida(db, origen, monto, f'Pago CxP #{id_cuenta} — {cuenta["proveedor"] or "Sin proveedor"}', current_user.id,
                         tipo_caja='pago_cxp', tipo_fondo='pago_cxp', referencia=f'pago-cxp-{pago.lastrowid}')
    registrar_auditoria('pagar_cxp', 'cuentas_por_pagar', f'Registró un pago de {q(monto)} (desde {fin.ORIGENES_ETIQUETA[origen]}) a la cuenta por pagar #{id_cuenta}.', db=db)
    db.commit()
    flash('Pago registrado.', 'success')
    return volver


@bp.route('/cuentas-por-pagar/<int:id_cuenta>/anular', methods=['POST'])
@login_required
@admin_required
def cxp_anular(id_cuenta):
    db = get_db()
    cuenta = _cuenta_pagar(db, id_cuenta)
    volver = redirect(url_for('dinero.cxp_detalle', id_cuenta=id_cuenta))
    motivo = request.form.get('motivo', '').strip()
    hay_pagos = db.execute("SELECT 1 FROM pagos_cxp WHERE id_cuenta = ? LIMIT 1", (id_cuenta,)).fetchone()
    if cuenta['estado'] == fin.ANULADA:
        flash('Esta cuenta ya está anulada.', 'error')
    elif hay_pagos or cuenta['estado'] == fin.PAGADA:
        flash('Esta cuenta ya tiene pagos aplicados: no se puede anular sin recuperar ese dinero.', 'error')
    elif cuenta['id_gasto']:
        flash('Esta cuenta nace de un gasto: anula el gasto para anularla.', 'error')
    elif len(motivo) < 5:
        flash('Explica el motivo de la anulación (mínimo 5 caracteres).', 'error')
    else:
        db.execute("UPDATE cuentas_por_pagar SET estado = ?, saldo_pendiente = 0, motivo_anulacion = ? WHERE id = ?", (fin.ANULADA, motivo[:255], id_cuenta))
        registrar_auditoria('anular_cxp', 'cuentas_por_pagar', f'Anuló la cuenta por pagar #{id_cuenta}. Motivo: {motivo}.', db=db)
        db.commit()
        flash('Cuenta por pagar anulada.', 'success')
        return redirect(url_for('dinero.cxp_lista'))
    return volver


# =============================================================================
# --- GASTOS ---
# =============================================================================

def _categorias(db, solo_activas=True):
    return db.execute(f"SELECT * FROM categorias_gasto {'WHERE activo = 1' if solo_activas else ''} ORDER BY nombre").fetchall()


@bp.route('/gastos')
@login_required
def gastos_lista():
    db = get_db()
    hoy = hoy_local()
    desde = fecha_o(request.args.get('desde', ''), hoy.replace(day=1))
    hasta = fecha_o(request.args.get('hasta', ''), hoy)
    categoria = request.args.get('categoria', type=int)
    condiciones, params = ['g.fecha_gasto BETWEEN ? AND ?'], [desde.isoformat(), hasta.isoformat()]
    if categoria:
        condiciones.append('g.id_categoria = ?')
        params.append(categoria)
    donde = ' AND '.join(condiciones)
    filas, pag = paginar_consulta(db, f"""SELECT g.*, c.nombre AS categoria, c.clasificacion, u.nombre AS usuario, pr.nombre AS proveedor
                                          FROM gastos g JOIN categorias_gasto c ON c.id = g.id_categoria
                                          LEFT JOIN usuarios u ON u.id = g.id_usuario LEFT JOIN proveedores pr ON pr.id = g.id_proveedor
                                          WHERE {donde} ORDER BY g.fecha_gasto DESC, g.id DESC""", params)
    total = D(db.execute(f"SELECT COALESCE(SUM(g.monto), 0) FROM gastos g WHERE {donde} AND g.estado = 'Registrado'", params).fetchone()[0])
    return render_template('gastos/lista.html', active_page='gastos', gastos=filas, pag=pag, total=total, categorias=_categorias(db, False),
                           categoria_id=categoria, desde=desde, hasta=hasta, formas=fin.FORMAS_GASTO)


def _leer_gasto(db, form):
    categoria = db.execute("SELECT * FROM categorias_gasto WHERE id = ? AND activo = 1", (form.get('id_categoria', type=int),)).fetchone()
    descripcion = form.get('descripcion', '').strip()
    monto = leer_monto(form.get('monto'))
    fecha = fecha_o(form.get('fecha_gasto', ''), hoy_local())
    forma = form.get('forma_pago', '')
    proveedor = None
    vence = fecha_o(form.get('fecha_vencimiento', ''), None)
    if categoria is None:
        return None, 'Selecciona una categoría de gasto.'
    if len(descripcion) < 3:
        return None, 'Describe el gasto: qué se pagó y por qué.'
    if monto is None:
        return None, 'Ingresa un monto válido.'
    if fecha > hoy_local():
        return None, 'La fecha del gasto no puede ser futura.'
    if forma not in fin.FORMAS_GASTO:
        return None, 'Indica cómo se paga el gasto.'
    if forma == 'credito':
        proveedor = db.execute("SELECT * FROM proveedores WHERE id = ? AND activo = 1", (form.get('id_proveedor', type=int),)).fetchone()
        if proveedor is None:
            return None, 'Para un gasto a crédito selecciona el proveedor al que se le debe.'
    return {'categoria': categoria, 'descripcion': descripcion[:300], 'monto': monto, 'fecha': fecha, 'forma': forma,
            'proveedor': proveedor, 'vence': vence}, None


@bp.route('/gastos/nuevo', methods=['GET', 'POST'])
@login_required
def gasto_nuevo():
    db = get_db()

    def mostrar():
        return render_template('gastos/form.html', active_page='gastos', form=request.form, formas=fin.FORMAS_GASTO,
                               categorias=_categorias(db), proveedores=_proveedores_activos(db),
                               saldos={'efectivo': fin.saldo_origen(db, 'caja'), 'fondo': fin.saldo_cuenta(db, 'fondo'),
                                       'por_consolidar': fin.saldo_cuenta(db, 'por_consolidar')},
                               caja_abierta=fin.turno_abierto(db) is not None, hoy=hoy_local().isoformat())

    if request.method == 'POST':
        datos, error = _leer_gasto(db, request.form)
        if error is None and datos['forma'] != 'credito':
            error = fin.validar_salida(db, fin.forma_a_origen(datos['forma']), datos['monto'])
        if error:
            flash(error, 'error')
            return mostrar()
        cur = db.execute("""INSERT INTO gastos (id_categoria, id_proveedor, descripcion, monto, fecha_gasto, forma_pago, fecha_vencimiento, id_usuario)
                            VALUES (?,?,?,?,?,?,?,?)""",
                         (datos['categoria']['id'], datos['proveedor']['id'] if datos['proveedor'] else None, datos['descripcion'], f(datos['monto']),
                          datos['fecha'].isoformat(), datos['forma'], datos['vence'].isoformat() if datos['vence'] else None, current_user.id))
        id_gasto = cur.lastrowid
        concepto = f'Gasto {datos["categoria"]["nombre"]}: {datos["descripcion"]}'
        if datos['forma'] == 'credito':
            cuenta = db.execute("""INSERT INTO cuentas_por_pagar (id_proveedor, descripcion, monto, saldo_pendiente, fecha_emision, fecha_vencimiento, id_gasto, id_usuario)
                                   VALUES (?,?,?,?,?,?,?,?)""",
                                (datos['proveedor']['id'], concepto[:300], f(datos['monto']), f(datos['monto']), datos['fecha'].isoformat(),
                                 datos['vence'].isoformat() if datos['vence'] else None, id_gasto, current_user.id))
            db.execute("UPDATE gastos SET id_cuenta_pagar = ? WHERE id = ?", (cuenta.lastrowid, id_gasto))
        else:
            mov_caja, mov_fondo = fin.registrar_salida(db, fin.forma_a_origen(datos['forma']), datos['monto'], concepto, current_user.id,
                                                       tipo_caja='gasto', tipo_fondo='gasto', referencia=f'gasto-{id_gasto}')
            db.execute("UPDATE gastos SET id_mov_caja = ?, id_mov_fondo = ? WHERE id = ?", (mov_caja, mov_fondo, id_gasto))
        registrar_auditoria('crear_gasto', 'gastos', f'Registró un gasto de {q(datos["monto"])} ({datos["categoria"]["nombre"]}, '
                            f'{fin.FORMAS_GASTO[datos["forma"]].lower()}): {datos["descripcion"]}.', db=db)
        db.commit()
        flash('Gasto registrado.', 'success')
        return redirect(url_for('dinero.gastos_lista'))
    return mostrar()


@bp.route('/gastos/<int:id_gasto>/editar', methods=['GET', 'POST'])
@login_required
@admin_required
def gasto_editar(id_gasto):
    """Corrige categoría, descripción y fecha. El monto y la forma de pago no se editan (ya movieron
    dinero): para eso se anula el gasto y se registra otro."""
    db = get_db()
    gasto = db.execute("SELECT g.*, c.nombre AS categoria FROM gastos g JOIN categorias_gasto c ON c.id = g.id_categoria WHERE g.id = ?", (id_gasto,)).fetchone()
    if gasto is None:
        abort(404)
    if gasto['estado'] != 'Registrado':
        flash('Un gasto anulado no se puede editar.', 'error')
        return redirect(url_for('dinero.gastos_lista'))
    categorias = db.execute("SELECT * FROM categorias_gasto WHERE activo = 1 OR id = ? ORDER BY nombre", (gasto['id_categoria'],)).fetchall()
    if request.method == 'POST':
        categoria = db.execute("SELECT id FROM categorias_gasto WHERE id = ?", (request.form.get('id_categoria', type=int),)).fetchone()
        descripcion = request.form.get('descripcion', '').strip()
        fecha = fecha_o(request.form.get('fecha_gasto', ''), None)
        if categoria is None:
            flash('Selecciona una categoría de gasto.', 'error')
        elif len(descripcion) < 3:
            flash('Describe el gasto: qué se pagó y por qué.', 'error')
        elif fecha is None or fecha > hoy_local():
            flash('La fecha del gasto no es válida (no puede ser futura).', 'error')
        else:
            db.execute("UPDATE gastos SET id_categoria = ?, descripcion = ?, fecha_gasto = ? WHERE id = ?", (categoria['id'], descripcion[:300], fecha.isoformat(), id_gasto))
            registrar_auditoria('editar_gasto', 'gastos', f'Editó el gasto #{id_gasto} ({q(gasto["monto"])}): {descripcion}.', db=db)
            db.commit()
            flash('Gasto actualizado.', 'success')
            return redirect(url_for('dinero.gastos_lista'))
    return render_template('gastos/editar.html', active_page='gastos', gasto=gasto, categorias=categorias, hoy=hoy_local().isoformat())


@bp.route('/gastos/<int:id_gasto>/anular', methods=['POST'])
@login_required
@admin_required
def gasto_anular(id_gasto):
    db = get_db()
    gasto = db.execute("SELECT * FROM gastos WHERE id = ?", (id_gasto,)).fetchone()
    if gasto is None:
        abort(404)
    motivo = request.form.get('motivo', '').strip()
    pagos = db.execute("SELECT 1 FROM pagos_cxp WHERE id_cuenta = ? LIMIT 1", (gasto['id_cuenta_pagar'],)).fetchone() if gasto['id_cuenta_pagar'] else None
    if gasto['estado'] == 'Anulado':
        flash('Este gasto ya está anulado.', 'error')
    elif len(motivo) < 5:
        flash('Explica el motivo de la anulación (mínimo 5 caracteres).', 'error')
    elif pagos:
        flash('Este gasto a crédito ya tiene pagos aplicados: no se puede anular.', 'error')
    else:
        concepto = f'Anulación de gasto #{id_gasto}: {motivo}'
        if gasto['id_cuenta_pagar']:
            db.execute("UPDATE cuentas_por_pagar SET estado = ?, saldo_pendiente = 0, motivo_anulacion = ? WHERE id = ?",
                       (fin.ANULADA, f'Anulación de gasto #{id_gasto}', gasto['id_cuenta_pagar']))
        else:
            fin.revertir_salida(db, gasto['id_mov_caja'], gasto['id_mov_fondo'], D(gasto['monto']), concepto, f'anulacion-gasto-{id_gasto}', current_user.id)
        db.execute("UPDATE gastos SET estado = 'Anulado', anulado_por = ?, anulado_en = ?, motivo_anulacion = ? WHERE id = ?",
                   (current_user.id, _ahora_texto(), motivo[:255], id_gasto))
        registrar_auditoria('anular_gasto', 'gastos', f'Anuló el gasto #{id_gasto} ({q(gasto["monto"])}). Motivo: {motivo}.', db=db)
        db.commit()
        flash('Gasto anulado. El dinero volvió a su cuenta.', 'success')
    return redirect(url_for('dinero.gastos_lista'))


@bp.route('/gastos/categorias', methods=['GET', 'POST'])
@login_required
def gastos_categorias():
    db = get_db()
    if request.method == 'POST':
        if current_user.rol != 'admin':
            abort(403)
        nombre = request.form.get('nombre', '').strip()
        clase = request.form.get('clasificacion', '')
        if len(nombre) < 2 or clase not in ('fijo', 'variable'):
            flash('Indica el nombre y si el gasto es fijo o variable.', 'error')
        else:
            try:
                db.execute("INSERT INTO categorias_gasto (nombre, clasificacion) VALUES (?, ?)", (nombre[:120], clase))
                registrar_auditoria('crear_categoria_gasto', 'gastos', f'Creó la categoría de gasto "{nombre}".', db=db)
                db.commit()
                flash('Categoría creada.', 'success')
            except sqlite3.IntegrityError:
                db.rollback()
                flash('Ya existe una categoría con ese nombre.', 'error')
        return redirect(url_for('dinero.gastos_categorias'))
    categorias = db.execute("""SELECT c.*, (SELECT COUNT(*) FROM gastos g WHERE g.id_categoria = c.id) AS usos
                               FROM categorias_gasto c ORDER BY c.activo DESC, c.nombre""").fetchall()
    return render_template('gastos/categorias.html', active_page='gastos', categorias=categorias)


@bp.route('/gastos/categorias/<int:id_categoria>/editar', methods=['POST'])
@login_required
@admin_required
def gastos_categoria_editar(id_categoria):
    db = get_db()
    categoria = db.execute("SELECT * FROM categorias_gasto WHERE id = ?", (id_categoria,)).fetchone()
    if categoria is None:
        abort(404)
    nombre, clase = request.form.get('nombre', '').strip(), request.form.get('clasificacion', '')
    if len(nombre) < 2 or clase not in ('fijo', 'variable'):
        flash('Indica el nombre y si el gasto es fijo o variable.', 'error')
    else:
        try:
            db.execute("UPDATE categorias_gasto SET nombre = ?, clasificacion = ? WHERE id = ?", (nombre[:120], clase, id_categoria))
            registrar_auditoria('editar_categoria_gasto', 'gastos', f'Editó la categoría de gasto "{categoria["nombre"]}" → "{nombre}".', db=db)
            db.commit()
            flash('Categoría actualizada.', 'success')
        except sqlite3.IntegrityError:
            db.rollback()
            flash('Ya existe otra categoría con ese nombre.', 'error')
    return redirect(url_for('dinero.gastos_categorias'))


@bp.route('/gastos/categorias/<int:id_categoria>/activar', methods=['POST'])
@login_required
@admin_required
def gastos_categoria_alternar(id_categoria):
    db = get_db()
    categoria = db.execute("SELECT * FROM categorias_gasto WHERE id = ?", (id_categoria,)).fetchone()
    if categoria is None:
        abort(404)
    nuevo = 0 if categoria['activo'] else 1
    db.execute("UPDATE categorias_gasto SET activo = ? WHERE id = ?", (nuevo, id_categoria))
    registrar_auditoria('activar_categoria_gasto', 'gastos', f'{"Activó" if nuevo else "Desactivó"} la categoría de gasto "{categoria["nombre"]}".', db=db)
    db.commit()
    return redirect(url_for('dinero.gastos_categorias'))


# =============================================================================
# --- REPORTES ---
# =============================================================================

@bp.route('/reportes')
@login_required
def reportes():
    return render_template('reportes/indice.html', active_page='reportes')


def _condicion_ventas(desde, hasta):
    return "v.estado <> 'Cancelada' AND v.fecha_venta BETWEEN ? AND ?", [inicio_utc(desde), fin_utc(hasta)]


@bp.route('/reportes/ventas')
@login_required
def reporte_ventas():
    db = get_db()
    desde, hasta = rango_fechas()
    cond, params = _condicion_ventas(desde, hasta)
    total, cantidad = db.execute(f"SELECT COALESCE(SUM(v.monto_total), 0), COUNT(*) FROM ventas v WHERE {cond}", params).fetchone()
    total = D(total)
    costo = fin.costo_de_ventas(db, desde, hasta)
    por_usuario = [(r[0], r[1], D(r[2])) for r in db.execute(
        f"""SELECT COALESCE(u.nombre, 'Sin registrar'), COUNT(*), SUM(v.monto_total) FROM ventas v LEFT JOIN usuarios u ON u.id = v.id_usuario
            WHERE {cond} GROUP BY 1 ORDER BY 3 DESC""", params)]
    por_metodo = [(fin.ETIQUETA_METODO.get(r[0], r[0]), r[1], D(r[2])) for r in db.execute(
        f"SELECT v.metodo_pago, COUNT(*), SUM(v.monto_total) FROM ventas v WHERE {cond} GROUP BY 1 ORDER BY 3 DESC", params)]
    por_dia = [(r[0], r[1], D(r[2])) for r in db.execute(
        f"SELECT {dia_local_sql('v.fecha_venta')} AS dia, COUNT(*), SUM(v.monto_total) FROM ventas v WHERE {cond} GROUP BY 1 ORDER BY 1", params)]
    por_tipo = {r[0]: D(r[1]) for r in db.execute(
        f"""SELECT i.tipo, SUM((vd.precio_unitario - vd.descuento) * vd.cantidad) FROM ventas_detalle vd JOIN ventas v ON v.id = vd.id_venta
            JOIN inventario i ON i.id = vd.id_item_inventario WHERE {cond} GROUP BY i.tipo""", params)}
    top = [(r[0], r[1], D(r[2]), r[3]) for r in db.execute(
        f"""SELECT i.nombre, SUM(vd.cantidad), SUM((vd.precio_unitario - vd.descuento) * vd.cantidad), i.tipo
            FROM ventas_detalle vd JOIN ventas v ON v.id = vd.id_venta JOIN inventario i ON i.id = vd.id_item_inventario
            WHERE {cond} GROUP BY i.id ORDER BY 2 DESC LIMIT 10""", params)]
    maximo = max((t for _, _, t in por_dia), default=Decimal('0'))
    por_dia = [(d, c, t, float(t / maximo * 100) if maximo else 0) for d, c, t in por_dia]

    if request.args.get('export') == 'csv':
        filas = [(d, c, f'{t:.2f}') for d, c, t, _ in por_dia]
        filas.append(('TOTAL', cantidad, f'{total:.2f}'))
        return respuesta_csv('reporte-ventas', ['Fecha', 'Ventas', 'Total (Q)'], filas)
    return render_template('reportes/ventas.html', active_page='reportes', desde=desde, hasta=hasta, total_ventas=total, cantidad_ventas=cantidad,
                           ticket_promedio=(total / cantidad).quantize(CENTAVO) if cantidad else Decimal('0.00'), utilidad_bruta=total - costo,
                           por_usuario=por_usuario, por_metodo=por_metodo, por_dia=por_dia, por_tipo=por_tipo, top=top,
                           **contexto_fechas('dinero.reporte_ventas'))


@bp.route('/reportes/productos')
@login_required
def reporte_productos():
    db = get_db()
    desde, hasta = rango_fechas()
    orden = request.args.get('orden', 'unidades')
    cond, params = _condicion_ventas(desde, hasta)
    filas = db.execute(f"""SELECT i.id, i.nombre, i.tipo, SUM(vd.cantidad) AS unidades,
                                  SUM((vd.precio_unitario - vd.descuento) * vd.cantidad) AS ingreso,
                                  SUM(vd.cantidad * COALESCE(vd.costo_unitario, i.precio_compra, 0)) AS costo
                           FROM ventas_detalle vd JOIN ventas v ON v.id = vd.id_venta JOIN inventario i ON i.id = vd.id_item_inventario
                           WHERE {cond} GROUP BY i.id""", params).fetchall()
    articulos = []
    for r in filas:
        ingreso, costo = D(r['ingreso']), D(r['costo'])
        utilidad = ingreso - costo
        articulos.append({'nombre': r['nombre'], 'tipo': r['tipo'], 'unidades': int(r['unidades'] or 0), 'ingreso': ingreso, 'costo': costo,
                          'utilidad': utilidad, 'margen': float(utilidad / ingreso * 100) if ingreso > 0 else None})
    articulos.sort(key=lambda a: a[orden if orden in ('unidades', 'ingreso', 'utilidad') else 'unidades'], reverse=True)
    vendidos = {r['id'] for r in filas}
    sin_ventas = [r for r in db.execute("SELECT id, nombre, tipo, cantidad FROM inventario WHERE is_deleted = 0 ORDER BY nombre") if r['id'] not in vendidos]

    if request.args.get('export') == 'csv':
        return respuesta_csv('reporte-productos-y-servicios', ['Artículo', 'Tipo', 'Unidades', 'Ingreso (Q)', 'Costo (Q)', 'Utilidad (Q)', 'Margen (%)'],
                             [(a['nombre'], a['tipo'], a['unidades'], f"{a['ingreso']:.2f}", f"{a['costo']:.2f}", f"{a['utilidad']:.2f}",
                               f"{a['margen']:.1f}" if a['margen'] is not None else '') for a in articulos])
    return render_template('reportes/productos.html', active_page='reportes', desde=desde, hasta=hasta, articulos=articulos, orden=orden,
                           sin_ventas=sin_ventas, total_ingreso=sum((a['ingreso'] for a in articulos), Decimal('0')),
                           total_utilidad=sum((a['utilidad'] for a in articulos), Decimal('0')), **contexto_fechas('dinero.reporte_productos'))


@bp.route('/reportes/inventario')
@login_required
def reporte_inventario():
    db = get_db()
    filtro = request.args.get('filtro', '')
    hoy = hoy_local()
    condiciones = ["is_deleted = 0", "tipo = 'Producto'"]
    params = []
    if filtro == 'bajo':
        condiciones.append("COALESCE(cantidad, 0) > 0 AND COALESCE(cantidad, 0) < 5")
    elif filtro == 'agotado':
        condiciones.append("COALESCE(cantidad, 0) <= 0")
    elif filtro == 'vencer':
        condiciones.append("fecha_vencimiento BETWEEN ? AND ?")
        params += [hoy.isoformat(), (hoy + timedelta(days=90)).isoformat()]
    productos = db.execute(f"SELECT * FROM inventario WHERE {' AND '.join(condiciones)} ORDER BY nombre", params).fetchall()
    resumen = db.execute("""SELECT COUNT(*), COALESCE(SUM(COALESCE(cantidad, 0)), 0), COALESCE(SUM(COALESCE(cantidad, 0) * COALESCE(precio_compra, 0)), 0),
                                   COALESCE(SUM(COALESCE(cantidad, 0) * COALESCE(precio_venta, 0)), 0),
                                   COALESCE(SUM(CASE WHEN COALESCE(cantidad, 0) <= 0 THEN 1 ELSE 0 END), 0),
                                   COALESCE(SUM(CASE WHEN COALESCE(cantidad, 0) > 0 AND COALESCE(cantidad, 0) < 5 THEN 1 ELSE 0 END), 0)
                            FROM inventario WHERE is_deleted = 0 AND tipo = 'Producto'""").fetchone()
    if request.args.get('export') == 'csv':
        return respuesta_csv('reporte-inventario', ['Producto', 'Existencias', 'Costo (Q)', 'Precio venta (Q)', 'Valor al costo (Q)', 'Vencimiento'],
                             [(p['nombre'], p['cantidad'] or 0, f"{D(p['precio_compra']):.2f}", f"{D(p['precio_venta']):.2f}",
                               f"{D(p['precio_compra']) * (p['cantidad'] or 0):.2f}", p['fecha_vencimiento'] or '') for p in productos])
    return render_template('reportes/inventario.html', active_page='reportes', productos=productos, filtro=filtro,
                           total_productos=resumen[0], total_unidades=resumen[1], valor_costo=D(resumen[2]), valor_venta=D(resumen[3]),
                           total_agotado=resumen[4], total_bajo=resumen[5], hoy=hoy.isoformat(),
                           limite_vencer=(hoy + timedelta(days=90)).isoformat())


@bp.route('/reportes/financiero')
@login_required
@admin_required
def reporte_financiero():
    db = get_db()
    desde, hasta = rango_fechas('mes')
    dias = (hasta - desde).days + 1
    previo_hasta = desde - timedelta(days=1)
    previo_desde = previo_hasta - timedelta(days=dias - 1)

    er = fin.estado_de_resultados(db, desde, hasta)
    er_prev = fin.estado_de_resultados(db, previo_desde, previo_hasta)
    flujo = fin.flujo_de_dinero(db, desde, hasta)
    oblig = fin.obligaciones_al_corte(db, hasta)
    saldos = fin.resumen_dinero(db)
    inv = db.execute("""SELECT COALESCE(SUM(CASE WHEN COALESCE(cantidad, 0) <= 0 THEN 1 ELSE 0 END), 0),
                               COALESCE(SUM(CASE WHEN COALESCE(cantidad, 0) > 0 AND COALESCE(cantidad, 0) < 5 THEN 1 ELSE 0 END), 0)
                        FROM inventario WHERE is_deleted = 0 AND tipo = 'Producto'""").fetchone()
    lectura = fin.lectura_del_periodo(er, er_prev, flujo, oblig, saldos, fin.problemas_de_conciliacion(db), {'agotados': inv[0], 'bajo': inv[1]})
    comparativo = [(n, a, b, fin.variacion(a, b)) for n, a, b in (
        ('Ventas', er['ventas'], er_prev['ventas']), ('Utilidad bruta', er['utilidad_bruta'], er_prev['utilidad_bruta']),
        ('Gastos', er['gastos_total'], er_prev['gastos_total']), ('Utilidad neta', er['utilidad_neta'], er_prev['utilidad_neta']))]

    if request.args.get('export') == 'csv':
        eq = er['equilibrio']
        filas = [
            ('Periodo', f'{desde.isoformat()} a {hasta.isoformat()}', ''),
            ('Ventas', f"{er['ventas']:.2f}", ''), ('Costo de ventas', f"{er['costo']:.2f}", ''),
            ('Utilidad bruta', f"{er['utilidad_bruta']:.2f}", ''),
            ('Gastos fijos', f"{er['gastos_fijos']:.2f}", ''), ('Gastos variables', f"{er['gastos_variables']:.2f}", ''),
            ('Otros ingresos', f"{er['otros_ingresos']:.2f}", ''), ('Utilidad neta', f"{er['utilidad_neta']:.2f}", ''),
            ('Punto de equilibrio (ventas necesarias)', f"{eq['ventas_necesarias']:.2f}" if eq['ventas_necesarias'] is not None else 'No calculable', ''),
            ('Entradas de dinero', f"{flujo['total_entradas']:.2f}", ''), ('Salidas de dinero', f"{flujo['total_salidas']:.2f}", ''),
            ('Flujo neto', f"{flujo['neto']:.2f}", ''), ('Flujo neto sin aportes ni préstamos', f"{flujo['neto_sin_financiamiento']:.2f}", ''),
            ('Por cobrar pendiente', f"{oblig['cobrar']['pendiente']:.2f}", ''), ('Por cobrar vencido', f"{oblig['cobrar']['vencido']:.2f}", ''),
            ('Por pagar pendiente', f"{oblig['pagar']['pendiente']:.2f}", ''), ('Por pagar vencido', f"{oblig['pagar']['vencido']:.2f}", ''),
            ('Saldo Fondo', f"{saldos['fondo']:.2f}", ''), ('Saldo Por consolidar', f"{saldos['por_consolidar']:.2f}", ''),
            ('Saldo Caja', f"{saldos['caja']:.2f}", ''),
        ]
        filas += [(f'Gasto: {n}', f'{t:.2f}', '') for n, _, t in er['gastos_por_categoria']]
        filas += [('', '', ''), ('Lectura del periodo', lectura['resumen'], '')]
        filas += [('Alerta', a, '') for a in lectura['alertas']] + [('Sugerencia', s, '') for s in lectura['sugerencias']]
        return respuesta_csv('reporte-financiero', ['Concepto', 'Valor', ''], filas)
    return render_template('reportes/financiero.html', active_page='reportes', desde=desde, hasta=hasta, previo_desde=previo_desde,
                           previo_hasta=previo_hasta, er=er, flujo=flujo, oblig=oblig, saldos=saldos, lectura=lectura,
                           comparativo=comparativo, **contexto_fechas('dinero.reporte_financiero'))


@bp.route('/reportes/libro')
@login_required
@admin_required
def reporte_libro():
    db = get_db()
    desde, hasta = rango_fechas('mes')
    cuenta = request.args.get('cuenta', '')
    movs = fin.movimientos_dinero(db, desde, hasta)
    if cuenta in ('caja', 'fondo', 'por_consolidar'):
        movs = [m for m in movs if m['cuenta'] == cuenta]
    etiquetas = {**fin.FONDO_TIPO_ETIQUETA, **{k: v for k, v in fin.CAJA_TIPO_ETIQUETA.items()}}
    etiquetas.update({'cobro_cxc': 'Cobro de cuenta'})
    nombres_cuenta = {'caja': 'Caja', 'fondo': 'Fondo', 'por_consolidar': 'Por consolidar'}
    if request.args.get('export') == 'csv':
        return respuesta_csv('libro-de-dinero',
                             ['Fecha', 'Cuenta', 'Tipo', 'Concepto', 'Naturaleza', 'Usuario', 'Entrada (Q)', 'Salida (Q)', 'Referencia'],
                             [(a_local(m['fecha']).strftime('%Y-%m-%d %H:%M'), nombres_cuenta[m['cuenta']],
                               etiquetas.get(m['tipo'], m['tipo']), m['concepto'], m['naturaleza'] or '', m['usuario'],
                               f"{m['monto']:.2f}" if m['entrada'] else '', '' if m['entrada'] else f"{m['monto']:.2f}", m['referencia']) for m in movs])
    pagina, pag = paginar_lista(movs, 50)
    entradas = sum((m['monto'] for m in movs if m['entrada'] and m['tipo'] not in fin.FLUJO_INTERNOS), Decimal('0'))
    salidas = sum((m['monto'] for m in movs if not m['entrada'] and m['tipo'] not in fin.FLUJO_INTERNOS), Decimal('0'))
    return render_template('reportes/libro.html', active_page='reportes', desde=desde, hasta=hasta, cuenta=cuenta, movs=pagina, pag=pag,
                           etiquetas=etiquetas, nombres_cuenta=nombres_cuenta, entradas=entradas, salidas=salidas, internos=fin.FLUJO_INTERNOS,
                           total=len(movs), naturalezas={**fin.NATURALEZAS_INGRESO, **fin.NATURALEZAS_EGRESO}, **contexto_fechas('dinero.reporte_libro'))


@bp.route('/reportes/detalle-del-periodo')
@login_required
def reporte_periodo():
    """Reporte imprimible de las ventas de un periodo, separado por forma de cobro (el que ya usaba la clínica)."""
    db = get_db()
    hoy = hoy_local()
    desde = fecha_o(request.args.get('fecha_inicio', ''), hoy)
    hasta = fecha_o(request.args.get('fecha_fin', ''), hoy)
    if desde > hasta:
        desde, hasta = hasta, desde
    ini, fin_dt = inicio_utc(desde), fin_utc(hasta)
    cond, params = _condicion_ventas(desde, hasta)

    ventas_data = db.execute(f"SELECT COUNT(*), COALESCE(SUM(v.monto_total), 0) FROM ventas v WHERE {cond}", params).fetchone()
    ganancia = D(ventas_data[1]) - fin.costo_de_ventas(db, desde, hasta)
    ingresos = db.execute("""SELECT COALESCE(SUM(CASE WHEN metodo_pago = 'Efectivo' THEN monto ELSE 0 END), 0),
                                    COALESCE(SUM(CASE WHEN metodo_pago <> 'Efectivo' THEN monto ELSE 0 END), 0) FROM (
                               SELECT metodo_pago, monto_total AS monto FROM ventas WHERE estado <> 'Cancelada' AND metodo_pago <> ? AND fecha_venta BETWEEN ? AND ?
                               UNION ALL SELECT a.metodo_pago, a.monto FROM abonos a LEFT JOIN cuentas_por_cobrar c ON c.id = a.id_cuenta
                                          WHERE c.estado <> 'Anulada' AND a.fecha_abono BETWEEN ? AND ?)""",
                          (fin.CREDITO, ini, fin_dt, ini, fin_dt)).fetchone()
    creditos = db.execute(f"SELECT COALESCE(SUM(v.monto_total), 0) FROM ventas v WHERE {cond} AND v.metodo_pago = ?", params + [fin.CREDITO]).fetchone()[0]
    tipos = db.execute(f"""SELECT COALESCE(SUM(CASE WHEN i.tipo = 'Producto' THEN (vd.precio_unitario - vd.descuento) * vd.cantidad ELSE 0 END), 0),
                                  COALESCE(SUM(CASE WHEN i.tipo = 'Servicio' THEN (vd.precio_unitario - vd.descuento) * vd.cantidad ELSE 0 END), 0)
                           FROM ventas_detalle vd JOIN ventas v ON v.id = vd.id_venta JOIN inventario i ON i.id = vd.id_item_inventario WHERE {cond}""", params).fetchone()
    reporte = {'fecha_inicio': desde.isoformat(), 'fecha_fin': hasta.isoformat(), 'total_ventas': D(ventas_data[1]), 'trx_count': ventas_data[0],
               'ganancia_bruta': ganancia, 'ingreso_efectivo': D(ingresos[0]), 'ingreso_bancos': D(ingresos[1]), 'creditos_nuevos': D(creditos),
               'total_productos': D(tipos[0]), 'total_servicios': D(tipos[1])}
    base = f"""SELECT v.id, v.fecha_venta, p.nombre, p.apellido, v.monto_total, v.metodo_pago, v.referencia_pago, v.saldo_pendiente,
                      GROUP_CONCAT(vd.cantidad || 'x ' || i.nombre, ', ') AS detalles_productos
               FROM ventas v LEFT JOIN pacientes p ON v.id_paciente = p.id JOIN ventas_detalle vd ON v.id = vd.id_venta
               JOIN inventario i ON vd.id_item_inventario = i.id WHERE {cond}"""
    lista_efectivo = db.execute(base + " AND v.metodo_pago = 'Efectivo' GROUP BY v.id ORDER BY v.fecha_venta", params).fetchall()
    lista_otros = db.execute(base + " AND v.metodo_pago NOT IN ('Efectivo', ?) GROUP BY v.id ORDER BY v.fecha_venta", params + [fin.CREDITO]).fetchall()
    lista_credito = db.execute(base + " AND v.metodo_pago = ? GROUP BY v.id ORDER BY v.fecha_venta", params + [fin.CREDITO]).fetchall()
    return render_template('reporte_financiero.html', reporte=reporte, lista_efectivo=lista_efectivo, lista_otros=lista_otros, lista_credito=lista_credito)
