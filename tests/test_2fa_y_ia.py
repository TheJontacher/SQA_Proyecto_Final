"""Pruebas de la verificación en 2 pasos por correo, la configuración y los reportes con IA.

El correo (SMTP) y OpenAI se simulan: ninguna prueba envía correos ni gasta tokens.
    python -m unittest discover -s tests -v
"""
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from typing import ClassVar
from unittest import mock

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

TMP = os.environ.get('CLINICA_TEST_ROOT') or tempfile.mkdtemp(prefix='clinica_test_2fa_')
os.makedirs(TMP, exist_ok=True)
os.environ['CLINICA_DB'] = os.path.join(TMP, 'prueba.db')
os.environ['CLINICA_BACKUP_DIR'] = os.path.join(TMP, 'backups')
os.environ['CLINICA_ADMIN_PASSWORD'] = 'Admin12345'
os.environ['FISCAL_ENCRYPTION_KEY'] = 'WjtZ0ZfG4m9mS5y0y2o5l0tQ1w3e6r8t0y2u4i6o8pA='
os.environ['SECRET_KEY'] = 'clave-de-prueba'
os.environ.pop('DATABASE_URL', None)
os.environ.pop('FLASK_ENV', None)

import app as aplicacion  # noqa: E402
import comun  # noqa: E402
import correo  # noqa: E402
import database  # noqa: E402
import rutas_ia  # noqa: E402
from comun import hoy_local  # noqa: E402

aplicacion.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)

_raiz_temporal = os.path.realpath(TMP)
for _ruta in (os.environ['CLINICA_DB'], os.environ['CLINICA_BACKUP_DIR']):
    if os.path.commonpath((_raiz_temporal, os.path.realpath(_ruta))) != _raiz_temporal:
        raise RuntimeError(f'La ruta de prueba no es temporal: {_ruta}')
if not (
    database.DATABASE_NAME == comun.DATABASE == aplicacion.DATABASE == os.environ['CLINICA_DB']
    and database.BACKUP_DIR == os.environ['CLINICA_BACKUP_DIR']
):
    raise RuntimeError('La aplicación y sus módulos no comparten la configuración temporal de base de datos.')

PERIODO = {'desde': (hoy_local() - __import__('datetime').timedelta(days=30)).isoformat(), 'hasta': hoy_local().isoformat()}


def sql(consulta, *params, escribir=False):
    conn = sqlite3.connect(os.environ['CLINICA_DB'])
    conn.row_factory = sqlite3.Row
    try:
        filas = conn.execute(consulta, params).fetchall()
        if escribir:
            conn.commit()
        return filas
    finally:
        conn.close()


class CorreosEnviados:
    """Sustituye al envío SMTP y guarda lo que se habría enviado."""
    def __init__(self):
        self.lista = []

    def __call__(self, config, destinatarios, asunto, texto, html=None):
        self.lista.append({'a': destinatarios, 'asunto': asunto, 'texto': texto, 'html': html})

    def ultimo_codigo(self):
        return re.search(r'\b(\d{6})\b', self.lista[-1]['texto']).group(1)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cli = aplicacion.app.test_client()
        sql("UPDATE usuarios SET email = NULL WHERE username = 'admin'", escribir=True)     # entra sin código
        r = cls.cli.post('/login', data={'username': 'admin', 'password': 'Admin12345'})
        assert r.status_code == 302, r.data
        # Con el correo saliente configurado, quien no tiene correo es enviado a registrarlo: el admin ya tiene el suyo.
        sql("UPDATE usuarios SET email = 'admin@example.com', email_verificado = 1 WHERE username = 'admin'", escribir=True)

    def setUp(self):
        self.correos = CorreosEnviados()
        self.parches = [mock.patch('correo.enviar_correo', self.correos), mock.patch('rutas_ia.enviar_correo', self.correos)]
        for p in self.parches:
            p.start()

    def tearDown(self):
        for p in self.parches:
            p.stop()

    def configurar_correo(self):
        r = self.cli.post('/configuracion/correo', data={
            'servidor': 'smtp.gmail.com', 'puerto': '587', 'seguridad': 'starttls', 'usuario': 'envia@example.com',
            'password': 'abcd efgh ijkl mnop', 'remitente_nombre': 'Clínica'})
        self.assertEqual(r.status_code, 302)


