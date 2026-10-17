"""Lógica del dinero de la clínica: caja por turnos, libro de Fondo, cuentas por
cobrar y por pagar, gastos, conciliación y cálculos de los reportes financieros.

Regla de oro: todo dinero que entra o sale deja UN movimiento con concepto,
usuario y fecha, en la caja o en el libro de Fondo. Nada se edita ni se borra;
un error se corrige con un movimiento contrario y un motivo. Antes de sacar
dinero se valida que haya saldo.

Las funciones reciben la conexión ``db`` y NO hacen commit: el que llama decide
cuándo confirmar, para que una operación y su movimiento de dinero se guarden
juntos o no se guarden.
"""
from datetime import timedelta
from decimal import Decimal

from comun import CENTAVO, D, a_datetime, f, fin_utc, hoy_local, inicio_utc, q

# =============================================================================
# --- CATÁLOGOS ---
# =============================================================================

CREDITO = 'Crédito'
METODOS_VENTA = ('Efectivo', 'Tarjeta', 'Transferencia', 'Deposito', 'Cheque', CREDITO)
METODOS_COBRO = ('Efectivo', 'Tarjeta', 'Transferencia', 'Deposito', 'Cheque')
METODOS_CON_REFERENCIA = ('Tarjeta', 'Transferencia', 'Deposito', 'Cheque')
ETIQUETA_METODO = {'Deposito': 'Depósito'}

# Estados de las cuentas por cobrar / pagar.
PENDIENTE, PARCIAL, PAGADA, ANULADA = 'Pendiente', 'Parcial', 'Pagada', 'Anulada'
ABIERTAS = (PENDIENTE, PARCIAL)

CAJA_INGRESOS = ('venta', 'cobro_cxc', 'ingreso', 'reversion')
CAJA_EGRESOS = ('pago_cxp', 'egreso', 'gasto', 'anulacion_venta')

FONDO_ENTRADAS = (
    'cierre_caja', 'cobro_cxc', 'venta_tarjeta', 'venta_transferencia',
    'ingreso_manual', 'consolidacion_recibida', 'reversion',
)
FONDO_SALIDAS = (
    'apertura_caja', 'pago_cxp', 'gasto', 'egreso_manual',
    'consolidacion_enviada', 'anulacion_venta',
)
FONDO_TIPO_ETIQUETA = {
    'cierre_caja': 'Cierre de caja', 'cobro_cxc': 'Cobro de cuenta', 'venta_tarjeta': 'Venta con tarjeta',
    'venta_transferencia': 'Venta por transferencia o depósito', 'ingreso_manual': 'Ingreso manual',
    'consolidacion_recibida': 'Consolidación recibida', 'reversion': 'Reversión',
    'apertura_caja': 'Apertura de caja', 'pago_cxp': 'Pago a proveedor', 'gasto': 'Gasto',
    'egreso_manual': 'Egreso manual', 'consolidacion_enviada': 'Consolidación enviada',
    'anulacion_venta': 'Anulación de venta',
}
CAJA_TIPO_ETIQUETA = {
    'venta': 'Venta en efectivo', 'cobro_cxc': 'Cobro de cuenta', 'ingreso': 'Ingreso manual',
    'reversion': 'Reversión', 'pago_cxp': 'Pago a proveedor', 'egreso': 'Egreso manual',
    'gasto': 'Gasto', 'anulacion_venta': 'Anulación de venta', 'apertura': 'Monto inicial',
}
CUENTAS_FONDO = {'fondo': 'Fondo', 'por_consolidar': 'Por consolidar'}

# Naturaleza financiera obligatoria de los movimientos manuales: de dónde viene o a dónde va
# el dinero que no es una venta ni un pago ya registrado.
NATURALEZAS_INGRESO = {
    'saldo_inicial': 'Saldo inicial (al empezar a usar el módulo)',
    'aporte_capital': 'Aporte de capital', 'prestamo': 'Préstamo recibido',
    'otro_ingreso': 'Otro ingreso', 'ajuste': 'Ajuste justificado',
}
NATURALEZAS_EGRESO = {
    'activo': 'Compra de activo', 'pago_prestamo': 'Pago de préstamo', 'retiro': 'Retiro del dueño',
    'impuesto': 'Pago de impuestos', 'ajuste': 'Ajuste justificado',
}
NATURALEZAS_FINANCIAMIENTO = ('saldo_inicial', 'aporte_capital', 'prestamo')
ORIGENES_ETIQUETA = {'caja': 'la caja', 'fondo': 'el fondo', 'por_consolidar': 'por consolidar'}
ORIGENES_NOMBRE = {'efectivo': 'Efectivo de la caja', 'fondo': 'Fondo', 'por_consolidar': 'Por consolidar'}
FORMAS_GASTO = {**ORIGENES_NOMBRE, 'credito': 'A crédito'}


