"""Pruebas de la migración y de los flujos de dinero y facturación.

Se ejecutan contra una base temporal que imita la base ANTIGUA de la clínica (antes del módulo de
dinero), así que también prueban que la migración conserva los datos. No tocan clinica.db.

    python -m unittest discover -s tests -v
"""
import base64
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import timedelta
from decimal import Decimal
from unittest import mock

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

TMP = os.environ.get('CLINICA_TEST_ROOT') or tempfile.mkdtemp(prefix='clinica_test_')
os.makedirs(TMP, exist_ok=True)
os.environ['CLINICA_DB'] = os.path.join(TMP, 'prueba.db')
os.environ['CLINICA_BACKUP_DIR'] = os.path.join(TMP, 'backups')
os.environ['FISCAL_ENCRYPTION_KEY'] = 'WjtZ0ZfG4m9mS5y0y2o5l0tQ1w3e6r8t0y2u4i6o8pA='
os.environ['SECRET_KEY'] = 'clave-de-prueba'
os.environ.pop('DATABASE_URL', None)
os.environ.pop('FLASK_ENV', None)

from werkzeug.security import generate_password_hash  # noqa: E402

import database  # noqa: E402


def crear_base_antigua():
    """Base con el esquema anterior y datos de ejemplo, tal como la dejaba la versión vieja."""
    conn = sqlite3.connect(os.environ['CLINICA_DB'])
    for sentencia in database.ESQUEMA_BASE:
        conn.execute(sentencia)
    conn.execute("INSERT INTO usuarios (username, password, nombre, rol) VALUES ('admin', ?, 'Administrador', 'admin')", (generate_password_hash('Admin12345'),))
    conn.execute("INSERT INTO usuarios (username, password, nombre, rol) VALUES ('cajera', ?, 'Cajera', 'usuario')", (generate_password_hash('Cajera12345'),))
    for nombre, apellido, tel in (('Ana', 'Pérez', '5555-0001'), ('Luis', 'Gómez', '5555-0002'), ('Rosa', 'Díaz', '5555-0003')):
        conn.execute("INSERT INTO pacientes (nombre, apellido, telefono) VALUES (?,?,?)", (nombre, apellido, tel))
    # Productos y un servicio
    conn.execute("INSERT INTO inventario (nombre, tipo, precio_venta, precio_compra, cantidad) VALUES ('Crema', 'Producto', 50, 20, 10)")
    conn.execute("INSERT INTO inventario (nombre, tipo, precio_venta, precio_compra, cantidad) VALUES ('Sérum', 'Producto', 200, 100, 3)")
    conn.execute("INSERT INTO inventario (nombre, tipo, precio_venta, cantidad) VALUES ('Limpieza facial', 'Servicio', 300, NULL)")
    # Turno antiguo cerrado
    conn.execute("INSERT INTO cierres_caja (fecha_apertura, fecha_cierre, monto_inicial, monto_final_calculado, monto_final_real, diferencia, estado) "
                 "VALUES ('2026-01-05 15:00:00', '2026-01-05 23:00:00', 100, 400, 400, 0, 'Cerrada')")
    # Ventas antiguas: efectivo, crédito con abono, crédito cancelada
    conn.execute("INSERT INTO ventas (id_paciente, fecha_venta, monto_total, metodo_pago, estado_pago, estado) VALUES (1, '2026-01-05 16:00:00', 300, 'Efectivo', 'Pagado', 'Completada')")
    conn.execute("INSERT INTO ventas_detalle (id_venta, id_item_inventario, cantidad, precio_unitario, descuento) VALUES (1, 3, 1, 300, 0)")
    conn.execute("INSERT INTO ventas (id_paciente, fecha_venta, monto_total, metodo_pago, saldo_pendiente, estado_pago, estado) VALUES (2, '2026-01-05 17:00:00', 400, 'Crédito', 250, 'Pendiente', 'Completada')")
    conn.execute("INSERT INTO ventas_detalle (id_venta, id_item_inventario, cantidad, precio_unitario, descuento) VALUES (2, 2, 2, 200, 0)")
    conn.execute("INSERT INTO abonos (id_venta, fecha_abono, monto, metodo_pago, referencia_pago, saldo_actual_snapshot) VALUES (2, '2026-01-06 10:00:00', 150, 'Efectivo', '', 250)")
    conn.execute("INSERT INTO ventas (id_paciente, fecha_venta, monto_total, metodo_pago, saldo_pendiente, estado_pago, estado) VALUES (3, '2026-01-05 18:00:00', 100, 'Crédito', 0, 'Pendiente', 'Cancelada')")
    conn.execute("INSERT INTO ventas_detalle (id_venta, id_item_inventario, cantidad, precio_unitario, descuento) VALUES (3, 1, 2, 50, 0)")
    # Turno abierto con una venta en efectivo dentro (debe pasar a movimientos_caja al migrar)
    conn.execute("INSERT INTO cierres_caja (fecha_apertura, monto_inicial, estado) VALUES ('2026-01-07 15:00:00', 100, 'Abierta')")
    conn.execute("INSERT INTO ventas (id_paciente, fecha_venta, monto_total, metodo_pago, estado_pago, estado) VALUES (1, '2026-01-07 16:00:00', 50, 'Efectivo', 'Pagado', 'Completada')")
    conn.execute("INSERT INTO ventas_detalle (id_venta, id_item_inventario, cantidad, precio_unitario, descuento) VALUES (4, 1, 1, 50, 0)")
    # Facturas de proveedores antiguas (gastos): una pagada, una pendiente con abono parcial
    conn.execute("INSERT INTO gastos (descripcion, proveedor, monto, fecha_emision, fecha_vencimiento, estado, fecha_pago, saldo_pendiente) VALUES ('Factura 1', 'Distribuidora Uno', 500, '2025-12-01', '2026-01-01', 'Pagado', '2026-01-02', 0)")
    conn.execute("INSERT INTO gastos (descripcion, proveedor, monto, fecha_emision, fecha_vencimiento, estado, saldo_pendiente) VALUES ('Factura 2', ' distribuidora uno ', 1000, '2026-01-02', '2026-02-01', 'Parcial', 700)")
    conn.execute("INSERT INTO abonos_gastos (id_gasto, fecha_abono, monto, metodo_pago, saldo_actual_snapshot) VALUES (2, '2026-01-03 10:00:00', 300, 'Transferencia', 700)")
    conn.commit()
    conn.close()