class Test1Configuracion(Base):
    def test_01_las_claves_se_guardan_cifradas(self):
        self.configurar_correo()
        guardada = sql("SELECT password_cifrada FROM config_correo")[0][0]
        self.assertNotIn('abcdefghijklmnop', guardada)
        self.assertNotIn('abcd', guardada)
        self.cli.post('/configuracion/ia', data={'modelo': 'gpt-4o-mini', 'modelo_otro': 'gpt-4.1-nano', 'token': 'sk-prueba-1234567890abcdef'})
        fila = sql("SELECT modelo, token_cifrado FROM config_ia")[0]
        self.assertEqual(fila['modelo'], 'gpt-4.1-nano')
        self.assertNotIn('sk-prueba', fila['token_cifrado'])
        pagina = self.cli.get('/configuracion').data.decode()
        self.assertNotIn('sk-prueba', pagina)
        self.assertNotIn('abcdefghijklmnop', pagina)
        # Guardar de nuevo sin escribir el token conserva el anterior
        self.cli.post('/configuracion/ia', data={'modelo': 'gpt-4o', 'modelo_otro': '', 'token': ''})
        self.assertEqual(sql("SELECT token_cifrado FROM config_ia")[0][0], fila['token_cifrado'])
        self.assertEqual(sql("SELECT modelo FROM config_ia")[0][0], 'gpt-4o')

    def test_02_modelo_invalido_rechazado(self):
        self.cli.post('/configuracion/ia', data={'modelo': 'gpt 4; drop', 'modelo_otro': '', 'token': ''})
        self.assertEqual(sql("SELECT modelo FROM config_ia")[0][0], 'gpt-4o')

    def test_03_correo_de_prueba(self):
        self.configurar_correo()
        self.cli.post('/configuracion/correo/probar', data={'destino': 'alguien@example.com'})
        self.assertEqual(self.correos.lista[-1]['a'], 'alguien@example.com')

    def test_03b_sin_probar_no_se_exige_el_codigo(self):
        """Un SMTP mal configurado no debe dejar a nadie fuera: el 2FA espera a que un envío de prueba salga bien."""
        self.configurar_correo()
        self.assertIsNone(sql("SELECT probado_en FROM config_correo")[0][0])
        sql("INSERT OR IGNORE INTO usuarios (username, password, nombre, rol, email) VALUES ('conmail', ?, 'Con Mail', 'usuario', 'cm@example.com')",
            aplicacion.generate_password_hash('Clave12345'), escribir=True)
        c = aplicacion.app.test_client()
        r = c.post('/login', data={'username': 'conmail', 'password': 'Clave12345'})
        self.assertNotIn('verificar', r.headers['Location'])
        self.assertEqual(self.correos.lista, [])
        self.assertEqual(c.get('/').status_code, 200)
        # Tras un envío de prueba correcto, queda activo
        self.cli.post('/configuracion/correo/probar', data={'destino': 'x@example.com'})
        self.assertTrue(sql("SELECT probado_en FROM config_correo")[0][0])
        r = aplicacion.app.test_client().post('/login', data={'username': 'conmail', 'password': 'Clave12345'})
        self.assertIn('/login/verificar', r.headers['Location'])
        sql("DELETE FROM usuarios WHERE username = 'conmail'", escribir=True)

    def test_04_solo_admin_entra_a_configuracion_e_ia(self):
        sql("INSERT INTO usuarios (username, password, nombre, rol) VALUES ('cajera2', ?, 'Cajera', 'usuario')",
            aplicacion.generate_password_hash('Cajera12345'), escribir=True)
        otro = aplicacion.app.test_client()
        otro.post('/login', data={'username': 'cajera2', 'password': 'Cajera12345'})
        for url in ('/configuracion', '/reportes/ia'):
            self.assertEqual(otro.get(url).status_code, 302, url)