def forma_a_origen(forma):
    """'efectivo' (de la caja) -> 'caja'; 'fondo' y 'por_consolidar' tal cual."""
    return 'caja' if forma == 'efectivo' else forma


def destino_de_cobro(metodo):
    """A dónde entra el dinero según cómo pagó el cliente: efectivo -> caja; tarjeta ->
    por consolidar (hasta que el banco liquide); transferencia, depósito o cheque -> fondo."""
    if metodo == 'Efectivo':
        return 'caja'
    if metodo == 'Tarjeta':
        return 'por_consolidar'
    return 'fondo'


def tipo_fondo_de_venta(metodo):
    return 'venta_tarjeta' if metodo == 'Tarjeta' else 'venta_transferencia'


# =============================================================================
# --- CAJA (TURNOS) ---
# =============================================================================

def turno_abierto(db):
    return db.execute("SELECT * FROM cierres_caja WHERE estado = 'Abierta' ORDER BY id DESC LIMIT 1").fetchone()


def desglose_efectivo(db, id_caja):
    """(entradas, salidas) de efectivo del turno, sin contar el monto inicial."""
    marcas_i = ','.join('?' * len(CAJA_INGRESOS))
    marcas_e = ','.join('?' * len(CAJA_EGRESOS))
    ing = db.execute(f"SELECT COALESCE(SUM(monto), 0) FROM movimientos_caja WHERE id_caja = ? AND tipo IN ({marcas_i})",
                     (id_caja, *CAJA_INGRESOS)).fetchone()[0]
    egr = db.execute(f"SELECT COALESCE(SUM(monto), 0) FROM movimientos_caja WHERE id_caja = ? AND tipo IN ({marcas_e})",
                     (id_caja, *CAJA_EGRESOS)).fetchone()[0]
    return D(ing), D(egr)


def esperado_caja(db, turno):
    """Efectivo esperado: monto inicial + ingresos en efectivo - egresos."""
    ing, egr = desglose_efectivo(db, turno['id'])
    return D(turno['monto_inicial']) + ing - egr


def mov_caja(db, id_caja, tipo, monto, concepto, id_usuario, referencia=None, naturaleza=None):
    assert tipo in CAJA_INGRESOS + CAJA_EGRESOS, tipo
    cur = db.execute(
        "INSERT INTO movimientos_caja (id_caja, tipo, monto, concepto, referencia, naturaleza, id_usuario) VALUES (?,?,?,?,?,?,?)",
        (id_caja, tipo, f(monto), concepto[:255], referencia, naturaleza, id_usuario))
    return cur.lastrowid


# =============================================================================
# --- FONDO Y POR CONSOLIDAR ---
# =============================================================================

def mov_fondo(db, cuenta, tipo, monto, concepto, id_usuario, referencia=None, naturaleza=None):
    assert cuenta in CUENTAS_FONDO and tipo in FONDO_ENTRADAS + FONDO_SALIDAS, (cuenta, tipo)
    cur = db.execute(
        "INSERT INTO movimientos_fondo (cuenta, tipo, monto, concepto, referencia, naturaleza, id_usuario) VALUES (?,?,?,?,?,?,?)",
        (cuenta, tipo, f(monto), concepto[:300], referencia, naturaleza, id_usuario))
    return cur.lastrowid


def saldo_cuenta(db, cuenta):
    """Saldo histórico de 'fondo' o 'por_consolidar' (entradas - salidas)."""
    marcas = ','.join('?' * len(FONDO_ENTRADAS))
    total = db.execute(
        f"SELECT COALESCE(SUM(CASE WHEN tipo IN ({marcas}) THEN monto ELSE -monto END), 0) FROM movimientos_fondo WHERE cuenta = ?",
        (*FONDO_ENTRADAS, cuenta)).fetchone()[0]
    return D(total)


def saldo_origen(db, origen):
    """Dinero disponible en 'caja' (turno abierto), 'fondo' o 'por_consolidar'."""
    if origen == 'caja':
        turno = turno_abierto(db)
        return esperado_caja(db, turno) if turno else D(0)
    return saldo_cuenta(db, origen)