crear_base_antigua()

import app as aplicacion  # noqa: E402  (al importarse migra la base)
import comun  # noqa: E402
import finanzas as fin  # noqa: E402


def fecha_gasto_pasada():
    return (comun.hoy_local() - timedelta(days=1)).isoformat()


aplicacion.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, UPLOAD_FOLDER=os.path.join(TMP, 'uploads'))
os.makedirs(aplicacion.app.config['UPLOAD_FOLDER'], exist_ok=True)
_raiz_temporal = os.path.realpath(TMP)
for _ruta in (os.environ['CLINICA_DB'], os.environ['CLINICA_BACKUP_DIR'], aplicacion.app.config['UPLOAD_FOLDER']):
    if os.path.commonpath((_raiz_temporal, os.path.realpath(_ruta))) != _raiz_temporal:
        raise RuntimeError(f'La ruta de prueba no es temporal: {_ruta}')
if not (
    database.DATABASE_NAME == comun.DATABASE == aplicacion.DATABASE == os.environ['CLINICA_DB']
    and database.BACKUP_DIR == os.environ['CLINICA_BACKUP_DIR']
):
    raise RuntimeError('La aplicación y sus módulos no comparten la configuración temporal de base de datos.')


def consulta(sql, *params):
    conn = sqlite3.connect(os.environ['CLINICA_DB'])
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def uno(sql, *params):
    return consulta(sql, *params)[0][0]


class Test1Migracion(unittest.TestCase):
    def test_conserva_los_datos_y_sus_totales(self):
        self.assertEqual(uno('PRAGMA user_version'), database.VERSION_ESQUEMA)
        self.assertEqual(uno('SELECT COUNT(*) FROM ventas'), 4)
        self.assertEqual(uno('SELECT COUNT(*) FROM ventas_detalle'), 4)
        self.assertEqual(uno('SELECT COUNT(*) FROM pacientes'), 3)
        self.assertEqual(uno('SELECT COUNT(*) FROM cierres_caja'), 2)
        self.assertEqual(uno('SELECT COUNT(*) FROM abonos'), 1)
        self.assertEqual(len(os.listdir(os.environ['CLINICA_BACKUP_DIR'])), 1, 'debe quedar un respaldo previo')

    def test_creditos_pasan_a_cuentas_por_cobrar(self):
        cuentas = {c['id_venta']: c for c in consulta('SELECT * FROM cuentas_por_cobrar')}
        self.assertEqual(set(cuentas), {2, 3})
        self.assertEqual((cuentas[2]['estado'], cuentas[2]['saldo_pendiente']), ('Parcial', 250))
        self.assertEqual((cuentas[3]['estado'], cuentas[3]['saldo_pendiente']), ('Anulada', 0))
        self.assertEqual(uno('SELECT id_cuenta FROM abonos'), cuentas[2]['id'])

    def test_facturas_de_proveedor_pasan_a_cuentas_por_pagar(self):
        cuentas = consulta('SELECT c.*, p.nombre AS proveedor FROM cuentas_por_pagar c JOIN proveedores p ON p.id = c.id_proveedor ORDER BY c.id')
        self.assertEqual([c['estado'] for c in cuentas], ['Pagada', 'Parcial'])
        self.assertEqual(cuentas[1]['saldo_pendiente'], 700)
        self.assertEqual(cuentas[0]['proveedor'], cuentas[1]['proveedor'], 'el mismo proveedor escrito distinto es uno solo')
        self.assertEqual(uno('SELECT COUNT(*) FROM pagos_cxp'), 1)
        fecha_pago_legado = uno('SELECT fecha_pago FROM pagos_cxp WHERE id = 1')
        inicio_tesoreria = uno("SELECT valor FROM configuracion WHERE clave = 'tesoreria_desde'")
        self.assertLess(fecha_pago_legado, inicio_tesoreria, 'el pago del fixture es anterior a la activación de tesorería')

    def test_el_efectivo_del_turno_abierto_se_conserva(self):
        turno = consulta("SELECT * FROM cierres_caja WHERE estado = 'Abierta'")[0]
        conn = sqlite3.connect(os.environ['CLINICA_DB']); conn.row_factory = sqlite3.Row
        try:
            self.assertEqual(fin.esperado_caja(conn, turno), Decimal('150.00'))   # 100 de base + 50 de la venta
        finally:
            conn.close()

    def test_es_idempotente(self):
        self.assertIsNone(database.inicializar_base_de_datos(os.environ['CLINICA_DB'], verbose=False))
        self.assertEqual(uno('SELECT COUNT(*) FROM cuentas_por_cobrar'), 2)

    def test_base_nueva_se_crea_sin_perder_ni_borrar(self):
        ruta = os.path.join(TMP, 'nueva.db')
        clave = database.inicializar_base_de_datos(ruta, verbose=False)
        self.assertTrue(clave)
        self.assertEqual(sqlite3.connect(ruta).execute('PRAGMA user_version').fetchone()[0], database.VERSION_ESQUEMA)
        # Volver a ejecutarlo NO debe borrar nada (el script anterior eliminaba la base).
        conn = sqlite3.connect(ruta); conn.execute("INSERT INTO pacientes (nombre, apellido) VALUES ('X', 'Y')"); conn.commit(); conn.close()
        self.assertIsNone(database.inicializar_base_de_datos(ruta, verbose=False))
        self.assertEqual(sqlite3.connect(ruta).execute('SELECT COUNT(*) FROM pacientes').fetchone()[0], 1)