class Test2Verificacion2Pasos(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        r = cls.cli.post('/configuracion/correo', data={
            'servidor': 'smtp.gmail.com', 'puerto': '587', 'seguridad': 'starttls', 'usuario': 'envia@example.com',
            'password': 'abcd efgh ijkl mnop', 'remitente_nombre': 'Clínica'})
        assert r.status_code == 302
        sql("UPDATE config_correo SET probado_en = '2026-01-01 00:00:00'", escribir=True)     # el envío de prueba ya salió bien

    def crear_bryan(self):
        if not sql("SELECT 1 FROM usuarios WHERE username = 'Bryan'"):
            r = self.cli.post('/usuarios/nuevo', data={'nombre': 'Bryan', 'username': 'Bryan', 'password': '46663627',
                                                        'rol': 'admin', 'email': 'Bryan1AntonioTorres@gmail.com'})
            self.assertEqual(r.status_code, 302)

    def test_10_crear_usuario_exige_correo_valido(self):
        for correo_malo in ('', 'sin-arroba', 'a@b'):
            self.cli.post('/usuarios/nuevo', data={'nombre': 'X', 'username': 'x1', 'password': 'Clave12345', 'rol': 'usuario', 'email': correo_malo})
            self.assertFalse(sql("SELECT 1 FROM usuarios WHERE username = 'x1'"), correo_malo)

    def test_11_correo_guardado_en_minusculas_y_no_se_repite(self):
        self.crear_bryan()
        self.assertEqual(sql("SELECT email FROM usuarios WHERE username = 'Bryan'")[0][0], 'bryan1antoniotorres@gmail.com')
        self.cli.post('/usuarios/nuevo', data={'nombre': 'Otro', 'username': 'otro', 'password': 'Clave12345', 'rol': 'usuario', 'email': 'BRYAN1antoniotorres@gmail.com'})
        self.assertFalse(sql("SELECT 1 FROM usuarios WHERE username = 'otro'"))

    def test_12_login_pide_codigo_y_no_abre_sesion_antes(self):
        self.crear_bryan()
        c = aplicacion.app.test_client()
        r = c.post('/login', data={'username': 'Bryan', 'password': '46663627'})
        self.assertEqual(r.status_code, 302)
        self.assertIn('/login/verificar', r.headers['Location'])
        self.assertEqual(len(self.correos.lista), 1)
        self.assertEqual(self.correos.lista[0]['a'], 'bryan1antoniotorres@gmail.com')
        # Sin código no hay acceso a nada
        self.assertEqual(c.get('/usuarios').status_code, 302)
        self.assertIn('/login', c.get('/usuarios').headers['Location'])
        # Código equivocado
        mal = '000000' if self.correos.ultimo_codigo() != '000000' else '111111'
        r = c.post('/login/verificar', data={'codigo': mal})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(c.get('/').status_code, 302)
        # Código correcto
        r = c.post('/login/verificar', data={'codigo': self.correos.ultimo_codigo()})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(c.get('/usuarios').status_code, 200)       # ya hay sesión (Bryan es administrador)
        self.assertEqual(c.get('/').status_code, 200)
        self.assertEqual(sql("SELECT email_verificado FROM usuarios WHERE username = 'Bryan'")[0][0], 1)

    def test_13_el_codigo_se_usa_una_sola_vez(self):
        self.crear_bryan()
        c = aplicacion.app.test_client()
        c.post('/login', data={'username': 'Bryan', 'password': '46663627'})
        codigo = self.correos.ultimo_codigo()
        self.assertEqual(c.post('/login/verificar', data={'codigo': codigo}).status_code, 302)
        otro = aplicacion.app.test_client()
        otro.post('/login', data={'username': 'Bryan', 'password': '46663627'})   # genera un código nuevo
        with otro.session_transaction() as ses:
            self.assertIn('pre2fa', ses)
        r = otro.post('/login/verificar', data={'codigo': codigo})                # el viejo ya no sirve
        self.assertEqual(r.status_code, 200)

    def test_14_cinco_fallos_invalidan_el_codigo(self):
        self.crear_bryan()
        c = aplicacion.app.test_client()
        c.post('/login', data={'username': 'Bryan', 'password': '46663627'})
        real = self.correos.ultimo_codigo()
        for _ in range(correo.MAX_INTENTOS_CODIGO):
            c.post('/login/verificar', data={'codigo': '999999' if real != '999999' else '888888'})
        r = c.post('/login/verificar', data={'codigo': real})                      # ni el correcto sirve ya
        self.assertEqual(r.status_code, 200)
        self.assertEqual(c.get('/').status_code, 302)

    def test_15_codigo_vencido(self):
        self.crear_bryan()
        c = aplicacion.app.test_client()
        c.post('/login', data={'username': 'Bryan', 'password': '46663627'})
        sql("UPDATE codigos_verificacion SET expira_en = '2000-01-01 00:00:00' WHERE usado = 0", escribir=True)
        r = c.post('/login/verificar', data={'codigo': self.correos.ultimo_codigo()})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(c.get('/').status_code, 302)

    def test_16_el_codigo_no_se_guarda_en_claro(self):
        self.crear_bryan()
        c = aplicacion.app.test_client()
        c.post('/login', data={'username': 'Bryan', 'password': '46663627'})
        codigo = self.correos.ultimo_codigo()
        self.assertNotIn(codigo, [f[0] for f in sql("SELECT codigo_hash FROM codigos_verificacion")])

    def test_17_contrasena_incorrecta_no_envia_codigo(self):
        self.crear_bryan()
        antes = len(self.correos.lista)
        c = aplicacion.app.test_client()
        c.post('/login', data={'username': 'Bryan', 'password': 'mala-clave'})
        self.assertEqual(len(self.correos.lista), antes)

    def test_18_reenvio_respeta_la_espera(self):
        self.crear_bryan()
        c = aplicacion.app.test_client()
        c.post('/login', data={'username': 'Bryan', 'password': '46663627'})
        enviados = len(self.correos.lista)
        c.post('/login/reenviar')
        self.assertEqual(len(self.correos.lista), enviados)         # aún no pasan 60 s

    def test_19_si_falla_el_envio_no_hay_acceso(self):
        self.crear_bryan()
        with mock.patch('correo.enviar_correo', side_effect=correo.CorreoError('SMTP caído.')):
            c = aplicacion.app.test_client()
            r = c.post('/login', data={'username': 'Bryan', 'password': '46663627'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(c.get('/').status_code, 302)

    def test_20_usuario_sin_correo_entra_y_debe_registrarlo(self):
        sql("INSERT INTO usuarios (username, password, nombre, rol) VALUES ('viejo2', ?, 'Viejo', 'usuario')",
            aplicacion.generate_password_hash('Viejo12345'), escribir=True)
        c = aplicacion.app.test_client()
        r = c.post('/login', data={'username': 'viejo2', 'password': 'Viejo12345'})
        self.assertIn('/', r.headers['Location'])
        r = c.get('/pacientes')
        self.assertEqual(r.status_code, 302)
        self.assertIn('/cuenta/correo', r.headers['Location'])
        # Registra su correo: el código llega a ese correo y solo entonces se guarda
        c.post('/cuenta/correo', data={'email': 'viejo2@example.com'})
        self.assertEqual(self.correos.lista[-1]['a'], 'viejo2@example.com')
        self.assertIsNone(sql("SELECT email FROM usuarios WHERE username = 'viejo2'")[0][0])
        c.post('/cuenta/correo/confirmar', data={'codigo': self.correos.ultimo_codigo()})
        self.assertEqual(sql("SELECT email FROM usuarios WHERE username = 'viejo2'")[0][0], 'viejo2@example.com')
        self.assertEqual(c.get('/pacientes').status_code, 200)


class Test3ReportesIA(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cli.post('/configuracion/correo', data={'servidor': 'smtp.gmail.com', 'puerto': '587', 'seguridad': 'starttls',
                                                    'usuario': 'envia@example.com', 'password': 'abcdefghijklmnop'})
        cls.cli.post('/configuracion/ia', data={'modelo': 'gpt-4o-mini', 'token': 'sk-prueba-1234567890abcdef'})
        conn = sqlite3.connect(os.environ['CLINICA_DB'])
        conn.execute("INSERT INTO pacientes (nombre, apellido) VALUES ('Paciente', 'Secreto')")
        conn.execute("INSERT INTO inventario (nombre, tipo, precio_venta, precio_compra, cantidad) VALUES ('Crema X', 'Producto', 50, 20, 10)")
        conn.execute("INSERT INTO ventas (id_paciente, fecha_venta, monto_total, metodo_pago, estado_pago, estado) "
                     "VALUES (1, datetime('now'), 100, 'Efectivo', 'Pagado', 'Completada')")
        conn.execute("INSERT INTO ventas_detalle (id_venta, id_item_inventario, cantidad, precio_unitario, descuento, costo_unitario) VALUES (1, 1, 2, 50, 0, 20)")
        conn.commit()
        conn.close()

    def respuesta_ok(self, texto='## Resumen ejecutivo\nTodo **bien**.\n\n- Punto uno\n- Punto dos'):
        class R:
            status_code = 200
            def json(_):
                return {'choices': [{'message': {'content': texto}}]}
        return R()

    def test_30_el_reporte_manda_cifras_y_nunca_datos_de_pacientes(self):
        with mock.patch('rutas_ia.requests.post', return_value=self.respuesta_ok()) as post:
            r = self.cli.post('/reportes/ia/generar', data={**PERIODO})
        self.assertEqual(r.status_code, 302)
        llamada = post.call_args
        self.assertEqual(llamada.kwargs['json']['model'], 'gpt-4o-mini')
        self.assertEqual(llamada.kwargs['headers']['Authorization'], 'Bearer sk-prueba-1234567890abcdef')
        enviado = str(llamada.kwargs['json']['messages'])
        self.assertIn('Crema X', enviado)
        self.assertIn('100.00', enviado)
        self.assertNotIn('Secreto', enviado)
        self.assertNotIn('Paciente', enviado)
        self.assertEqual(len(sql("SELECT * FROM reportes_ia")), 1)
        pagina = self.cli.get(r.headers['Location']).data.decode()
        self.assertIn('<strong', pagina)
        self.assertIn('Punto uno', pagina)

    def test_31_enviar_por_correo(self):
        with mock.patch('rutas_ia.requests.post', return_value=self.respuesta_ok()):
            self.cli.post('/reportes/ia/generar', data={**PERIODO, 'enviar': 'on',
                                                        'destinatarios': 'dueno@example.com, otro@example.com'})
        self.assertEqual(self.correos.lista[-1]['a'], ['dueno@example.com', 'otro@example.com'])
        self.assertIn('Todo', self.correos.lista[-1]['texto'])
        self.assertIn('<strong', self.correos.lista[-1]['html'])
        self.assertEqual(sql("SELECT enviado_a FROM reportes_ia ORDER BY id DESC LIMIT 1")[0][0], 'dueno@example.com, otro@example.com')

    def test_32_destinatario_invalido_no_llama_a_la_ia(self):
        with mock.patch('rutas_ia.requests.post') as post:
            self.cli.post('/reportes/ia/generar', data={**PERIODO, 'enviar': 'on', 'destinatarios': 'malo'})
        post.assert_not_called()

    def test_33_error_de_openai_se_explica_y_no_guarda(self):
        antes = len(sql("SELECT * FROM reportes_ia"))

        class R:
            status_code = 401
            def json(_):
                return {'error': {'message': 'Incorrect API key provided: sk-prueba-1234567890abcdef'}}
        with mock.patch('rutas_ia.requests.post', return_value=R()):
            r = self.cli.post('/reportes/ia/generar', data={**PERIODO}, follow_redirects=True)
        self.assertEqual(len(sql("SELECT * FROM reportes_ia")), antes)
        pagina = r.data.decode()
        self.assertIn('rechazó el token', pagina)
        self.assertNotIn('sk-prueba', pagina)

    def test_34_el_markdown_se_escapa(self):
        html = rutas_ia.markdown_a_html('## Título\n<script>alert(1)</script> **negrita**\n\n| a | b |\n|---|---|\n| 1 | <b>2</b> |')
        self.assertNotIn('<script>', html)
        self.assertNotIn('<b>2</b>', html)
        self.assertIn('<strong>negrita</strong>', html)
        self.assertIn('<table', html)

    def test_35_el_token_no_queda_en_la_auditoria(self):
        textos = ' '.join(f[0] for f in sql("SELECT descripcion FROM auditoria"))
        self.assertNotIn('sk-prueba', textos)
        self.assertNotIn('abcdefghijklmnop', textos)


class Test4PequenoContribuyente(Base):
    CONFIG: ClassVar[dict[str, str | int]] = {'afiliacion_iva': 'PEQ', 'nit': '1234567', 'codigo_establecimiento': 1, 'tipo_frase': 1, 'escenario_frase': 1,
                                              'direccion': 'Calle 1', 'codigo_postal': '01001', 'municipio': 'Guatemala', 'departamento': 'Guatemala'}

    def payload(self, afiliacion, descuento=0):
        from digifact import calcular_lineas_fact, construir_payload_fact
        lineas, total = calcular_lineas_fact([{'description': 'Crema', 'unit': 'UNI', 'qty': 2, 'unit_price': 56, 'unit_discount': 0, 'servicio': False}],
                                             descuento, con_iva=afiliacion != 'PEQ')
        cfg = {**self.CONFIG, 'afiliacion_iva': afiliacion}
        return construir_payload_fact(config=cfg, emitido_en='2026-10-17T10:00:00-06:00', nombre_emisor='Clínica', nombre_sucursal=None,
                                      nit_receptor='CF', nombre_receptor=None, lineas=lineas, total=total)

    def test_40_pequeno_contribuyente_sin_iva_y_como_FPEQ(self):
        p = self.payload('PEQ')
        self.assertEqual(p['Header']['DocType'], 'FPEQ')
        self.assertEqual(p['Seller']['TaxIDAdditionalInfo'][0]['Value'], 'PEQ')
        frase = {i['Name']: i['Value'] for i in p['Seller']['AdditionlInfo']}
        self.assertEqual(frase, {'TipoFrase': '3', 'Escenario': '1'})       # Digifact: "FPEQ debe incluir la FRASE (3)"
        impuesto = p['Items'][0]['Taxes']['Tax'][0]                          # Digifact exige el nodo, con IVA en cero
        self.assertEqual((impuesto['TaxableAmount'], impuesto['Amount']), (112.0, 0.0))
        self.assertEqual(p['Totals']['TotalTaxes']['TotalTax'][0]['Amount'], 0.0)
        self.assertEqual(p['Items'][0]['Totals']['TotalItem'], 112.0)
        self.assertEqual(p['Totals']['GrandTotal']['InvoiceTotal'], 112.0)

    def test_41_regimen_general_no_cambia(self):
        p = self.payload('GEN')
        self.assertEqual(p['Header']['DocType'], 'FACT')
        self.assertEqual(p['Items'][0]['Taxes']['Tax'][0]['Amount'], 12.0)
        self.assertEqual(p['Totals']['TotalTaxes']['TotalTax'][0]['Amount'], 12.0)
        self.assertIn('AdditionlInfo', p['Seller'])

    def test_42_con_descuento_el_total_cuadra(self):
        p = self.payload('PEQ', descuento=12)
        self.assertEqual(p['Totals']['GrandTotal']['InvoiceTotal'], 100.0)
        self.assertEqual(p['Items'][0]['Totals']['TotalItem'], 100.0)

    def test_43_se_guarda_desde_la_pantalla(self):
        datos = {'ambiente': 'test', 'nit': '1234567', 'razon_social': 'Mi Clínica', 'usuario_digifact': 'u1', 'password_digifact': 'x1y2z3',
                 'codigo_establecimiento': '1', 'tipo_frase': '1', 'escenario_frase': '1'}
        self.cli.post('/facturacion/configuracion', data={**datos, 'afiliacion_iva': 'PEQ'})
        self.assertEqual(sql("SELECT afiliacion_iva, tipo_frase, escenario_frase FROM config_fiscal")[0][:], ('PEQ', 3, 1))
        self.assertIn('value="PEQ" selected', self.cli.get('/facturacion/configuracion').data.decode())
        self.cli.post('/facturacion/configuracion', data={**datos, 'afiliacion_iva': 'XXX'})
        self.assertEqual(sql("SELECT afiliacion_iva FROM config_fiscal")[0][0], 'PEQ')      # inválido: no cambia
        self.cli.post('/facturacion/configuracion', data={**datos, 'afiliacion_iva': 'GEN'})
        self.assertEqual(sql("SELECT afiliacion_iva FROM config_fiscal")[0][0], 'GEN')


class Test5NitEnPacientes(Base):
    def test_50_separar_nombre_de_sat(self):
        from digifact import separar_nombre
        self.assertEqual(separar_nombre('PEREZ LOPEZ,JUAN CARLOS'), ('Juan Carlos', 'Perez Lopez'))
        self.assertEqual(separar_nombre('JUAN CARLOS PEREZ LOPEZ'), ('Juan Carlos', 'Perez Lopez'))
        self.assertEqual(separar_nombre('ANA GOMEZ'), ('Ana', 'Gomez'))
        self.assertEqual(separar_nombre('PEÑA, JOSÉ'), ('José', 'Peña'))

    def test_51_la_consulta_devuelve_nombres_y_apellidos(self):
        sql("UPDATE config_fiscal SET activo = 1", escribir=True)
        cliente = mock.Mock()
        cliente.lookup_nit.return_value = {'RESPONSE': [{'NOMBRE': 'MENDEZ RUIZ,LUIS ANTONIO'}]}
        with mock.patch('rutas_facturacion.crear_cliente', return_value=cliente):
            datos = self.cli.get('/nit/9876543').get_json()
        self.assertEqual((datos['nombres'], datos['apellidos']), ('Luis Antonio', 'Mendez Ruiz'))
        self.assertEqual(datos['nombre'], 'MENDEZ RUIZ,LUIS ANTONIO')

    def test_52_el_formulario_de_paciente_llena_ambas_casillas(self):
        pagina = self.cli.get('/pacientes').data.decode()
        self.assertIn("buscarNitEn('nit', 'nombre', this, 'estado-nit', 'apellido')", pagina)


if __name__ == '__main__':
    unittest.main()