def validar_salida(db, origen, monto):
    """Texto de error si no se puede sacar ``monto`` de ``origen``; None si se puede."""
    if origen not in ORIGENES_ETIQUETA:
        return 'Selecciona de dónde sale el dinero.'
    if origen == 'caja' and turno_abierto(db) is None:
        return 'No hay una caja abierta para sacar efectivo.'
    disponible = saldo_origen(db, origen)
    if D(monto) > disponible:
        return f'Saldo insuficiente en {ORIGENES_ETIQUETA[origen]}. Disponible: {q(disponible)}.'
    return None


def registrar_salida(db, origen, monto, concepto, id_usuario, *, tipo_caja, tipo_fondo, referencia=None, naturaleza=None):
    """Saca dinero de caja/fondo/por_consolidar. Devuelve (id_mov_caja|None, id_mov_fondo|None).
    Quien llama debe haber validado el saldo con ``validar_salida``."""
    if origen == 'caja':
        turno = turno_abierto(db)
        if turno is None:
            raise ValueError('No hay una caja abierta.')
        return mov_caja(db, turno['id'], tipo_caja, monto, concepto, id_usuario, referencia, naturaleza), None
    return None, mov_fondo(db, origen, tipo_fondo, monto, concepto, id_usuario, referencia, naturaleza)


def registrar_entrada(db, destino, monto, concepto, id_usuario, *, tipo_caja, tipo_fondo, referencia=None, naturaleza=None):
    """Mete dinero a caja/fondo/por_consolidar. Devuelve (id_mov_caja|None, id_mov_fondo|None)."""
    if destino == 'caja':
        turno = turno_abierto(db)
        if turno is None:
            raise ValueError('No hay una caja abierta.')
        return mov_caja(db, turno['id'], tipo_caja, monto, concepto, id_usuario, referencia, naturaleza), None
    return None, mov_fondo(db, destino, tipo_fondo, monto, concepto, id_usuario, referencia, naturaleza)


def revertir_salida(db, id_mov_caja, id_mov_fondo, monto, concepto, referencia, id_usuario):
    """Devuelve a donde estaba el dinero de una salida que se anula. Caja: si su turno sigue
    abierto vuelve ahí; si ya cerró, el efectivo ya pasó al Fondo, así que entra al Fondo."""
    if id_mov_caja:
        original = db.execute("SELECT id_caja FROM movimientos_caja WHERE id = ?", (id_mov_caja,)).fetchone()
        turno = db.execute("SELECT estado FROM cierres_caja WHERE id = ?", (original['id_caja'],)).fetchone()
        if turno is not None and turno['estado'] == 'Abierta':
            mov_caja(db, original['id_caja'], 'reversion', monto, concepto, id_usuario, referencia)
        else:
            mov_fondo(db, 'fondo', 'reversion', monto, concepto, id_usuario, referencia)
    elif id_mov_fondo:
        original = db.execute("SELECT cuenta FROM movimientos_fondo WHERE id = ?", (id_mov_fondo,)).fetchone()
        mov_fondo(db, original['cuenta'], 'reversion', monto, concepto, id_usuario, referencia)


def tesoreria_desde(db):
    fila = db.execute("SELECT valor FROM configuracion WHERE clave = 'tesoreria_desde'").fetchone()
    return fila['valor'] if fila else '0000-00-00 00:00:00'


# =============================================================================
# --- CUENTAS POR COBRAR / POR PAGAR ---
# =============================================================================

def estado_tras_pago(saldo):
    return PAGADA if D(saldo) <= 0 else PARCIAL


def sincronizar_venta_con_cuenta(db, id_venta):
    """Mantiene saldo_pendiente/estado_pago de la venta a crédito igual al de su cuenta por cobrar."""
    cuenta = db.execute("SELECT saldo_pendiente, estado FROM cuentas_por_cobrar WHERE id_venta = ? ORDER BY id DESC LIMIT 1",
                        (id_venta,)).fetchone()
    if cuenta is None:
        return
    saldo = 0 if cuenta['estado'] in (PAGADA, ANULADA) else cuenta['saldo_pendiente']
    db.execute("UPDATE ventas SET saldo_pendiente = ?, estado_pago = ? WHERE id = ?",
               (saldo, 'Pendiente' if D(saldo) > 0 else 'Pagado', id_venta))


