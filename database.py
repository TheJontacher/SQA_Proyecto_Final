"""Esquema y migraciones de la base de datos de la clínica.

Este módulo NUNCA borra datos. Se puede ejecutar cuantas veces se quiera:

    python database.py            crea la base si no existe, o la migra en sitio
    python database.py --respaldo  guarda una copia consistente en backups/ (se puede programar con cron)
    python database.py --reset     (solo desarrollo) respalda y recrea la base vacía

La versión del esquema vive en ``PRAGMA user_version``:
    0 -> base original (antes del módulo de dinero y facturación)
    1 -> reservada (la base original recién creada por este módulo)
    2 -> caja con movimientos, fondo, cuentas por cobrar/pagar, gastos,
         proveedores, facturación electrónica (FEL) y auditoría
Cada migración corre en una sola transacción y, antes de empezar, se guarda
un respaldo completo en ``backups/``.
"""
import os
import sys
import secrets
import sqlite3
from datetime import datetime

from werkzeug.security import generate_password_hash

RUTA_BASE = os.path.dirname(os.path.abspath(__file__))
DATABASE_NAME = os.environ.get('CLINICA_DB') or os.path.join(RUTA_BASE, 'clinica.db')
BACKUP_DIR = os.environ.get('CLINICA_BACKUP_DIR') or os.path.join(RUTA_BASE, 'backups')
VERSION_ESQUEMA = 4

CATEGORIAS_GASTO_BASE = (
    ('Alquiler', 'fijo'),
    ('Servicios (luz, agua, internet)', 'fijo'),
    ('Sueldos y honorarios', 'fijo'),
    ('Transporte y fletes', 'variable'),
    ('Mantenimiento', 'variable'),
    ('Insumos y limpieza', 'variable'),
    ('Publicidad', 'variable'),
    ('Impuestos y trámites', 'variable'),
    ('Otros gastos', 'variable'),
)


# =============================================================================
# --- UTILIDADES ---
# =============================================================================

def _existe_tabla(conn, tabla):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (tabla,)).fetchone() is not None


def _columnas(conn, tabla):
    return {fila[1] for fila in conn.execute(f'PRAGMA table_info({tabla})')}


def _agregar_columna(conn, tabla, columna, definicion):
    if columna not in _columnas(conn, tabla):
        conn.execute(f'ALTER TABLE {tabla} ADD COLUMN {columna} {definicion}')