class Test2Flujos(unittest.TestCase):
    """Estas pruebas son una historia en orden: cada una parte de lo que dejó la anterior."""

    @classmethod
    def setUpClass(cls):
        cls.cli = aplicacion.app.test_client()
        r = cls.cli.post('/login', data={'username': 'admin', 'password': 'Admin12345'})
        assert r.status_code == 302, r.data

    def post(self, url, **datos):
        return self.cli.post(url, data=datos, follow_redirects=False)

    def mensaje(self):
        """Texto de los avisos (flash) pendientes del cliente, y los consume."""
        with self.cli.session_transaction() as sesion:
            textos = [m for _, m in sesion.get('_flashes', [])]
            sesion['_flashes'] = []
        return ' | '.join(textos)

    def saldo(self, cuenta):
        conn = sqlite3.connect(os.environ['CLINICA_DB']); conn.row_factory = sqlite3.Row
        try:
            return fin.saldo_origen(conn, cuenta)
        finally:
            conn.close()

    def esperado(self):
        return self.saldo('caja')

    def venta(self, items, metodo='Efectivo', paciente=1, referencia='', descuento='0', token=None):
        datos = {'id_paciente': str(paciente), 'metodo_pago': metodo, 'referencia_pago': referencia, 'descuento_general': descuento,
                 'token': token or os.urandom(6).hex(), 'item_ids[]': [str(i) for i, _, _ in items],
                 'quantities[]': [str(c) for _, c, _ in items], 'discounts[]': [str(d) for _, _, d in items]}
        return self.cli.post('/ventas/nueva', data=datos)

    # ---- páginas ----
    def test_01_todas_las_pantallas_abren(self):
        urls = ['/', '/pacientes', '/citas', '/citas/nueva', '/inventario', '/ventas', '/ventas/nueva', '/caja', '/caja/historial', '/caja/2',
                '/cuentas-por-cobrar', '/cuentas-por-cobrar/nueva', '/cuentas-por-cobrar/1', '/cuentas-por-pagar', '/cuentas-por-pagar/nueva',
                '/cuentas-por-pagar/2', '/gastos', '/gastos/nuevo', '/gastos/categorias', '/fondo', '/fondo?cuenta=por_consolidar', '/proveedores',
                '/proveedores/nuevo', '/reportes', '/reportes/ventas?desde=2026-01-01&hasta=2026-12-31', '/reportes/productos?desde=2026-01-01&hasta=2026-12-31',
                '/reportes/inventario', '/reportes/inventario?filtro=bajo', '/reportes/financiero?desde=2026-01-01&hasta=2026-12-31',
                '/reportes/libro', '/reportes/detalle-del-periodo?fecha_inicio=2026-01-01&fecha_fin=2026-12-31', '/facturacion/documentos',
                '/facturacion/configuracion', '/usuarios', '/auditoria', '/papelera', '/venta/detalle/1', '/venta/detalle/2', '/venta/detalle/3',
                '/paciente/1/expedientes', '/paciente/1/expediente/nuevo', '/inventario/item/nuevo', '/inventario/historial', '/citas/historial']
        for url in urls:
            with self.subTest(url=url):
                r = self.cli.get(url)
                self.assertEqual(r.status_code, 200, f'{url} -> {r.status_code}')

    def test_02_las_direcciones_antiguas_siguen_funcionando(self):
        self.assertEqual(self.cli.get('/creditos').status_code, 302)
        self.assertEqual(self.cli.get('/pagos').status_code, 302)

    # ---- caja y fondo ----
    def test_10_cerrar_el_turno_antiguo_manda_el_efectivo_al_fondo(self):
        self.assertEqual(self.saldo('fondo'), 0)
        # contó 140 (esperaba 150): falta explicar
        self.post('/caja/cerrar', monto_contado='140', notas='')
        self.assertEqual(uno("SELECT COUNT(*) FROM cierres_caja WHERE estado = 'Abierta'"), 1, 'sin notas no debe cerrar con diferencia')
        self.post('/caja/cerrar', monto_contado='140', notas='Se dieron 10 de cambio sin anotar')
        self.assertEqual(uno("SELECT COUNT(*) FROM cierres_caja WHERE estado = 'Abierta'"), 0)
        self.assertEqual(self.saldo('fondo'), Decimal('140.00'))
        cierre = consulta('SELECT * FROM cierres_caja WHERE id = 2')[0]
        self.assertEqual((cierre['monto_final_calculado'], cierre['monto_final_real'], cierre['diferencia']), (150, 140, -10))

    def test_11_no_se_vende_sin_caja_abierta(self):
        self.venta([(1, 1, 0)])
        self.assertEqual(uno('SELECT COUNT(*) FROM ventas'), 4)

    def test_12_abrir_caja_exige_origen_y_saldo(self):
        self.post('/caja/abrir', monto_inicial='100')
        self.assertEqual(uno("SELECT COUNT(*) FROM cierres_caja WHERE estado = 'Abierta'"), 0, 'falta indicar de dónde sale')
        self.post('/caja/abrir', monto_inicial='500', origen_apertura='fondo')
        self.assertEqual(uno("SELECT COUNT(*) FROM cierres_caja WHERE estado = 'Abierta'"), 0, 'el fondo solo tiene 140')
        self.post('/caja/abrir', monto_inicial='100', origen_apertura='fondo')
        self.assertEqual(uno("SELECT COUNT(*) FROM cierres_caja WHERE estado = 'Abierta'"), 1)
        self.assertEqual(self.saldo('fondo'), Decimal('40.00'))
        self.assertEqual(self.esperado(), Decimal('100.00'))
        # Un segundo turno abierto es imposible
        self.post('/caja/abrir', monto_inicial='0')
        self.assertEqual(uno("SELECT COUNT(*) FROM cierres_caja WHERE estado = 'Abierta'"), 1)

    def test_20_venta_en_efectivo(self):
        r = self.venta([(1, 2, 0)])                      # 2 cremas a Q50
        self.assertEqual(r.status_code, 302)
        self.assertEqual(uno('SELECT monto_total FROM ventas ORDER BY id DESC LIMIT 1'), 100)
        self.assertEqual(self.esperado(), Decimal('200.00'))
        self.assertEqual(uno('SELECT cantidad FROM inventario WHERE id = 1'), 8)
        detalle = consulta('SELECT * FROM ventas_detalle ORDER BY id DESC LIMIT 1')[0]
        self.assertEqual(detalle['costo_unitario'], 20, 'guarda el costo del momento')
        self.assertTrue(uno("SELECT COUNT(*) FROM movimientos_caja WHERE referencia = 'venta-5'"))

    def test_21_el_doble_clic_no_duplica_la_venta(self):
        antes = uno('SELECT COUNT(*) FROM ventas')
        self.venta([(1, 1, 0)], token='mismo-token')
        self.venta([(1, 1, 0)], token='mismo-token')
        self.assertEqual(uno('SELECT COUNT(*) FROM ventas'), antes + 1)
        # devolvemos esa venta para dejar el estado como estaba
        self.post(f'/ventas/cancelar/{uno("SELECT MAX(id) FROM ventas")}', motivo='Prueba de doble clic', origen='caja')

    def test_22_ventas_con_tarjeta_y_transferencia_van_a_su_cuenta(self):
        self.venta([(3, 1, 0)], metodo='Tarjeta', referencia='AUT123')       # servicio Q300
        self.assertEqual(self.saldo('por_consolidar'), Decimal('300.00'))
        self.venta([(2, 1, 0)], metodo='Transferencia', referencia='TR55')   # sérum Q200
        self.assertEqual(self.saldo('fondo'), Decimal('240.00'))              # 40 + 200
        self.assertEqual(self.esperado(), Decimal('200.00'), 'tarjeta y transferencia no tocan el efectivo')
        self.assertEqual(uno('SELECT cantidad FROM inventario WHERE id = 3'), None, 'el servicio no tiene stock')

    def test_23_sin_referencia_no_se_vende_con_tarjeta(self):
        antes = uno('SELECT COUNT(*) FROM ventas')
        self.venta([(1, 1, 0)], metodo='Tarjeta', referencia='')
        self.assertEqual(uno('SELECT COUNT(*) FROM ventas'), antes)

    def test_24_validaciones_de_la_venta(self):
        antes = uno('SELECT COUNT(*) FROM ventas')
        casos = [
            ('cantidad cero', {'items': [(1, 0, 0)]}, 'inválid'),
            ('cantidad negativa', {'items': [(1, -3, 0)]}, 'inválid'),
            ('descuento mayor que el precio', {'items': [(1, 1, 80)]}, 'descuento'),
            ('descuento general excesivo', {'items': [(1, 1, 0)], 'descuento': '999'}, 'descuento general'),
            ('más que el stock', {'items': [(1, 500, 0)]}, 'stock'),
            ('método inventado', {'items': [(1, 1, 0)], 'metodo': 'Bitcoin'}, 'método de pago'),
            ('línea repetida que supera el stock', {'items': [(1, 4, 0), (1, 4, 0), (1, 4, 0)]}, 'stock'),
            ('paciente inexistente', {'items': [(1, 1, 0)], 'paciente': 999}, 'paciente'),
            ('artículo inexistente', {'items': [(999, 1, 0)]}, 'ya no existe'),
        ]
        for nombre, kw, _texto in casos:
            with self.subTest(nombre):
                self.mensaje()
                self.venta(kw.pop('items'), **kw)
                aviso = self.mensaje()
                self.assertTrue(aviso, 'debe explicar el rechazo')
        self.assertEqual(uno('SELECT COUNT(*) FROM ventas'), antes)

    def test_25_venta_a_credito_crea_la_cuenta(self):
        self.venta([(2, 1, 0)], metodo='Crédito', paciente=2)
        id_venta = uno('SELECT MAX(id) FROM ventas')
        cuenta = consulta('SELECT * FROM cuentas_por_cobrar WHERE id_venta = ?', id_venta)[0]
        self.assertEqual((cuenta['monto'], cuenta['saldo_pendiente'], cuenta['estado']), (200, 200, 'Pendiente'))
        self.assertEqual(uno('SELECT saldo_pendiente FROM ventas WHERE id = ?', id_venta), 200)
        self.assertEqual(self.esperado(), Decimal('200.00'), 'el crédito no mueve dinero')

    def test_26_cobrar_la_cuenta(self):
        id_cuenta = uno('SELECT MAX(id) FROM cuentas_por_cobrar')
        self.post(f'/cuentas-por-cobrar/{id_cuenta}/pagar', monto='500', metodo_pago='Efectivo')     # más que el saldo
        self.assertEqual(uno('SELECT saldo_pendiente FROM cuentas_por_cobrar WHERE id = ?', id_cuenta), 200)
        self.post(f'/cuentas-por-cobrar/{id_cuenta}/pagar', monto='50', metodo_pago='Efectivo')
        self.assertEqual(self.esperado(), Decimal('250.00'))
        self.post(f'/cuentas-por-cobrar/{id_cuenta}/pagar', monto='60', metodo_pago='Transferencia')   # falta la referencia
        self.assertEqual(uno('SELECT saldo_pendiente FROM cuentas_por_cobrar WHERE id = ?', id_cuenta), 150)
        self.post(f'/cuentas-por-cobrar/{id_cuenta}/pagar', monto='150', metodo_pago='Transferencia', referencia_pago='TR-9')
        cuenta = consulta('SELECT * FROM cuentas_por_cobrar WHERE id = ?', id_cuenta)[0]
        self.assertEqual((cuenta['estado'], cuenta['saldo_pendiente']), ('Pagada', 0))
        self.assertEqual(self.saldo('fondo'), Decimal('390.00'))
        venta = consulta('SELECT * FROM ventas WHERE id = ?', cuenta['id_venta'])[0]
        self.assertEqual((venta['estado_pago'], venta['saldo_pendiente']), ('Pagado', 0), 'la venta se sincroniza con su cuenta')

    def test_27_cobrar_en_efectivo_exige_caja_abierta_y_las_cuentas_antiguas_siguen_cobrables(self):
        # La cuenta migrada (venta 2) tenía Q250 de saldo
        self.post('/cuentas-por-cobrar/1/pagar', monto='100', metodo_pago='Efectivo')
        self.assertEqual(uno('SELECT saldo_pendiente FROM cuentas_por_cobrar WHERE id = 1'), 150)
        self.assertEqual(self.esperado(), Decimal('350.00'))

    # ---- gastos y cuentas por pagar ----
    def test_30_gastos(self):
        categoria = uno("SELECT id FROM categorias_gasto WHERE nombre = 'Alquiler'")
        base = {'id_categoria': str(categoria), 'fecha_gasto': fecha_gasto_pasada(), 'descripcion': 'Alquiler de prueba'}
        self.post('/gastos/nuevo', monto='9999', forma_pago='efectivo', **base)       # más que la caja
        self.assertEqual(uno('SELECT COUNT(*) FROM gastos'), 0)
        self.post('/gastos/nuevo', monto='80', forma_pago='efectivo', **base)
        self.assertEqual(self.esperado(), Decimal('270.00'))
        self.post('/gastos/nuevo', monto='100', forma_pago='fondo', **base)
        self.assertEqual(self.saldo('fondo'), Decimal('290.00'))
        self.post('/gastos/nuevo', monto='60', forma_pago='credito', **base)                      # sin proveedor
        self.assertEqual(uno('SELECT COUNT(*) FROM gastos'), 2)
        prov = uno('SELECT id FROM proveedores LIMIT 1')
        self.post('/gastos/nuevo', monto='60', forma_pago='credito', id_proveedor=str(prov), **base)
        self.assertEqual(uno('SELECT COUNT(*) FROM gastos'), 3)
        self.assertEqual(uno("SELECT COUNT(*) FROM cuentas_por_pagar WHERE id_gasto IS NOT NULL"), 1)

    def test_31_anular_un_gasto_devuelve_el_dinero(self):
        id_gasto = uno("SELECT id FROM gastos WHERE forma_pago = 'efectivo'")
        self.post(f'/gastos/{id_gasto}/anular', motivo='x')                      # motivo muy corto
        self.assertEqual(uno('SELECT estado FROM gastos WHERE id = ?', id_gasto), 'Registrado')
        self.post(f'/gastos/{id_gasto}/anular', motivo='Se registró dos veces')
        self.assertEqual(uno('SELECT estado FROM gastos WHERE id = ?', id_gasto), 'Anulado')
        self.assertEqual(self.esperado(), Decimal('350.00'))

    def test_32_pagar_una_cuenta_por_pagar(self):
        self.post('/cuentas-por-pagar/2/pagar', monto='999', forma_pago='fondo')            # más que el saldo (700)
        self.assertEqual(uno('SELECT saldo_pendiente FROM cuentas_por_pagar WHERE id = 2'), 700)
        self.post('/cuentas-por-pagar/2/pagar', monto='200', forma_pago='fondo')
        self.assertEqual(uno('SELECT saldo_pendiente FROM cuentas_por_pagar WHERE id = 2'), 500)
        self.assertEqual(self.saldo('fondo'), Decimal('90.00'))
        self.post('/cuentas-por-pagar/2/pagar', monto='500', forma_pago='fondo')            # fondo sin saldo suficiente
        self.assertEqual(uno('SELECT saldo_pendiente FROM cuentas_por_pagar WHERE id = 2'), 500)
        self.post('/cuentas-por-pagar/2/pagar', monto='300', forma_pago='efectivo')
        self.assertEqual(uno('SELECT estado FROM cuentas_por_pagar WHERE id = 2'), 'Parcial')
        self.assertEqual(self.esperado(), Decimal('50.00'))

    def test_33_consolidar_tarjeta(self):
        self.post('/fondo/consolidar', monto='9999')
        self.assertEqual(self.saldo('por_consolidar'), Decimal('300.00'))
        self.post('/fondo/consolidar', monto='300', notas='Depósito del banco')
        self.assertEqual(self.saldo('por_consolidar'), 0)
        self.assertEqual(self.saldo('fondo'), Decimal('390.00'))

    # ---- cancelar ventas ----
    def test_40_cancelar_una_venta_en_efectivo_devuelve_stock_y_dinero(self):
        id_venta = 5                                       # cobró Q100 en efectivo; en caja hay Q50
        self.assertEqual(uno('SELECT cantidad FROM inventario WHERE id = 1'), 8)
        self.post(f'/ventas/cancelar/{id_venta}', motivo='Error de digitación', origen='caja')
        self.assertEqual(uno('SELECT estado FROM ventas WHERE id = ?', id_venta), 'Completada', 'la caja no tiene Q100 para devolver')
        self.post(f'/ventas/cancelar/{id_venta}', motivo='x', origen='fondo')
        self.assertEqual(uno('SELECT estado FROM ventas WHERE id = ?', id_venta), 'Completada', 'motivo muy corto')
        self.post(f'/ventas/cancelar/{id_venta}', motivo='Error de digitación', origen='fondo')
        self.assertEqual(uno('SELECT estado FROM ventas WHERE id = ?', id_venta), 'Cancelada')
        self.assertEqual(uno('SELECT cantidad FROM inventario WHERE id = 1'), 10)
        self.assertEqual(self.saldo('fondo'), Decimal('290.00'))
        # No se puede cancelar dos veces
        self.post(f'/ventas/cancelar/{id_venta}', motivo='Otra vez', origen='fondo')
        self.assertEqual(uno("SELECT COUNT(*) FROM movimientos_fondo WHERE referencia = 'anulacion-venta-5'"), 1)

    def test_41_una_venta_a_credito_con_abonos_no_se_cancela(self):
        self.post('/ventas/cancelar/2', motivo='Quiero cancelarla', origen='ninguno')
        self.assertEqual(uno('SELECT estado FROM ventas WHERE id = 2'), 'Completada')

    def test_42_una_venta_antigua_se_cancela_sin_mover_dinero(self):
        fondo_antes = self.saldo('fondo')
        self.post('/ventas/cancelar/1', motivo='Venta de enero mal hecha', origen='ninguno')
        self.assertEqual(uno('SELECT estado FROM ventas WHERE id = 1'), 'Cancelada')
        self.assertEqual(self.saldo('fondo'), fondo_antes)

    def test_43_el_usuario_normal_no_puede_cancelar(self):
        otro = aplicacion.app.test_client()
        otro.post('/login', data={'username': 'cajera', 'password': 'Cajera12345'})
        id_venta = uno("SELECT id FROM ventas WHERE estado = 'Completada' ORDER BY id DESC LIMIT 1")
        otro.post(f'/ventas/cancelar/{id_venta}', data={'motivo': 'No debería poder', 'origen': 'caja'})
        self.assertEqual(uno('SELECT estado FROM ventas WHERE id = ?', id_venta), 'Completada')

    # ---- permisos y seguridad ----
    def test_50_permisos_del_usuario_normal(self):
        otro = aplicacion.app.test_client()
        otro.post('/login', data={'username': 'cajera', 'password': 'Cajera12345'})
        for url in ('/fondo', '/cuentas-por-pagar', '/reportes/financiero', '/reportes/libro', '/usuarios', '/auditoria',
                    '/facturacion/configuracion', '/proveedores', '/papelera'):
            with self.subTest(url=url):
                self.assertEqual(otro.get(url).status_code, 302, f'{url} debería estar vedada')
        for url in ('/', '/caja', '/ventas', '/cuentas-por-cobrar', '/gastos', '/reportes/ventas', '/facturacion/documentos'):
            with self.subTest(url=url):
                self.assertEqual(otro.get(url).status_code, 200)

    def test_51_hay_que_iniciar_sesion(self):
        anonimo = aplicacion.app.test_client()
        for url in ('/', '/caja', '/ventas', '/uploads/x.pdf', '/api/pacientes/buscar?q=a', '/nit/1234567'):
            with self.subTest(url=url):
                self.assertEqual(anonimo.get(url).status_code, 302)

    def test_52_csrf(self):
        import re
        aplicacion.app.config['WTF_CSRF_ENABLED'] = True
        try:
            nuevo = aplicacion.app.test_client()
            pagina = nuevo.get('/login').data.decode()
            token = re.search(r'name="csrf_token" value="([^"]+)"', pagina).group(1)
            nuevo.post('/login', data={'username': 'admin', 'password': 'Admin12345'})            # sin token
            self.assertEqual(nuevo.get('/caja').status_code, 302, 'sin token no debe iniciar sesión')
            nuevo.post('/login', data={'username': 'admin', 'password': 'Admin12345', 'csrf_token': token})
            self.assertEqual(nuevo.get('/caja').status_code, 200)
            antes = uno('SELECT COUNT(*) FROM gastos')
            nuevo.post('/gastos/nuevo', data={'monto': '1', 'forma_pago': 'fondo', 'id_categoria': '1', 'descripcion': 'sin token', 'fecha_gasto': fecha_gasto_pasada()})
            self.assertEqual(uno('SELECT COUNT(*) FROM gastos'), antes, 'un POST sin token no debe registrar nada')
        finally:
            aplicacion.app.config['WTF_CSRF_ENABLED'] = False

    def test_53_bloqueo_por_intentos_fallidos(self):
        c = aplicacion.app.test_client()
        for _ in range(5):
            c.post('/login', data={'username': 'cajera', 'password': 'mala'})
        # ahora ni con la clave buena
        r = c.post('/login', data={'username': 'cajera', 'password': 'Cajera12345'})
        self.assertEqual(r.status_code, 200, 'la cuenta debe estar bloqueada')
        consulta_ = consulta("SELECT bloqueado_hasta FROM usuarios WHERE username = 'cajera'")
        self.assertTrue(consulta_[0][0])
        sqlite3.connect(os.environ['CLINICA_DB']).execute("UPDATE usuarios SET bloqueado_hasta = NULL, intentos_fallidos = 0").connection.commit()

    def test_53b_aviso_de_contrasena_predeterminada(self):
        conn = sqlite3.connect(os.environ['CLINICA_DB'])
        conn.execute("INSERT INTO usuarios (username, password, nombre, rol) VALUES ('viejo', ?, 'Viejo', 'admin')", (generate_password_hash('46663627'),))
        conn.commit(); conn.close()
        c = aplicacion.app.test_client()
        r = c.post('/login', data={'username': 'viejo', 'password': '46663627'})
        self.assertEqual(r.status_code, 302)
        self.assertIn('/usuarios', r.headers['Location'])
        self.assertIn('por defecto', c.get('/usuarios').data.decode())

    def test_54_redireccion_abierta_bloqueada(self):
        c = aplicacion.app.test_client()
        r = c.post('/login?next=https://malo.example/', data={'username': 'admin', 'password': 'Admin12345'})
        self.assertNotIn('malo.example', r.headers['Location'])

    def test_55_no_se_borra_para_siempre_un_paciente_con_historial(self):
        self.post('/pacientes/eliminar/1')
        self.assertEqual(uno('SELECT is_deleted FROM pacientes WHERE id = 1'), 1)
        self.post('/pacientes/eliminar-permanente/1')
        self.assertEqual(uno('SELECT COUNT(*) FROM pacientes WHERE id = 1'), 1, 'tiene ventas: no se puede borrar')
        self.post('/pacientes/restaurar/1')

    def test_56_subida_de_archivos_peligrosos(self):
        import io
        conn = sqlite3.connect(os.environ['CLINICA_DB'])
        conn.execute("INSERT INTO expedientes (id_paciente, fecha_expediente) VALUES (1, '2026-01-01')"); conn.commit(); conn.close()
        id_exp = uno('SELECT MAX(id_expediente) FROM expedientes')
        self.cli.post(f'/documento/subir/{id_exp}', data={'documento': [(io.BytesIO(b'<script>alert(1)</script>'), 'malo.html'), (io.BytesIO(b'%PDF-1.4'), 'bueno.pdf')]},
                          content_type='multipart/form-data')
        nombres = [d['nombre_archivo'] for d in consulta('SELECT nombre_archivo FROM documentos WHERE id_expediente = ?', id_exp)]
        self.assertEqual(len(nombres), 1)
        self.assertTrue(nombres[0].endswith('bueno.pdf'))
        self.assertTrue(os.path.exists(os.path.join(aplicacion.app.config['UPLOAD_FOLDER'], nombres[0])))

    def test_57_nit_del_paciente(self):
        self.post('/pacientes/nuevo', nombre='Mario', apellido='Soto', nit='1234567-8', telefono='', fecha_nacimiento='')
        self.assertEqual(uno("SELECT nit FROM pacientes WHERE nombre = 'Mario'"), '12345678')
        self.post('/pacientes/nuevo', nombre='Mala', apellido='Persona', nit='12ab', telefono='', fecha_nacimiento='')
        self.assertEqual(uno("SELECT COUNT(*) FROM pacientes WHERE nombre = 'Mala'"), 0)

    # ---- reportes y conciliación ----
    def test_60_la_conciliacion_cuadra_tras_todo_lo_anterior(self):
        conn = sqlite3.connect(os.environ['CLINICA_DB']); conn.row_factory = sqlite3.Row
        try:
            revisiones = fin.conciliar_dinero(conn)
        finally:
            conn.close()
        fallas = [(r['titulo'], r['detalles']) for r in revisiones if not r['ok']]
        self.assertEqual(fallas, [])

    def test_61_estado_de_resultados(self):
        from datetime import date
        conn = sqlite3.connect(os.environ['CLINICA_DB']); conn.row_factory = sqlite3.Row
        try:
            er = fin.estado_de_resultados(conn, date(2026, 1, 1), date(2030, 12, 31))
        finally:
            conn.close()
        # Ventas no canceladas: 300 (cancelada) no cuenta; 400 crédito + 50 efectivo + 300 tarjeta + 200 transferencia + 200 crédito = 1150
        self.assertEqual(er['ventas'], Decimal('1150.00'))
        self.assertEqual(er['gastos_total'], Decimal('160.00'))        # 100 del fondo + 60 a crédito; el de 80 se anuló

    def test_62_los_reportes_aceptan_filtros_raros(self):
        for url in ('/reportes/ventas?desde=basura&hasta=', '/fondo?desde=2026-13-45', '/reportes/financiero?desde=2030-01-01&hasta=2020-01-01',
                    '/ventas?q=%27%3B+DROP+TABLE+ventas%3B--&fecha=ayer&metodo=x&estado=y&pagina=9999', '/cuentas-por-cobrar?q=%25&estado=zzz'):
            with self.subTest(url=url):
                self.assertEqual(self.cli.get(url).status_code, 200)
        self.assertEqual(uno('SELECT COUNT(*) FROM ventas') > 0, True)

    def test_63_exportaciones(self):
        for url in ('/reportes/ventas?export=csv', '/reportes/productos?export=csv', '/reportes/inventario?export=csv',
                    '/reportes/financiero?export=csv', '/reportes/libro?export=csv', '/inventario/exportar-xlsx'):
            with self.subTest(url=url):
                r = self.cli.get(url)
                self.assertEqual(r.status_code, 200)
                self.assertGreater(len(r.data), 50)

    # ---- facturación electrónica ----
    def test_70_facturacion_electronica(self):
        r = self.post('/facturacion/configuracion', ambiente='test', nit='1234567', razon_social='Clínica de Prueba, S.A.', usuario_digifact='USUARIO',
                      password_digifact='secreta', codigo_establecimiento='1', direccion='3a calle 4-56 zona 1', codigo_postal='01001',
                      municipio='Guatemala', departamento='Guatemala', activo='on')
        self.assertEqual(r.status_code, 302)
        config = consulta('SELECT * FROM config_fiscal')[0]
        self.assertEqual(config['activo'], 1)
        self.assertNotIn('secreta', config['password_cifrada'], 'la contraseña no se guarda en claro')

        venta = uno("SELECT id FROM ventas WHERE metodo_pago = 'Transferencia' AND estado = 'Completada' LIMIT 1")
        capturado = {}

        class Respuesta:
            def __init__(self, status, datos):
                self.status_code, self._datos = status, datos
            def json(self):
                return self._datos

        def falso_post(url, json=None, **kw):
            return Respuesta(200, {'Token': 'tok'})

        def falso_request(metodo, url, params=None, json=None, headers=None, timeout=None):
            capturado['url'], capturado['payload'], capturado['params'] = url, json, params
            if url.endswith('/CancelFelGT'):
                return Respuesta(200, {'Codigo': 1, 'ResponseDATA1': base64.b64encode(b'<anulado/>').decode(), 'ResponseDATA3': base64.b64encode(b'%PDF').decode()})
            return Respuesta(200, {'code': 1, 'authNumber': 'AUTH-1', 'batch': 'ABC', 'serial': '123',
                                   'responseData1': base64.b64encode(b'<xml/>').decode(), 'responseData3': base64.b64encode(b'%PDF').decode()})

        with mock.patch('requests.post', falso_post), mock.patch('requests.request', falso_request):
            self.post(f'/ventas/{venta}/facturar', nit_receptor='7654321-K', nombre_receptor='Cliente SAT')
            factura = consulta('SELECT * FROM facturas WHERE id_venta = ?', venta)[0]
            self.assertEqual(factura['estado'], 'certificado')
            self.assertEqual(factura['numero_autorizacion'], 'AUTH-1')
            payload = capturado['payload']
            self.assertEqual(payload['Header']['DocType'], 'FACT')
            self.assertEqual(payload['Buyer']['TaxID'], '7654321K')
            self.assertEqual(payload['Seller']['TaxID'], '1234567')
            self.assertEqual(payload['Items'][0]['Type'], 'Bien')
            total_items = round(sum(i['Totals']['TotalItem'] for i in payload['Items']), 2)
            self.assertEqual(total_items, payload['Totals']['GrandTotal']['InvoiceTotal'])
            iva = round(sum(i['Taxes']['Tax'][0]['Amount'] for i in payload['Items']), 2)
            self.assertAlmostEqual(iva, round(200 - 200 / 1.12, 2), places=2)
            self.assertTrue(payload['Header']['IssuedDateTime'].endswith('-06:00'))

            # Ya facturada: no se puede emitir otra vez
            self.post(f'/ventas/{venta}/facturar')
            self.assertEqual(uno('SELECT COUNT(*) FROM facturas WHERE id_venta = ?', venta), 1)
            # No se puede cancelar una venta con factura vigente
            self.post(f'/ventas/cancelar/{venta}', motivo='Con factura vigente', origen='fondo')
            self.assertEqual(uno('SELECT estado FROM ventas WHERE id = ?', venta), 'Completada')
            self.assertEqual(self.cli.get(f'/venta/detalle/{venta}').status_code, 200)
            self.assertIn('Factura electrónica: Certificado', self.cli.get(f'/venta/detalle/{venta}').data.decode())
            # Descargas
            id_factura = factura['id']
            self.assertEqual(self.cli.get(f'/facturacion/documentos/{id_factura}/descargar/pdf').data, b'%PDF')
            # Anular
            self.post(f'/facturacion/documentos/{id_factura}/anular', motivo='Datos del receptor equivocados')
            self.assertEqual(uno('SELECT estado FROM facturas WHERE id = ?', id_factura), 'anulado')
            self.assertEqual(capturado['payload']['Autorizacion'], 'AUTH-1')
            self.assertEqual(capturado['payload']['IdReceptor'], '7654321K')
            self.assertIn('Anulado', self.cli.get(f'/venta/detalle/{venta}').data.decode())
            # Una venta sin factura muestra el botón y el formulario para emitirla
            otra = uno("SELECT id FROM ventas WHERE estado = 'Completada' AND monto_total > 0 AND id NOT IN (SELECT id_venta FROM facturas) LIMIT 1")
            pagina = self.cli.get(f'/venta/detalle/{otra}').data.decode()
            self.assertIn('Emitir factura', pagina)
            self.assertIn('modalFactura', pagina)

    def test_71_factura_a_consumidor_final_con_servicio_y_descuentos(self):
        venta_id = None
        with mock.patch('requests.post', lambda url, json=None, **kw: type('R', (), {'status_code': 200, 'json': lambda s: {'Token': 't'}})()):
            capturado = {}

            def falso_request(metodo, url, params=None, json=None, headers=None, timeout=None):
                capturado['payload'] = json
                return type('R', (), {'status_code': 200, 'json': lambda s: {'code': 1, 'authNumber': 'A2', 'batch': 'B', 'serial': '9',
                                                                              'responseData1': 'eG1s', 'responseData3': 'cGRm'}})()

            # Venta con servicio + producto, descuento por línea y general
            self.post('/caja/abrir', monto_inicial='0') if not uno("SELECT COUNT(*) FROM cierres_caja WHERE estado = 'Abierta'") else None
            self.venta([(3, 1, 20), (1, 3, 5)], metodo='Efectivo', descuento='10')
            venta_id = uno('SELECT MAX(id) FROM ventas')
            total = uno('SELECT monto_total FROM ventas WHERE id = ?', venta_id)
            self.assertEqual(total, (300 - 20) + (50 - 5) * 3 - 10)
            with mock.patch('requests.request', falso_request):
                self.post(f'/ventas/{venta_id}/facturar', nit_receptor='', nombre_receptor='')
            self.assertEqual(uno('SELECT estado FROM facturas WHERE id_venta = ?', venta_id), 'certificado')
            payload = capturado['payload']
            self.assertEqual(payload['Buyer']['TaxID'], 'CF')
            self.assertEqual([i['Type'] for i in payload['Items']], ['Servicio', 'Bien'])
            self.assertEqual(round(sum(i['Totals']['TotalItem'] for i in payload['Items']), 2), total)

    def test_72_falla_de_red_deja_la_factura_indeterminada(self):
        import requests
        venta_id = uno("SELECT id FROM ventas WHERE estado = 'Completada' AND monto_total > 0 AND id NOT IN (SELECT id_venta FROM facturas) ORDER BY id DESC LIMIT 1")
        with mock.patch('requests.post', lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError('sin red'))):
            self.post(f'/ventas/{venta_id}/facturar', nit_receptor='CF')
        self.assertEqual(uno('SELECT estado FROM facturas WHERE id_venta = ?', venta_id), 'indeterminado')
        # y no se reintenta sola
        self.post(f'/ventas/{venta_id}/facturar', nit_receptor='CF')
        self.assertEqual(uno('SELECT estado FROM facturas WHERE id_venta = ?', venta_id), 'indeterminado')

    def test_80_la_auditoria_registra_lo_importante(self):
        acciones = {r[0] for r in consulta('SELECT DISTINCT accion FROM auditoria')}
        for esperada in ('abrir_caja', 'cerrar_caja', 'crear_venta', 'cancelar_venta', 'crear_gasto', 'pagar_cxp', 'cobrar_cxc', 'emitir_fel', 'anular_fel', 'login'):
            self.assertIn(esperada, acciones)
        textos = ' '.join(r[0] for r in consulta('SELECT descripcion FROM auditoria'))
        self.assertNotIn('Admin12345', textos)
        self.assertNotIn('secreta', textos)


if __name__ == '__main__':
    unittest.main(verbosity=2)