def resumen_dinero(db):
    """Saldos y posición neta de la clínica."""
    fondo = saldo_cuenta(db, 'fondo')
    consolidar = saldo_cuenta(db, 'por_consolidar')
    caja = saldo_origen(db, 'caja')
    por_cobrar = D(db.execute("SELECT COALESCE(SUM(saldo_pendiente), 0) FROM cuentas_por_cobrar WHERE estado IN ('Pendiente','Parcial')").fetchone()[0])
    por_pagar = D(db.execute("SELECT COALESCE(SUM(saldo_pendiente), 0) FROM cuentas_por_pagar WHERE estado IN ('Pendiente','Parcial')").fetchone()[0])
    return {
        'fondo': fondo, 'por_consolidar': consolidar, 'caja': caja,
        'por_cobrar': por_cobrar, 'por_pagar': por_pagar,
        'caja_abierta': turno_abierto(db) is not None,
        'neta': fondo + consolidar + caja + por_cobrar - por_pagar,
    }


# =============================================================================
# --- CONCILIACIÓN ---
# =============================================================================

def conciliar_dinero(db):
    """Revisa que cada operación con dinero tenga su movimiento y que los saldos cuadren.
    Devuelve [{titulo, ok, detalles, total}] para mostrar. Solo mira lo registrado desde que
    se activó el módulo (configuracion.tesoreria_desde): lo anterior es historia."""
    desde = tesoreria_desde(db)
    referencias = {r[0] for r in db.execute("SELECT referencia FROM movimientos_caja WHERE referencia IS NOT NULL")}
    referencias |= {r[0] for r in db.execute("SELECT referencia FROM movimientos_fondo WHERE referencia IS NOT NULL")}
    revisiones = []

    def agregar(titulo, faltantes):
        revisiones.append({'titulo': titulo, 'ok': not faltantes, 'detalles': faltantes[:10], 'total': len(faltantes)})

    # 1. Ventas cobradas
    faltan = []
    for v in db.execute("SELECT id, estado FROM ventas WHERE fecha_venta >= ? AND monto_total > 0 AND metodo_pago <> ?", (desde, CREDITO)):
        if f'venta-{v["id"]}' not in referencias:
            faltan.append(f'Venta #{v["id"]}: cobrada sin registro de dinero.')
        if v['estado'] == 'Cancelada' and f'anulacion-venta-{v["id"]}' not in referencias:
            faltan.append(f'Venta #{v["id"]}: cancelada sin devolución registrada.')
    agregar('Cada venta cobrada tiene su entrada de dinero', faltan)

    # 2. Aperturas y cierres de caja contra el Fondo
    faltan = []
    for c in db.execute("SELECT * FROM cierres_caja WHERE fecha_apertura >= ?", (desde,)):
        if c['monto_inicial'] > 0 and f'apertura-caja-{c["id"]}' not in referencias:
            faltan.append(f'Caja #{c["id"]}: apertura de {q(c["monto_inicial"])} sin origen en el Fondo.')
        if c['estado'] == 'Cerrada' and (c['monto_final_real'] or 0) > 0 and f'cierre-{c["id"]}' not in referencias:
            faltan.append(f'Caja #{c["id"]}: cierre de {q(c["monto_final_real"])} sin depósito al Fondo.')
    agregar('Cada apertura y cierre de caja pasa por el Fondo', faltan)

    # 3. Cálculo de cada turno cerrado
    faltan = []
    for c in db.execute("SELECT * FROM cierres_caja WHERE estado = 'Cerrada' AND fecha_apertura >= ?", (desde,)):
        recalculado = esperado_caja(db, c)
        if c['monto_final_calculado'] is not None and abs(recalculado - D(c['monto_final_calculado'])) > CENTAVO:
            faltan.append(f'Caja #{c["id"]}: esperado guardado {q(c["monto_final_calculado"])}, '
                          f'pero sus movimientos suman {q(recalculado)}.')
    agregar('Los turnos cerrados no cambiaron después de cerrarse', faltan)

    # 4. Cobros y pagos de cuentas
    faltan = []
    for a in db.execute("SELECT id, id_cuenta FROM abonos WHERE fecha_abono >= ?", (desde,)):
        if f'cobro-cxc-{a["id"]}' not in referencias:
            faltan.append(f'Cobro de cuenta por cobrar #{a["id_cuenta"]}: sin movimiento de dinero.')
    for p in db.execute("SELECT id, id_cuenta FROM pagos_cxp WHERE fecha_pago >= ?", (desde,)):
        if f'pago-cxp-{p["id"]}' not in referencias:
            faltan.append(f'Pago de cuenta por pagar #{p["id_cuenta"]}: sin movimiento de dinero.')
    agregar('Cada cobro y pago de cuentas movió dinero', faltan)

    # 5. Gastos
    faltan = []
    for g in db.execute("SELECT id, forma_pago, estado FROM gastos WHERE creado_en >= ?", (desde,)):
        if g['forma_pago'] != 'credito' and f'gasto-{g["id"]}' not in referencias:
            faltan.append(f'Gasto #{g["id"]}: sin movimiento de dinero.')
        if g['estado'] == 'Anulado' and g['forma_pago'] != 'credito' and f'anulacion-gasto-{g["id"]}' not in referencias:
            faltan.append(f'Gasto #{g["id"]}: anulado sin reversión del dinero.')
    agregar('Cada gasto movió dinero', faltan)

    # 6. Saldos
    faltan = []
    for cuenta, nombre in CUENTAS_FONDO.items():
        saldo = saldo_cuenta(db, cuenta)
        if saldo < 0:
            faltan.append(f'{nombre}: saldo negativo de {q(saldo)}.')
    turno = turno_abierto(db)
    if turno is not None and esperado_caja(db, turno) < 0:
        faltan.append('Caja abierta con efectivo esperado negativo.')
    agregar('Ninguna cuenta tiene saldo negativo', faltan)
    return revisiones