def respaldar_base(ruta, etiqueta):
    """Copia consistente de la base (API de respaldo de SQLite) en backups/."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    sello = datetime.now().strftime('%Y%m%d_%H%M%S')
    destino = os.path.join(BACKUP_DIR, f'clinica_{etiqueta}_{sello}.db')
    origen = sqlite3.connect(ruta)
    copia = sqlite3.connect(destino)
    try:
        origen.backup(copia)
    finally:
        copia.close()
        origen.close()
    return destino


# =============================================================================
# --- ESQUEMA BASE (versión 1: tal como estaba antes del módulo de dinero) ---
# =============================================================================

ESQUEMA_BASE = (
    """CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        nombre TEXT NOT NULL,
        rol TEXT DEFAULT 'admin'
    )""",
    """CREATE TABLE IF NOT EXISTS pacientes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL,
        apellido TEXT NOT NULL,
        fecha_nacimiento DATE,
        telefono TEXT,
        creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_deleted INTEGER NOT NULL DEFAULT 0,
        deleted_at TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS expedientes (
        id_expediente INTEGER PRIMARY KEY AUTOINCREMENT,
        id_paciente INTEGER NOT NULL,
        fecha_expediente DATE NOT NULL,
        motivo_consulta TEXT, enfermedades TEXT, vasos_agua TEXT, alimentos TEXT,
        problemas_hormonales TEXT, alergias TEXT, menstruacion TEXT, caida_cabello TEXT,
        caspa TEXT, horas_sueno TEXT, productos_corporales TEXT, otros_sintomas TEXT,
        skincare_limpieza TEXT, skincare_hidratacion TEXT, skincare_ojos TEXT,
        skincare_fotoproteccion TEXT, skincare_noche TEXT, skincare_tratamiento TEXT,
        sustancias_ingeridas TEXT,
        skincare_nuevo_limpieza TEXT, skincare_nuevo_hidratacion TEXT, skincare_nuevo_ojos TEXT,
        skincare_nuevo_fotoproteccion TEXT, skincare_nuevo_noche TEXT, skincare_nuevo_tratamiento TEXT,
        FOREIGN KEY (id_paciente) REFERENCES pacientes (id)
    )""",
    "CREATE TABLE IF NOT EXISTS sesiones (id_sesion INTEGER PRIMARY KEY AUTOINCREMENT, id_expediente INTEGER NOT NULL, fecha_sesion DATE NOT NULL, tratamiento TEXT NOT NULL, resultados TEXT, FOREIGN KEY (id_expediente) REFERENCES expedientes (id_expediente))",
    "CREATE TABLE IF NOT EXISTS documentos (id_documento INTEGER PRIMARY KEY AUTOINCREMENT, id_expediente INTEGER NOT NULL, nombre_archivo TEXT NOT NULL, ruta_archivo TEXT NOT NULL, fecha_subida TIMESTAMP DEFAULT CURRENT_TIMESTAMP, is_deleted INTEGER NOT NULL DEFAULT 0, deleted_at TIMESTAMP, FOREIGN KEY (id_expediente) REFERENCES expedientes (id_expediente))",
    """CREATE TABLE IF NOT EXISTS inventario (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        codigo_barras TEXT,
        nombre TEXT NOT NULL,
        descripcion TEXT,
        tipo TEXT NOT NULL,
        precio_venta REAL NOT NULL,
        precio_compra REAL,
        porcentaje_ganancia REAL,
        cantidad INTEGER,
        fecha_vencimiento DATE,
        ruta_foto TEXT,
        creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_deleted INTEGER NOT NULL DEFAULT 0,
        deleted_at TIMESTAMP
    )""",
    "CREATE TABLE IF NOT EXISTS movimientos_inventario (id INTEGER PRIMARY KEY AUTOINCREMENT, id_item INTEGER NOT NULL, tipo_movimiento TEXT NOT NULL, cantidad INTEGER NOT NULL, fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP, descripcion TEXT, FOREIGN KEY (id_item) REFERENCES inventario (id))",
    """CREATE TABLE IF NOT EXISTS citas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        id_paciente INTEGER NOT NULL,
        fecha_cita DATE NOT NULL,
        hora_cita TIME NOT NULL,
        descripcion TEXT,
        estado TEXT NOT NULL DEFAULT 'Pendiente',
        creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (id_paciente) REFERENCES pacientes (id)
    )""",
    """CREATE TABLE IF NOT EXISTS ventas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        id_paciente INTEGER NOT NULL,
        fecha_venta TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        monto_total REAL NOT NULL,
        descuento_total REAL DEFAULT 0,
        metodo_pago TEXT,
        referencia_pago TEXT,
        saldo_pendiente REAL DEFAULT 0,
        estado_pago TEXT DEFAULT 'Pagado',
        estado TEXT DEFAULT 'Completada',
        id_cita INTEGER,
        FOREIGN KEY (id_paciente) REFERENCES pacientes (id),
        FOREIGN KEY (id_cita) REFERENCES citas (id)
    )""",
    "CREATE TABLE IF NOT EXISTS ventas_detalle (id INTEGER PRIMARY KEY AUTOINCREMENT, id_venta INTEGER NOT NULL, id_item_inventario INTEGER NOT NULL, cantidad INTEGER NOT NULL, precio_unitario REAL NOT NULL, descuento REAL DEFAULT 0, FOREIGN KEY (id_venta) REFERENCES ventas (id), FOREIGN KEY (id_item_inventario) REFERENCES inventario (id))",
    "CREATE TABLE IF NOT EXISTS abonos (id INTEGER PRIMARY KEY AUTOINCREMENT, id_venta INTEGER NOT NULL, fecha_abono TIMESTAMP DEFAULT CURRENT_TIMESTAMP, monto REAL NOT NULL, metodo_pago TEXT NOT NULL, referencia_pago TEXT, saldo_actual_snapshot REAL, notas TEXT, FOREIGN KEY (id_venta) REFERENCES ventas (id))",
    "CREATE TABLE IF NOT EXISTS cierres_caja (id INTEGER PRIMARY KEY AUTOINCREMENT, fecha_apertura TIMESTAMP DEFAULT CURRENT_TIMESTAMP, fecha_cierre TIMESTAMP, monto_inicial REAL NOT NULL, total_ventas_efectivo REAL, total_ventas_otros REAL, monto_final_calculado REAL, monto_final_real REAL, diferencia REAL, estado TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS gastos (id INTEGER PRIMARY KEY AUTOINCREMENT, descripcion TEXT NOT NULL, proveedor TEXT, monto REAL NOT NULL, fecha_emision DATE, fecha_vencimiento DATE, estado TEXT DEFAULT 'Pendiente', fecha_pago DATE, comprobante_ruta TEXT, saldo_pendiente REAL, creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS abonos_gastos (id INTEGER PRIMARY KEY AUTOINCREMENT, id_gasto INTEGER NOT NULL, fecha_abono TIMESTAMP DEFAULT CURRENT_TIMESTAMP, monto REAL NOT NULL, metodo_pago TEXT NOT NULL, referencia_pago TEXT, saldo_actual_snapshot REAL, notas TEXT, FOREIGN KEY (id_gasto) REFERENCES gastos (id))",
)


# =============================================================================
# --- MIGRACIÓN 2: DINERO (caja, fondo, cuentas, gastos) Y FACTURACIÓN FEL ---
# =============================================================================

def _migrar_a_v2(conn):
    # ---- Columnas nuevas en tablas existentes --------------------------------
    _agregar_columna(conn, 'usuarios', 'activo', 'INTEGER NOT NULL DEFAULT 1')
    _agregar_columna(conn, 'usuarios', 'intentos_fallidos', 'INTEGER NOT NULL DEFAULT 0')
    _agregar_columna(conn, 'usuarios', 'bloqueado_hasta', 'TIMESTAMP')
    _agregar_columna(conn, 'pacientes', 'nit', 'TEXT')
    _agregar_columna(conn, 'ventas', 'id_caja', 'INTEGER')
    _agregar_columna(conn, 'ventas', 'id_usuario', 'INTEGER')
    _agregar_columna(conn, 'ventas', 'motivo_cancelacion', 'TEXT')
    _agregar_columna(conn, 'ventas', 'cancelada_en', 'TIMESTAMP')
    _agregar_columna(conn, 'ventas', 'cancelada_por', 'INTEGER')
    _agregar_columna(conn, 'ventas_detalle', 'costo_unitario', 'REAL')
    _agregar_columna(conn, 'cierres_caja', 'id_usuario_apertura', 'INTEGER')
    _agregar_columna(conn, 'cierres_caja', 'id_usuario_cierre', 'INTEGER')
    _agregar_columna(conn, 'cierres_caja', 'notas', 'TEXT')

    # ---- Tablas nuevas -------------------------------------------------------
    conn.execute("CREATE TABLE IF NOT EXISTS configuracion (clave TEXT PRIMARY KEY, valor TEXT)")

    conn.execute("""CREATE TABLE IF NOT EXISTS proveedores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL,
        nit TEXT,
        telefono TEXT,
        activo INTEGER NOT NULL DEFAULT 1,
        creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_proveedores_nombre ON proveedores(nombre COLLATE NOCASE)")

    conn.execute("""CREATE TABLE IF NOT EXISTS categorias_gasto (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL,
        clasificacion TEXT NOT NULL DEFAULT 'variable' CHECK (clasificacion IN ('fijo', 'variable')),
        activo INTEGER NOT NULL DEFAULT 1,
        creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_categorias_gasto_nombre ON categorias_gasto(nombre COLLATE NOCASE)")

    # Movimientos de efectivo dentro de un turno de caja (cierres_caja = turnos).
    conn.execute("""CREATE TABLE IF NOT EXISTS movimientos_caja (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        id_caja INTEGER NOT NULL,
        tipo TEXT NOT NULL,
        monto REAL NOT NULL,
        concepto TEXT NOT NULL,
        referencia TEXT,
        naturaleza TEXT,
        id_usuario INTEGER,
        creado_en TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (id_caja) REFERENCES cierres_caja (id)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_mov_caja_caja ON movimientos_caja(id_caja)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_mov_caja_ref ON movimientos_caja(referencia)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_mov_caja_fecha ON movimientos_caja(creado_en)")

    # Libro de Fondo: dos cuentas persistentes ('fondo' y 'por_consolidar'). Solo se agrega.
    conn.execute("""CREATE TABLE IF NOT EXISTS movimientos_fondo (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cuenta TEXT NOT NULL CHECK (cuenta IN ('fondo', 'por_consolidar')),
        tipo TEXT NOT NULL,
        monto REAL NOT NULL,
        concepto TEXT NOT NULL,
        referencia TEXT,
        naturaleza TEXT,
        id_usuario INTEGER,
        creado_en TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_mov_fondo_cuenta ON movimientos_fondo(cuenta, creado_en)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_mov_fondo_ref ON movimientos_fondo(referencia)")

    conn.execute("""CREATE TABLE IF NOT EXISTS cuentas_por_cobrar (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        id_paciente INTEGER NOT NULL,
        id_venta INTEGER,
        monto REAL NOT NULL,
        saldo_pendiente REAL NOT NULL,
        fecha_vencimiento DATE,
        estado TEXT NOT NULL DEFAULT 'Pendiente',
        notas TEXT,
        motivo_anulacion TEXT,
        id_usuario INTEGER,
        creado_en TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (id_paciente) REFERENCES pacientes (id),
        FOREIGN KEY (id_venta) REFERENCES ventas (id)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_cxc_estado ON cuentas_por_cobrar(estado)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_cxc_venta ON cuentas_por_cobrar(id_venta)")

    conn.execute("""CREATE TABLE IF NOT EXISTS gastos_nuevo (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        id_categoria INTEGER NOT NULL,
        id_proveedor INTEGER,
        descripcion TEXT NOT NULL,
        monto REAL NOT NULL,
        fecha_gasto DATE NOT NULL,
        forma_pago TEXT NOT NULL,
        fecha_vencimiento DATE,
        estado TEXT NOT NULL DEFAULT 'Registrado',
        id_mov_caja INTEGER,
        id_mov_fondo INTEGER,
        id_cuenta_pagar INTEGER,
        id_usuario INTEGER,
        creado_en TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        anulado_por INTEGER,
        anulado_en TIMESTAMP,
        motivo_anulacion TEXT,
        FOREIGN KEY (id_categoria) REFERENCES categorias_gasto (id),
        FOREIGN KEY (id_proveedor) REFERENCES proveedores (id)
    )""")

    # ---- Cuentas por pagar: son las antiguas "gastos" (facturas de proveedores) ----
    if 'id_proveedor' not in _columnas(conn, 'gastos') and _existe_tabla(conn, 'abonos_gastos'):
        _convertir_gastos_en_cuentas_por_pagar(conn)
    elif not _existe_tabla(conn, 'cuentas_por_pagar'):
        _crear_cuentas_por_pagar(conn, 'cuentas_por_pagar')
        _crear_pagos_cxp(conn, 'pagos_cxp')
    # La tabla de gastos operativos nueva toma el nombre libre.
    if _existe_tabla(conn, 'gastos_nuevo'):
        if _existe_tabla(conn, 'gastos') and 'id_categoria' not in _columnas(conn, 'gastos'):
            raise RuntimeError('La tabla gastos original no se convirtió; migración abortada.')
        if not _existe_tabla(conn, 'gastos'):
            conn.execute('ALTER TABLE gastos_nuevo RENAME TO gastos')
        else:
            conn.execute('DROP TABLE gastos_nuevo')
    conn.execute("CREATE INDEX IF NOT EXISTS ix_gastos_fecha ON gastos(fecha_gasto)")

    # ---- Cuentas por cobrar: salen de las ventas a crédito ------------------------
    _reconstruir_abonos(conn)
    _migrar_creditos_a_cuentas_por_cobrar(conn)

    # ---- Facturación electrónica (Digifact / FEL Guatemala) -----------------------
    conn.execute("""CREATE TABLE IF NOT EXISTS config_fiscal (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        ambiente TEXT NOT NULL DEFAULT 'test',
        nit TEXT NOT NULL,
        razon_social TEXT NOT NULL,
        nombre_comercial TEXT,
        usuario_digifact TEXT NOT NULL,
        password_cifrada TEXT NOT NULL,
        afiliacion_iva TEXT NOT NULL DEFAULT 'GEN',
        codigo_establecimiento INTEGER NOT NULL DEFAULT 1,
        tipo_frase INTEGER,
        escenario_frase INTEGER,
        direccion TEXT,
        codigo_postal TEXT,
        municipio TEXT,
        departamento TEXT,
        activo INTEGER NOT NULL DEFAULT 0,
        actualizado_por INTEGER,
        actualizado_en TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS facturas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        id_venta INTEGER NOT NULL,
        tipo_doc TEXT NOT NULL DEFAULT 'FACT',
        estado TEXT NOT NULL DEFAULT 'pendiente',
        ambiente TEXT NOT NULL DEFAULT 'test',
        numero_autorizacion TEXT,
        serie TEXT,
        numero TEXT,
        nit_receptor TEXT,
        nombre_receptor TEXT,
        total REAL,
        xml_base64 TEXT,
        pdf_base64 TEXT,
        anulacion_xml_base64 TEXT,
        anulacion_pdf_base64 TEXT,
        emision_snapshot TEXT,
        nit_emisor_snapshot TEXT,
        mensaje_error TEXT,
        motivo_anulacion TEXT,
        anulada_en TIMESTAMP,
        id_usuario INTEGER,
        creado_en TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (id_venta, tipo_doc),
        FOREIGN KEY (id_venta) REFERENCES ventas (id)
    )""")

    conn.execute("""CREATE TABLE IF NOT EXISTS auditoria (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        id_usuario INTEGER,
        accion TEXT NOT NULL,
        modulo TEXT NOT NULL,
        descripcion TEXT NOT NULL,
        ip TEXT,
        creado_en TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_auditoria_fecha ON auditoria(creado_en)")

    # ---- Índices útiles en tablas existentes ------------------------------------
    conn.execute("CREATE INDEX IF NOT EXISTS ix_ventas_fecha ON ventas(fecha_venta)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_ventas_paciente ON ventas(id_paciente)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_ventas_caja ON ventas(id_caja)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_ventas_detalle_venta ON ventas_detalle(id_venta)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_citas_fecha ON citas(fecha_cita)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_expedientes_paciente ON expedientes(id_paciente)")
    try:  # un solo turno de caja abierto a la vez, también ante dos peticiones simultáneas
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_caja_abierta ON cierres_caja(estado) WHERE estado = 'Abierta'")
    except sqlite3.IntegrityError:
        pass  # ya hay más de un turno abierto en los datos: lo resuelve la persona, no la migración

    # ---- Datos de arranque ------------------------------------------------------
    for nombre, clase in CATEGORIAS_GASTO_BASE:
        conn.execute("INSERT OR IGNORE INTO categorias_gasto (nombre, clasificacion) VALUES (?, ?)", (nombre, clase))

    # Todo el dinero anterior a este momento es historia: la conciliación solo
    # revisa lo registrado a partir de aquí.
    conn.execute("INSERT OR IGNORE INTO configuracion (clave, valor) VALUES ('tesoreria_desde', strftime('%Y-%m-%d %H:%M:%S', 'now'))")

    # Cada venta antigua queda ligada al turno de caja en el que ocurrió.
    conn.execute("""UPDATE ventas SET id_caja = (
            SELECT c.id FROM cierres_caja c
            WHERE ventas.fecha_venta >= c.fecha_apertura
              AND (c.fecha_cierre IS NULL OR ventas.fecha_venta <= c.fecha_cierre)
            ORDER BY c.id DESC LIMIT 1)
        WHERE id_caja IS NULL""")

    _recrear_efectivo_del_turno_abierto(conn)


def _crear_cuentas_por_pagar(conn, nombre):
    conn.execute(f"""CREATE TABLE {nombre} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        id_proveedor INTEGER,
        descripcion TEXT NOT NULL,
        monto REAL NOT NULL,
        saldo_pendiente REAL NOT NULL,
        fecha_emision DATE,
        fecha_vencimiento DATE,
        estado TEXT NOT NULL DEFAULT 'Pendiente',
        fecha_pago DATE,
        comprobante_ruta TEXT,
        motivo_anulacion TEXT,
        id_gasto INTEGER,
        id_usuario INTEGER,
        creado_en TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (id_proveedor) REFERENCES proveedores (id)
    )""")
    conn.execute(f"CREATE INDEX IF NOT EXISTS ix_{nombre}_estado ON {nombre}(estado)")


def _crear_pagos_cxp(conn, nombre):
    conn.execute(f"""CREATE TABLE {nombre} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        id_cuenta INTEGER NOT NULL,
        fecha_pago TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        monto REAL NOT NULL,
        metodo_pago TEXT NOT NULL,
        referencia_pago TEXT,
        saldo_actual_snapshot REAL,
        notas TEXT,
        id_usuario INTEGER,
        FOREIGN KEY (id_cuenta) REFERENCES cuentas_por_pagar (id)
    )""")
    conn.execute(f"CREATE INDEX IF NOT EXISTS ix_{nombre}_cuenta ON {nombre}(id_cuenta)")


def _convertir_gastos_en_cuentas_por_pagar(conn):
    """Las 'gastos' de la base original eran facturas de proveedores por pagar.
    Pasan a cuentas_por_pagar (con proveedor real) y sus abonos a pagos_cxp."""
    antes = conn.execute("""SELECT COUNT(*), ROUND(COALESCE(SUM(monto), 0), 2),
        ROUND(COALESCE(SUM(CASE WHEN estado = 'Pagado' THEN 0 ELSE COALESCE(saldo_pendiente, monto) END), 0), 2) FROM gastos""").fetchone()
    antes_pagos = conn.execute("SELECT COUNT(*), ROUND(COALESCE(SUM(monto), 0), 2) FROM abonos_gastos").fetchone()

    # Un proveedor por cada nombre distinto (sin distinguir mayúsculas ni espacios sobrantes).
    for (nombre,) in conn.execute("SELECT DISTINCT TRIM(proveedor) FROM gastos WHERE proveedor IS NOT NULL AND TRIM(proveedor) <> ''").fetchall():
        conn.execute("INSERT OR IGNORE INTO proveedores (nombre) VALUES (?)", (nombre,))
    conn.execute("INSERT OR IGNORE INTO proveedores (nombre) VALUES ('Sin proveedor')")

    _crear_cuentas_por_pagar(conn, 'cuentas_por_pagar')
    _crear_pagos_cxp(conn, 'pagos_cxp')
    conn.execute("""
        INSERT INTO cuentas_por_pagar
            (id, id_proveedor, descripcion, monto, saldo_pendiente, fecha_emision, fecha_vencimiento,
             estado, fecha_pago, comprobante_ruta, creado_en)
        SELECT g.id,
               COALESCE((SELECT p.id FROM proveedores p WHERE p.nombre = TRIM(g.proveedor) COLLATE NOCASE),
                        (SELECT p.id FROM proveedores p WHERE p.nombre = 'Sin proveedor')),
               g.descripcion, g.monto,
               CASE WHEN g.estado = 'Pagado' THEN 0 ELSE COALESCE(g.saldo_pendiente, g.monto) END,
               g.fecha_emision, g.fecha_vencimiento,
               CASE g.estado WHEN 'Pagado' THEN 'Pagada' WHEN 'Parcial' THEN 'Parcial' ELSE 'Pendiente' END,
               g.fecha_pago, g.comprobante_ruta, COALESCE(g.creado_en, CURRENT_TIMESTAMP)
        FROM gastos g
    """)
    conn.execute("""
        INSERT INTO pagos_cxp (id, id_cuenta, fecha_pago, monto, metodo_pago, referencia_pago, saldo_actual_snapshot, notas)
        SELECT id, id_gasto, fecha_abono, monto, metodo_pago, referencia_pago, saldo_actual_snapshot, notas
        FROM abonos_gastos
    """)
    despues = conn.execute("SELECT COUNT(*), ROUND(COALESCE(SUM(monto), 0), 2), ROUND(COALESCE(SUM(saldo_pendiente), 0), 2) FROM cuentas_por_pagar").fetchone()
    despues_pagos = conn.execute("SELECT COUNT(*), ROUND(COALESCE(SUM(monto), 0), 2) FROM pagos_cxp").fetchone()
    if tuple(antes) != tuple(despues) or tuple(antes_pagos) != tuple(despues_pagos):
        raise RuntimeError(f'Las cuentas por pagar no cuadran tras migrar: {tuple(antes)} -> {tuple(despues)}')
    conn.execute("DROP TABLE abonos_gastos")
    conn.execute("DROP TABLE gastos")


def _reconstruir_abonos(conn):
    """abonos pasa a colgar de una cuenta por cobrar (id_cuenta); id_venta queda opcional."""
    if 'id_cuenta' in _columnas(conn, 'abonos'):
        return
    conn.execute("""CREATE TABLE abonos_nuevo (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        id_cuenta INTEGER,
        id_venta INTEGER,
        fecha_abono TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        monto REAL NOT NULL,
        metodo_pago TEXT NOT NULL,
        referencia_pago TEXT,
        saldo_actual_snapshot REAL,
        notas TEXT,
        id_usuario INTEGER,
        FOREIGN KEY (id_cuenta) REFERENCES cuentas_por_cobrar (id),
        FOREIGN KEY (id_venta) REFERENCES ventas (id)
    )""")
    conn.execute("""INSERT INTO abonos_nuevo (id, id_venta, fecha_abono, monto, metodo_pago, referencia_pago, saldo_actual_snapshot, notas)
                    SELECT id, id_venta, fecha_abono, monto, metodo_pago, referencia_pago, saldo_actual_snapshot, notas FROM abonos""")
    conn.execute("DROP TABLE abonos")
    conn.execute("ALTER TABLE abonos_nuevo RENAME TO abonos")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_abonos_cuenta ON abonos(id_cuenta)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_abonos_venta ON abonos(id_venta)")


def _migrar_creditos_a_cuentas_por_cobrar(conn):
    ya = conn.execute("SELECT COUNT(*) FROM cuentas_por_cobrar").fetchone()[0]
    if ya:
        return
    # Toda venta a crédito, y cualquier venta que tenga abonos o saldo (por si hubiera datos atípicos).
    ventas = conn.execute("""SELECT id, id_paciente, fecha_venta, monto_total, saldo_pendiente, estado
                             FROM ventas
                             WHERE metodo_pago LIKE 'Cr%dito' OR saldo_pendiente > 0
                                OR id IN (SELECT id_venta FROM abonos)
                             ORDER BY id""").fetchall()
    for id_venta, id_paciente, fecha, monto, saldo, estado in ventas:
        tiene_abonos = conn.execute("SELECT 1 FROM abonos WHERE id_venta = ? LIMIT 1", (id_venta,)).fetchone() is not None
        if estado == 'Cancelada':
            estado_cuenta, saldo = 'Anulada', 0
        elif (saldo or 0) <= 0.004:
            estado_cuenta, saldo = 'Pagada', 0
        else:
            estado_cuenta = 'Parcial' if tiene_abonos else 'Pendiente'
        cur = conn.execute(
            """INSERT INTO cuentas_por_cobrar (id_paciente, id_venta, monto, saldo_pendiente, estado, creado_en, motivo_anulacion)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (id_paciente, id_venta, monto, saldo, estado_cuenta, fecha,
             'Venta cancelada antes de la migración' if estado_cuenta == 'Anulada' else None))
        conn.execute("UPDATE abonos SET id_cuenta = ? WHERE id_venta = ?", (cur.lastrowid, id_venta))
    huerfanos = conn.execute("SELECT COUNT(*) FROM abonos WHERE id_cuenta IS NULL").fetchone()[0]
    if huerfanos:
        raise RuntimeError(f'{huerfanos} abonos no pertenecen a ninguna venta a crédito; migración abortada.')


def _recrear_efectivo_del_turno_abierto(conn):
    """Si al migrar hay un turno abierto, su efectivo (ventas y abonos en efectivo desde la
    apertura) pasa a movimientos_caja para que el esperado siga siendo el mismo."""
    turno = conn.execute("SELECT id, fecha_apertura FROM cierres_caja WHERE estado = 'Abierta' ORDER BY id DESC LIMIT 1").fetchone()
    if turno is None or conn.execute("SELECT 1 FROM movimientos_caja WHERE id_caja = ? LIMIT 1", (turno[0],)).fetchone():
        return
    id_caja, apertura = turno
    conn.execute("""INSERT INTO movimientos_caja (id_caja, tipo, monto, concepto, referencia, creado_en)
        SELECT ?, 'venta', monto_total, 'Venta #' || id || ' (anterior a la migración)', 'venta-' || id, fecha_venta
        FROM ventas WHERE fecha_venta >= ? AND metodo_pago = 'Efectivo' AND estado <> 'Cancelada' AND monto_total > 0""",
        (id_caja, apertura))
    conn.execute("""INSERT INTO movimientos_caja (id_caja, tipo, monto, concepto, referencia, creado_en)
        SELECT ?, 'cobro_cxc', a.monto, 'Abono a venta #' || a.id_venta || ' (anterior a la migración)', 'cobro-cxc-' || a.id, a.fecha_abono
        FROM abonos a JOIN ventas v ON v.id = a.id_venta
        WHERE a.fecha_abono >= ? AND a.metodo_pago = 'Efectivo' AND v.estado <> 'Cancelada'""",
        (id_caja, apertura))


# =============================================================================
# --- PUNTO DE ENTRADA ---
# =============================================================================

# =============================================================================
# --- MIGRACIÓN 3: CORREO, VERIFICACIÓN EN 2 PASOS Y REPORTES CON IA ---
# =============================================================================

def _migrar_a_v3(conn):
    _agregar_columna(conn, 'usuarios', 'email', 'TEXT')
    _agregar_columna(conn, 'usuarios', 'email_verificado', 'INTEGER NOT NULL DEFAULT 0')
    conn.execute("CREATE INDEX IF NOT EXISTS ix_usuarios_email ON usuarios(email)")
    # Códigos de un solo uso (se guarda solo su huella, nunca el código).
    conn.execute("""CREATE TABLE IF NOT EXISTS codigos_verificacion (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        id_usuario INTEGER NOT NULL,
        proposito TEXT NOT NULL,
        email TEXT NOT NULL,
        codigo_hash TEXT NOT NULL,
        expira_en TIMESTAMP NOT NULL,
        intentos INTEGER NOT NULL DEFAULT 0,
        usado INTEGER NOT NULL DEFAULT 0,
        creado_en TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_codigos_usuario ON codigos_verificacion(id_usuario, proposito)")
    # Una sola fila de configuración (id = 1). Las contraseñas y el token se guardan cifrados.
    conn.execute("""CREATE TABLE IF NOT EXISTS config_correo (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        servidor TEXT NOT NULL DEFAULT 'smtp.gmail.com',
        puerto INTEGER NOT NULL DEFAULT 587,
        seguridad TEXT NOT NULL DEFAULT 'starttls',
        usuario TEXT NOT NULL,
        password_cifrada TEXT NOT NULL,
        remitente_nombre TEXT,
        actualizado_por INTEGER,
        actualizado_en TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS config_ia (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        modelo TEXT NOT NULL DEFAULT 'gpt-4o-mini',
        token_cifrado TEXT,
        actualizado_por INTEGER,
        actualizado_en TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS reportes_ia (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        id_usuario INTEGER,
        desde DATE NOT NULL,
        hasta DATE NOT NULL,
        modelo TEXT NOT NULL,
        contenido TEXT NOT NULL,
        enviado_a TEXT,
        creado_en TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")


def _migrar_a_v4(conn):
    # El 2FA por correo solo se activa cuando un envío de prueba salió bien: una configuración
    # SMTP equivocada no debe dejar a nadie fuera del sistema.
    _agregar_columna(conn, 'config_correo', 'probado_en', 'TIMESTAMP')


MIGRACIONES = {2: _migrar_a_v2, 3: _migrar_a_v3, 4: _migrar_a_v4}


def _crear_esquema_base(conn):
    for sentencia in ESQUEMA_BASE:
        conn.execute(sentencia)


def _usuario_inicial(conn):
    """Administrador de una instalación nueva. La contraseña se genera al azar y se
    muestra una sola vez (o se toma de CLINICA_ADMIN_PASSWORD)."""
    if conn.execute("SELECT 1 FROM usuarios LIMIT 1").fetchone():
        return None
    clave = os.environ.get('CLINICA_ADMIN_PASSWORD') or secrets.token_urlsafe(9)
    conn.execute("INSERT INTO usuarios (username, password, nombre, rol) VALUES (?, ?, ?, ?)",
                 ('admin', generate_password_hash(clave), 'Administrador', 'admin'))
    return clave


def inicializar_base_de_datos(ruta=None, verbose=True):
    """Crea la base si no existe y aplica las migraciones pendientes.

    Es idempotente y conserva todos los datos. Devuelve la contraseña inicial del
    administrador solo cuando se crea una base nueva; en cualquier otro caso, None.
    """
    ruta = ruta or DATABASE_NAME
    existia = os.path.exists(ruta) and os.path.getsize(ruta) > 0
    clave_inicial = None

    conn = sqlite3.connect(ruta, isolation_level=None)  # transacciones manuales
    try:
        version = conn.execute('PRAGMA user_version').fetchone()[0]
        if version >= VERSION_ESQUEMA:
            return None

        if existia and version < VERSION_ESQUEMA:
            conn.close()
            copia = respaldar_base(ruta, f'antes_de_migrar_v{version}')
            if verbose:
                print(f'Respaldo previo a la migración: {copia}')
            conn = sqlite3.connect(ruta, isolation_level=None)

        conn.execute('PRAGMA foreign_keys = OFF')
        conn.execute('BEGIN IMMEDIATE')
        try:
            if conn.execute('PRAGMA user_version').fetchone()[0] >= VERSION_ESQUEMA:
                conn.execute('ROLLBACK')   # otro proceso ya migró mientras esperábamos el candado
                return None
            if not existia:
                _crear_esquema_base(conn)
                clave_inicial = _usuario_inicial(conn)
            for destino in sorted(MIGRACIONES):
                if destino > version:
                    if verbose:
                        print(f'Migrando la base a la versión {destino}...')
                    MIGRACIONES[destino](conn)
            conn.execute(f'PRAGMA user_version = {VERSION_ESQUEMA}')
            conn.execute('COMMIT')
        except Exception:
            conn.execute('ROLLBACK')
            raise
        if verbose:
            print('Base de datos lista (versión %d).' % VERSION_ESQUEMA)
    finally:
        conn.close()
    return clave_inicial


# Compatibilidad con el nombre anterior. Ya NO borra datos.
def crear_base_de_datos():
    return inicializar_base_de_datos()


if __name__ == '__main__':
    if '--respaldo' in sys.argv:
        print('Respaldo guardado en:', respaldar_base(DATABASE_NAME, 'manual'))
        sys.exit(0)
    if '--reset' in sys.argv:
        if os.path.exists(DATABASE_NAME):
            if input(f"Esto vaciará '{DATABASE_NAME}' (se guardará un respaldo). Escribe BORRAR para continuar: ").strip() != 'BORRAR':
                sys.exit('Cancelado. No se tocó nada.')
            print('Respaldo:', respaldar_base(DATABASE_NAME, 'antes_de_reiniciar'))
            os.remove(DATABASE_NAME)
    clave = inicializar_base_de_datos()
    if clave:
        print(f'Usuario inicial: admin   Contraseña: {clave}   (cámbiala al entrar)')