def problemas_de_conciliacion(db):
    return sum(r['total'] for r in conciliar_dinero(db) if not r['ok'])


# =============================================================================
# --- REPORTES FINANCIEROS ---
# =============================================================================

def _condicion_ventas(desde, hasta, alias='v'):
    """Fragmento SQL + parámetros para 'ventas completadas del periodo (días locales)'."""
    return (f"{alias}.estado <> 'Cancelada' AND {alias}.fecha_venta BETWEEN ? AND ?",
            [inicio_utc(desde), fin_utc(hasta)])


def costo_de_ventas(db, desde, hasta):
    """Costo de lo vendido: cantidad x costo guardado en la venta (o, en ventas anteriores al
    registro del costo, el precio de compra actual del artículo)."""
    cond, params = _condicion_ventas(desde, hasta)
    total = db.execute(f"""SELECT COALESCE(SUM(vd.cantidad * COALESCE(vd.costo_unitario, i.precio_compra, 0)), 0)
                           FROM ventas_detalle vd JOIN ventas v ON v.id = vd.id_venta
                           JOIN inventario i ON i.id = vd.id_item_inventario WHERE {cond}""", params).fetchone()[0]
    return D(total)


def estado_de_resultados(db, desde, hasta):
    """Ventas, costo, gastos, otros ingresos, utilidad y punto de equilibrio."""
    cond, params = _condicion_ventas(desde, hasta)
    fila = db.execute(f"SELECT COALESCE(SUM(monto_total), 0), COUNT(*) FROM ventas v WHERE {cond}", params).fetchone()
    ventas, cantidad = D(fila[0]), fila[1]
    costo = costo_de_ventas(db, desde, hasta)
    utilidad_bruta = ventas - costo

    por_categoria = [
        (r[0], r[1], D(r[2])) for r in db.execute(
            """SELECT c.nombre, c.clasificacion, SUM(g.monto) FROM gastos g JOIN categorias_gasto c ON c.id = g.id_categoria
               WHERE g.estado = 'Registrado' AND g.fecha_gasto BETWEEN ? AND ?
               GROUP BY c.nombre, c.clasificacion ORDER BY SUM(g.monto) DESC""",
            (desde.isoformat(), hasta.isoformat()))]
    fijos = sum((t for _, c, t in por_categoria if c == 'fijo'), Decimal('0'))
    variables = sum((t for _, c, t in por_categoria if c == 'variable'), Decimal('0'))

    ini, fin = inicio_utc(desde), fin_utc(hasta)
    otros = D(db.execute("SELECT COALESCE(SUM(monto), 0) FROM movimientos_caja WHERE tipo = 'ingreso' AND naturaleza = 'otro_ingreso' AND creado_en BETWEEN ? AND ?", (ini, fin)).fetchone()[0]) \
        + D(db.execute("SELECT COALESCE(SUM(monto), 0) FROM movimientos_fondo WHERE tipo = 'ingreso_manual' AND naturaleza = 'otro_ingreso' AND creado_en BETWEEN ? AND ?", (ini, fin)).fetchone()[0])

    utilidad_neta = utilidad_bruta + otros - fijos - variables
    contribucion = ventas - costo - variables
    pct_contribucion = (contribucion / ventas) if ventas > 0 else None
    necesarias = (fijos / pct_contribucion) if pct_contribucion and pct_contribucion > 0 else None
    return {
        'ventas': ventas, 'cantidad_ventas': cantidad, 'costo': costo, 'utilidad_bruta': utilidad_bruta,
        'margen_bruto_pct': (utilidad_bruta / ventas * 100) if ventas > 0 else None,
        'gastos_fijos': fijos, 'gastos_variables': variables, 'gastos_total': fijos + variables,
        'gastos_por_categoria': por_categoria, 'otros_ingresos': otros, 'utilidad_neta': utilidad_neta,
        'equilibrio': {
            'costos_fijos': fijos, 'margen_contribucion': contribucion,
            'margen_contribucion_pct': (pct_contribucion * 100) if pct_contribucion is not None else None,
            'ventas_necesarias': necesarias.quantize(CENTAVO) if necesarias is not None else None,
            'diferencia': (ventas - necesarias).quantize(CENTAVO) if necesarias is not None else None,
        },
    }


FLUJO_CANON = {
    'venta': 'Ventas en efectivo', 'venta_tarjeta': 'Ventas con tarjeta',
    'venta_transferencia': 'Ventas por transferencia o depósito',
    'cobro_cxc': 'Cobros de cuentas por cobrar', 'ingreso': 'Ingresos manuales', 'ingreso_manual': 'Ingresos manuales',
    'reversion': 'Reversiones de anulaciones',
    'pago_cxp': 'Pagos a proveedores', 'gasto': 'Gastos', 'egreso': 'Egresos manuales', 'egreso_manual': 'Egresos manuales',
    'anulacion_venta': 'Devoluciones por ventas canceladas',
}
FLUJO_INTERNOS = {
    'apertura_caja': 'Apertura de caja', 'cierre_caja': 'Cierre de caja',
    'consolidacion_enviada': 'Consolidación (sale de Por consolidar)',
    'consolidacion_recibida': 'Consolidación (entra al Fondo)',
}


def movimientos_dinero(db, desde, hasta):
    """Une los movimientos de caja y de fondo del periodo en una sola lista, del más reciente al más antiguo."""
    ini, fin = inicio_utc(desde), fin_utc(hasta)
    filas = []
    for m in db.execute("""SELECT m.*, u.nombre AS usuario FROM movimientos_caja m LEFT JOIN usuarios u ON u.id = m.id_usuario
                           WHERE m.creado_en BETWEEN ? AND ?""", (ini, fin)):
        filas.append({'fecha': m['creado_en'], 'cuenta': 'caja', 'tipo': m['tipo'], 'entrada': m['tipo'] in CAJA_INGRESOS,
                      'monto': D(m['monto']), 'concepto': m['concepto'], 'naturaleza': m['naturaleza'],
                      'usuario': m['usuario'] or '', 'referencia': m['referencia'] or ''})
    for m in db.execute("""SELECT m.*, u.nombre AS usuario FROM movimientos_fondo m LEFT JOIN usuarios u ON u.id = m.id_usuario
                           WHERE m.creado_en BETWEEN ? AND ?""", (ini, fin)):
        filas.append({'fecha': m['creado_en'], 'cuenta': m['cuenta'], 'tipo': m['tipo'], 'entrada': m['tipo'] in FONDO_ENTRADAS,
                      'monto': D(m['monto']), 'concepto': m['concepto'], 'naturaleza': m['naturaleza'],
                      'usuario': m['usuario'] or '', 'referencia': m['referencia'] or ''})
    filas.sort(key=lambda r: (r['fecha'], r['referencia']), reverse=True)
    return filas


def flujo_de_dinero(db, desde, hasta):
    """Entradas y salidas REALES de dinero en el periodo. Los traslados entre cuentas (apertura y
    cierre de caja, consolidación) se muestran aparte: no es dinero que entra ni sale de la clínica."""
    entradas, salidas, internos = {}, {}, {}
    financiamiento = Decimal('0')
    for m in movimientos_dinero(db, desde, hasta):
        if m['tipo'] in FLUJO_INTERNOS:
            etiqueta = FLUJO_INTERNOS[m['tipo']]
            internos[etiqueta] = internos.get(etiqueta, Decimal('0')) + m['monto']
            continue
        etiqueta = FLUJO_CANON.get(m['tipo'], m['tipo'])
        destino = entradas if m['entrada'] else salidas
        destino[etiqueta] = destino.get(etiqueta, Decimal('0')) + m['monto']
        if m['entrada'] and m['naturaleza'] in NATURALEZAS_FINANCIAMIENTO:
            financiamiento += m['monto']
    total_entradas = sum(entradas.values(), Decimal('0'))
    total_salidas = sum(salidas.values(), Decimal('0'))
    ordenar = lambda d: sorted(d.items(), key=lambda kv: kv[1], reverse=True)
    return {
        'entradas': ordenar(entradas), 'salidas': ordenar(salidas), 'internos': ordenar(internos),
        'total_entradas': total_entradas, 'total_salidas': total_salidas,
        'neto': total_entradas - total_salidas, 'financiamiento': financiamiento,
        'neto_sin_financiamiento': total_entradas - financiamiento - total_salidas,
    }


def obligaciones_al_corte(db, corte):
    """Cuentas por cobrar y por pagar pendientes al ``corte``, con lo vencido y lo que vence
    hoy y en los 7 días siguientes."""
    fin = fin_utc(corte)
    es_hoy = corte >= hoy_local()

    def saldos(tabla, tabla_pagos, col_cuenta, col_fecha_pago):
        cuentas = db.execute(f"SELECT * FROM {tabla} WHERE estado <> 'Anulada' AND creado_en <= ?", (fin,)).fetchall()
        pagado = {r[0]: D(r[1]) for r in db.execute(
            f"SELECT {col_cuenta}, SUM(monto) FROM {tabla_pagos} WHERE {col_fecha_pago} <= ? GROUP BY {col_cuenta}", (fin,))}
        con_pagos = {r[0] for r in db.execute(f"SELECT DISTINCT {col_cuenta} FROM {tabla_pagos}")}
        pendiente = vencido = vence_hoy = vence_7 = Decimal('0')
        for c in cuentas:
            if es_hoy:
                saldo = max(Decimal('0'), D(c['saldo_pendiente']))
            elif c['estado'] == PAGADA and c['id'] not in con_pagos:
                # Cuenta saldada antes de que existieran los registros de pago: se tiene por pagada
                # desde su fecha de pago.
                fecha_pago = a_datetime(c['fecha_pago']) if 'fecha_pago' in c.keys() else None
                saldo = Decimal('0') if fecha_pago is None or fecha_pago.date() <= corte else D(c['monto'])
            else:
                saldo = max(Decimal('0'), D(c['monto']) - pagado.get(c['id'], Decimal('0')))
            pendiente += saldo
            vence = c['fecha_vencimiento']
            if not vence or saldo <= 0:
                continue
            vence = a_datetime(vence).date()
            if vence < corte:
                vencido += saldo
            elif vence == corte:
                vence_hoy += saldo
            elif vence <= corte + timedelta(days=7):
                vence_7 += saldo
        return {'pendiente': pendiente, 'vencido': vencido, 'vence_hoy': vence_hoy, 'vence_7': vence_7}

    return {
        'cobrar': saldos('cuentas_por_cobrar', 'abonos', 'id_cuenta', 'fecha_abono'),
        'pagar': saldos('cuentas_por_pagar', 'pagos_cxp', 'id_cuenta', 'fecha_pago'),
    }


def variacion(actual, anterior):
    """Cambio porcentual entre dos periodos, o None si no hay base para comparar."""
    if anterior is None or anterior == 0:
        return None
    return float((actual - anterior) / abs(anterior) * 100)


def lectura_del_periodo(er, er_prev, flujo, oblig, saldos_dinero, conciliacion, inventario):
    """Resumen, alertas y sugerencias escritos con reglas fijas a partir de las cifras del
    periodo. No usa inteligencia artificial: la misma información siempre produce el mismo texto."""
    alertas, sugerencias, buenas = [], [], []
    ventas = er['ventas']

    resumen = (
        f"En el periodo se vendieron {q(ventas)} en {er['cantidad_ventas']} venta"
        f"{'s' if er['cantidad_ventas'] != 1 else ''}, con una utilidad bruta de {q(er['utilidad_bruta'])}"
        + (f" ({er['margen_bruto_pct']:.1f}% de margen)" if er['margen_bruto_pct'] is not None else '')
        + f", gastos por {q(er['gastos_total'])} y una utilidad neta de {q(er['utilidad_neta'])}."
    )
    eq = er['equilibrio']
    if eq['ventas_necesarias'] is not None:
        comparacion = 'superan' if eq['diferencia'] >= 0 else 'están por debajo de'
        resumen += (f" El punto de equilibrio es {q(eq['ventas_necesarias'])}: las ventas {comparacion} "
                    f"ese nivel por {q(abs(eq['diferencia']))}.")

    if er['utilidad_neta'] < 0:
        alertas.append(f"El periodo cerró con una pérdida de {q(abs(er['utilidad_neta']))}.")
        if er['gastos_por_categoria']:
            n, _, t = er['gastos_por_categoria'][0]
            sugerencias.append(f"Revisa primero el gasto más grande: {n} ({q(t)}).")
        else:
            sugerencias.append('Revisa los precios y los costos: las ventas no cubren el costo de lo vendido.')
    if eq['diferencia'] is not None and eq['diferencia'] < 0:
        alertas.append(f"Las ventas están {q(abs(eq['diferencia']))} por debajo del punto de equilibrio.")
        sugerencias.append(f"Una meta de ventas de {q(eq['ventas_necesarias'])} cubriría todos los costos del periodo.")
    if er['margen_bruto_pct'] is not None and er['margen_bruto_pct'] < 20 and ventas > 0:
        alertas.append(f"El margen bruto es bajo ({er['margen_bruto_pct']:.1f}%).")
        sugerencias.append('Compara el precio de venta con el costo de lo más vendido (reporte de Productos y servicios).')
    if ventas > 0 and er['gastos_total'] / ventas > Decimal('0.40'):
        alertas.append(f"Los gastos consumen el {er['gastos_total'] / ventas * 100:.0f}% de las ventas.")

    cambio_ventas = variacion(ventas, er_prev['ventas'])
    if cambio_ventas is not None:
        if cambio_ventas <= -15:
            alertas.append(f"Las ventas cayeron {abs(cambio_ventas):.0f}% frente al periodo anterior ({q(er_prev['ventas'])}).")
        elif cambio_ventas >= 15:
            buenas.append(f"Las ventas subieron {cambio_ventas:.0f}% frente al periodo anterior ({q(er_prev['ventas'])}).")

    pagar, cobrar = oblig['pagar'], oblig['cobrar']
    disponible = saldos_dinero['fondo'] + saldos_dinero['caja']
    if pagar['vencido'] > 0:
        alertas.append(f"Hay cuentas por pagar vencidas por {q(pagar['vencido'])}.")
        sugerencias.append('Regulariza primero lo vencido con cada proveedor (Dinero → Por pagar).')
    proximo = pagar['vence_hoy'] + pagar['vence_7']
    if proximo > 0:
        alertas.append(f"Vencen {q(proximo)} en cuentas por pagar hoy o en los próximos 7 días.")
        if disponible < proximo:
            sugerencias.append(f"El dinero disponible (Fondo + Caja) es {q(disponible)}, menos de lo que vence: "
                               'consolida tarjetas pendientes o cobra cuentas por cobrar.')
        else:
            sugerencias.append(f"Tienes {q(disponible)} disponibles (Fondo + Caja): programa esos pagos.")
    if cobrar['vencido'] > 0:
        alertas.append(f"Tus pacientes te deben {q(cobrar['vencido'])} en cuentas vencidas.")
        sugerencias.append('Cobra primero las cuentas vencidas (Dinero → Por cobrar).')
    if saldos_dinero['por_consolidar'] > 0:
        sugerencias.append(f"Hay {q(saldos_dinero['por_consolidar'])} de tarjeta por consolidar: pásalos al Fondo cuando el banco los deposite.")
    if inventario['agotados'] or inventario['bajo']:
        alertas.append(f"Inventario: {inventario['agotados']} producto(s) agotado(s) y {inventario['bajo']} con stock bajo.")
        sugerencias.append('Registra la entrada de stock de lo que se está agotando.')
    if conciliacion:
        alertas.append(f"La conciliación del dinero tiene {conciliacion} diferencia(s) sin resolver.")
        sugerencias.append('Abre Fondo → Conciliación y corrige cada registro marcado antes de decidir con estas cifras.')
    if flujo['neto_sin_financiamiento'] < 0 and flujo['total_salidas'] > 0:
        alertas.append(f"Sin contar aportes ni préstamos, salió {q(abs(flujo['neto_sin_financiamiento']))} más de lo que entró.")

    if not alertas:
        buenas.append('No se detectaron alertas en este periodo.')
    return {'resumen': resumen, 'alertas': alertas[:6], 'sugerencias': sugerencias[:5], 'positivos': buenas}
