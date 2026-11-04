import importlib.util
import io
import os
import pathlib
import re
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from datetime import date

TEST_ROOT = os.environ.get("CLINICA_TEST_ROOT") or tempfile.mkdtemp(prefix="clinica_test_regressions_")
os.makedirs(TEST_ROOT, exist_ok=True)
os.environ["CLINICA_DB"] = str(pathlib.Path(TEST_ROOT) / "bootstrap.sqlite")
os.environ["CLINICA_BACKUP_DIR"] = str(pathlib.Path(TEST_ROOT) / "backups")
os.environ["CLINICA_ADMIN_PASSWORD"] = "isolated-test-admin-password"
os.environ["SECRET_KEY"] = "clave-de-prueba"
os.environ["FISCAL_ENCRYPTION_KEY"] = "WjtZ0ZfG4m9mS5y0y2o5l0tQ1w3e6r8t0y2u4i6o8pA="
os.environ.pop("DATABASE_URL", None)
os.environ.pop("FLASK_ENV", None)

import app as clinic_app  # noqa: E402
import comun  # noqa: E402
import database  # noqa: E402

_test_root_real = os.path.realpath(TEST_ROOT)
for _ruta in (os.environ["CLINICA_DB"], os.environ["CLINICA_BACKUP_DIR"]):
    if os.path.commonpath((_test_root_real, os.path.realpath(_ruta))) != _test_root_real:
        raise RuntimeError(f"La ruta de prueba no es temporal: {_ruta}")
if not (
    database.DATABASE_NAME == comun.DATABASE == clinic_app.DATABASE == os.environ["CLINICA_DB"]
    and database.BACKUP_DIR == os.environ["CLINICA_BACKUP_DIR"]
):
    raise RuntimeError("La aplicación y sus módulos no comparten la configuración temporal de base de datos.")


PROJECT_DIR = pathlib.Path(__file__).resolve().parents[1]
MIGRATION_PATH = PROJECT_DIR / "migrate_sqlite_to_postgres.py"


@contextmanager
def sqlite_connection(path):
    conn = sqlite3.connect(path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


class ClinicRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=TEST_ROOT)
        self.db_path = pathlib.Path(self.temp_dir.name) / "test_clinica.sqlite"
        self.previous_database_name = database.DATABASE_NAME
        self.previous_database = clinic_app.DATABASE
        self.previous_comun_database = comun.DATABASE
        self.previous_clinica_db = os.environ.get("CLINICA_DB")
        self.previous_backup_dir = os.environ.get("CLINICA_BACKUP_DIR")
        self.previous_database_backup_dir = database.BACKUP_DIR
        self.previous_admin_password = os.environ.get("CLINICA_ADMIN_PASSWORD")

        database_path = str(self.db_path)
        backup_dir = str(pathlib.Path(self.temp_dir.name) / "backups")
        os.environ["CLINICA_DB"] = database_path
        os.environ["CLINICA_BACKUP_DIR"] = backup_dir
        os.environ["CLINICA_ADMIN_PASSWORD"] = "isolated-test-admin-password"
        database.DATABASE_NAME = database_path
        database.BACKUP_DIR = backup_dir
        comun.DATABASE = database_path
        clinic_app.DATABASE = database_path
        self.assertEqual(database.DATABASE_NAME, comun.DATABASE)
        self.assertEqual(comun.DATABASE, clinic_app.DATABASE)
        temp_root = os.path.realpath(self.temp_dir.name)
        for ruta in (database_path, backup_dir):
            if os.path.commonpath((temp_root, os.path.realpath(ruta))) != temp_root:
                self.fail(f"La ruta de prueba no es temporal: {ruta}")
        self.previous_upload_config = {
            key: clinic_app.app.config[key]
            for key in ("UPLOAD_FOLDER", "PRODUCT_UPLOAD_FOLDER", "EXPENSE_UPLOAD_FOLDER")
        }
        self.upload_root = pathlib.Path(self.temp_dir.name) / "uploads"
        clinic_app.app.config["UPLOAD_FOLDER"] = str(self.upload_root)
        clinic_app.app.config["PRODUCT_UPLOAD_FOLDER"] = str(self.upload_root / "productos")
        clinic_app.app.config["EXPENSE_UPLOAD_FOLDER"] = str(self.upload_root / "comprobantes_gastos")
        for folder in (
            self.upload_root,
            pathlib.Path(clinic_app.app.config["PRODUCT_UPLOAD_FOLDER"]),
            pathlib.Path(clinic_app.app.config["EXPENSE_UPLOAD_FOLDER"]),
        ):
            folder.mkdir(parents=True, exist_ok=True)
        database.crear_base_de_datos()
        with sqlite_connection(self.db_path) as conn:
            conn.execute(
                "INSERT INTO pacientes (nombre, apellido) VALUES ('Paciente', 'Ficticio')"
            )

        self.client = clinic_app.app.test_client()
        login_page = self.client.get("/login")
        csrf_match = re.search(
            r'name="csrf_token" value="([^"]+)"',
            login_page.get_data(as_text=True),
        )
        self.assertIsNotNone(csrf_match)
        self.csrf_token = csrf_match.group(1)
        response = self.client.post(
            "/login",
            data={
                "username": "admin",
                "password": "isolated-test-admin-password",
                "csrf_token": self.csrf_token,
            },
        )
        self.assertEqual(response.status_code, 302)

    def tearDown(self):
        clinic_app.DATABASE = self.previous_database
        comun.DATABASE = self.previous_comun_database
        database.DATABASE_NAME = self.previous_database_name
        database.BACKUP_DIR = self.previous_database_backup_dir
        clinic_app.app.config.update(self.previous_upload_config)
        for key, value in (
            ("CLINICA_DB", self.previous_clinica_db),
            ("CLINICA_BACKUP_DIR", self.previous_backup_dir),
            ("CLINICA_ADMIN_PASSWORD", self.previous_admin_password),
        ):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp_dir.cleanup()

    def query(self, sql, params=()):
        with sqlite_connection(self.db_path) as conn:
            return conn.execute(sql, params).fetchall()

    def add_product(self, price=25, quantity=5):
        with sqlite_connection(self.db_path) as conn:
            conn.execute(
                "INSERT INTO inventario "
                "(nombre, tipo, precio_venta, precio_compra, cantidad, is_deleted) "
                "VALUES ('Test product', 'Producto', ?, 10, ?, 0)",
                (price, quantity),
            )

    def open_cash(self):
        return self.client.post(
            "/caja/abrir",
            data={"csrf_token": self.csrf_token, "monto_inicial": "0"},
        )

    def test_patient_expedient_session_and_protected_document_flow(self):
        response = self.client.post(
            "/pacientes/nuevo",
            data={
                "csrf_token": self.csrf_token,
                "nombre": "Paciente",
                "apellido": "Ficticio",
                "telefono": "55500001",
                "fecha_nacimiento": "1990-01-01",
            },
        )
        self.assertEqual(response.status_code, 302)
        patient_id = self.query("SELECT id FROM pacientes WHERE telefono='55500001'")[0][0]

        fields = [
            "enfermedades", "vasos_agua", "alimentos", "problemas_hormonales", "alergias",
            "menstruacion", "caida_cabello", "caspa", "horas_sueno", "productos_corporales",
            "otros_sintomas", "skincare_limpieza", "skincare_hidratacion", "skincare_ojos",
            "skincare_fotoproteccion", "skincare_noche", "skincare_tratamiento",
            "sustancias_ingeridas", "skincare_nuevo_limpieza", "skincare_nuevo_hidratacion",
            "skincare_nuevo_ojos", "skincare_nuevo_fotoproteccion", "skincare_nuevo_noche",
            "skincare_nuevo_tratamiento",
        ]
        form = {
            "csrf_token": self.csrf_token,
            "p_nombre": "Paciente",
            "p_apellido": "Ficticio",
            "p_fecha_nacimiento": "1990-01-01",
            "p_telefono": "55500001",
            "fecha_expediente": "2026-10-10",
            "motivo_consulta": "Prueba ficticia",
        }
        form.update({field: "dato ficticio" for field in fields})
        response = self.client.post(
            f"/paciente/{patient_id}/expediente/nuevo",
            data=form,
        )
        self.assertEqual(response.status_code, 302)
        expediente_id = self.query(
            "SELECT id_expediente FROM expedientes WHERE id_paciente=?", (patient_id,)
        )[0][0]

        response = self.client.post(
            f"/sesion/nueva/{expediente_id}",
            data={
                "csrf_token": self.csrf_token,
                "fecha_sesion": "2026-10-10",
                "tratamiento": "Seguimiento ficticio",
                "resultados": "Sin datos reales",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.query("SELECT COUNT(*) FROM sesiones WHERE id_expediente=?", (expediente_id,)),
            [(1,)],
        )

        response = self.client.post(
            f"/documento/subir/{expediente_id}",
            data={
                "csrf_token": self.csrf_token,
                "documento": (io.BytesIO(b"%PDF-fictitious-test"), "scan.pdf"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 302)
        document_id, filename = self.query(
            "SELECT id_documento, ruta_archivo FROM documentos WHERE id_expediente=?",
            (expediente_id,),
        )[0]
        download = self.client.get(f"/uploads/{filename}")
        self.assertEqual(download.status_code, 200)
        self.assertIn("inline", download.headers.get("Content-Disposition", ""))
        download.close()
        self.assertEqual(
            clinic_app.app.test_client().get(f"/uploads/{filename}").status_code,
            302,
        )

        self.client.post(
            f"/documento/eliminar/{document_id}",
            data={"csrf_token": self.csrf_token},
        )
        self.assertEqual(
            self.query("SELECT is_deleted FROM documentos WHERE id_documento=?", (document_id,)),
            [(1,)],
        )
        self.client.post(
            f"/documento/restaurar/{document_id}",
            data={"csrf_token": self.csrf_token},
        )
        self.assertEqual(
            self.query("SELECT is_deleted FROM documentos WHERE id_documento=?", (document_id,)),
            [(0,)],
        )

    def test_cash_open_close_is_validated_and_idempotent(self):
        with sqlite_connection(self.db_path) as conn:
            conn.execute(
                "INSERT INTO movimientos_fondo (cuenta, tipo, monto, concepto, naturaleza) "
                "VALUES ('fondo', 'ingreso_manual', 50, 'Fondo ficticio de prueba', 'saldo_inicial')"
            )
        opened = self.client.post(
            "/caja/abrir",
            data={
                "csrf_token": self.csrf_token,
                "monto_inicial": "50",
                "origen_apertura": "fondo",
            },
        )
        self.assertEqual(opened.status_code, 302)
        self.client.post(
            "/caja/abrir",
            data={"csrf_token": self.csrf_token, "monto_inicial": "0"},
        )
        self.assertEqual(self.query("SELECT COUNT(*) FROM cierres_caja WHERE estado='Abierta'"), [(1,)])
        response = self.client.post(
            "/caja/cerrar",
            data={"csrf_token": self.csrf_token, "monto_contado": "50"},
        )
        self.assertEqual(response.status_code, 302)
        self.client.post(
            "/caja/cerrar",
            data={"csrf_token": self.csrf_token, "monto_contado": "50"},
        )
        self.assertEqual(self.query("SELECT estado, monto_final_real FROM cierres_caja"), [("Cerrada", 50.0)])

    def test_service_profit_report_treats_null_purchase_cost_as_zero(self):
        with sqlite_connection(self.db_path) as conn:
            conn.execute(
                "INSERT INTO inventario (nombre, tipo, precio_venta, precio_compra, cantidad, is_deleted) "
                "VALUES ('Test service', 'Servicio', 100, NULL, NULL, 0)"
            )
        self.open_cash()
        self.client.post(
            "/ventas/nueva",
            data={
                "csrf_token": self.csrf_token,
                "id_paciente": "1",
                "item_ids[]": "1",
                "quantities[]": "1",
                "discounts[]": "0",
                "descuento_general": "0",
                "metodo_pago": "Efectivo",
            },
        )
        today = date.today().isoformat()
        report = self.client.get(f"/reportes/productos?desde={today}&hasta={today}")
        self.assertEqual(report.status_code, 200)
        self.assertIn("Q100.00", report.get_data(as_text=True))

    def test_database_initializer_is_repeatable_and_preserves_data(self):
        with sqlite_connection(self.db_path) as conn:
            conn.execute("CREATE TABLE sentinel (value TEXT NOT NULL)")
            conn.execute("INSERT INTO sentinel VALUES ('keep')")
        database.crear_base_de_datos()
        self.assertEqual(self.query("SELECT value FROM sentinel"), [("keep",)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM usuarios WHERE username='admin'"), [(1,)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM pacientes"), [(1,)])

    def test_post_requests_require_csrf_and_logout_is_post_only(self):
        anonymous = clinic_app.app.test_client()
        response = anonymous.post("/login", data={"username": "admin", "password": "bad"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/")
        with anonymous.session_transaction() as session:
            self.assertNotIn("_user_id", session)
        self.assertEqual(anonymous.get("/logout").status_code, 405)
        authenticated_response = self.client.get("/")
        self.assertEqual(authenticated_response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(authenticated_response.headers["X-Frame-Options"], "SAMEORIGIN")
        self.assertEqual(authenticated_response.headers["Referrer-Policy"], "same-origin")
        csrf_rejected = self.client.post("/logout")
        self.assertEqual(csrf_rejected.status_code, 302)
        with self.client.session_transaction() as session:
            self.assertIn("_user_id", session)
        logout_response = self.client.post(
            "/logout", data={"csrf_token": self.csrf_token}
        )
        self.assertEqual(logout_response.status_code, 302)
        self.assertEqual(logout_response.location, "/login")
        with self.client.session_transaction() as session:
            self.assertNotIn("_user_id", session)

    def test_current_date_helpers_parse_and_format_expected_inputs(self):
        today = date.today()
        self.assertEqual(clinic_app.calcular_edad(today.isoformat()), 0)
        self.assertIsNone(clinic_app.calcular_edad("not-a-date"))
        formatter = clinic_app.app.jinja_env.filters["local"]
        self.assertEqual(formatter("2026-10-10 15:45:00"), "10/10/2026 09:45")

    def test_sale_stock_and_cancellation_are_consistent(self):
        self.add_product()
        self.assertEqual(self.open_cash().status_code, 302)
        sale_data = {
            "csrf_token": self.csrf_token,
            "id_paciente": "1",
            "item_ids[]": "1",
            "discounts[]": "0",
            "descuento_general": "0",
            "metodo_pago": "Efectivo",
        }
        oversale = self.client.post(
            "/ventas/nueva", data={**sale_data, "quantities[]": "6"}
        )
        self.assertEqual(oversale.status_code, 302)
        self.assertEqual(self.query("SELECT COUNT(*) FROM ventas"), [(0,)])
        self.assertEqual(self.query("SELECT cantidad FROM inventario WHERE id=1"), [(5,)])

        self.client.post(
            "/ventas/nueva", data={**sale_data, "quantities[]": "2"}
        )
        sale_id = self.query("SELECT id FROM ventas")[0][0]
        self.assertEqual(self.query("SELECT cantidad FROM inventario WHERE id=1"), [(3,)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM movimientos_inventario"), [(1,)])

        response = self.client.post(
            f"/ventas/cancelar/{sale_id}",
            data={
                "csrf_token": self.csrf_token,
                "motivo": "Cancelación ficticia para prueba",
                "origen": "caja",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.query("SELECT cantidad FROM inventario WHERE id=1"), [(5,)])
        self.assertEqual(
            self.query("SELECT estado FROM ventas WHERE id=?", (sale_id,)),
            [("Cancelada",)],
        )
        self.assertEqual(self.query("SELECT COUNT(*) FROM movimientos_inventario"), [(2,)])

    def test_credit_installments_cannot_overpay(self):
        self.add_product(price=50)
        self.assertEqual(self.open_cash().status_code, 302)
        self.client.post(
            "/ventas/nueva",
            data={
                "csrf_token": self.csrf_token,
                "id_paciente": "1",
                "item_ids[]": "1",
                "quantities[]": "1",
                "discounts[]": "0",
                "descuento_general": "0",
                "metodo_pago": "Crédito",
            },
        )
        sale_id = self.query("SELECT id FROM ventas")[0][0]
        account_id = self.query(
            "SELECT id FROM cuentas_por_cobrar WHERE id_venta=?",
            (sale_id,),
        )[0][0]
        payment = {
            "csrf_token": self.csrf_token,
            "monto": "30",
            "metodo_pago": "Efectivo",
        }
        self.client.post(f"/cuentas-por-cobrar/{account_id}/pagar", data=payment)
        self.client.post(f"/cuentas-por-cobrar/{account_id}/pagar", data=payment)
        self.assertEqual(
            self.query("SELECT saldo_pendiente FROM ventas WHERE id=?", (sale_id,)),
            [(20.0,)],
        )
        self.assertEqual(
            self.query("SELECT COUNT(*) FROM abonos WHERE id_cuenta=?", (account_id,)),
            [(1,)],
        )

    def test_expense_installments_cannot_overpay(self):
        with sqlite_connection(self.db_path) as conn:
            conn.execute(
                "INSERT INTO movimientos_fondo (cuenta, tipo, monto, concepto, naturaleza) "
                "VALUES ('fondo', 'ingreso_manual', 50, 'Fondo ficticio de prueba', 'saldo_inicial')"
            )
            provider_id = conn.execute(
                "INSERT INTO proveedores (nombre) VALUES ('Proveedor ficticio')"
            ).lastrowid
            conn.execute(
                "INSERT INTO cuentas_por_pagar "
                "(id_proveedor, descripcion, monto, saldo_pendiente, estado) "
                "VALUES (?, 'Gasto ficticio', 50, 50, 'Pendiente')",
                (provider_id,),
            )
            account_id = conn.execute(
                "SELECT id FROM cuentas_por_pagar WHERE id_proveedor=?",
                (provider_id,),
            ).fetchone()[0]
        payment = {
            "csrf_token": self.csrf_token,
            "monto": "30",
            "forma_pago": "fondo",
        }
        self.client.post(f"/cuentas-por-pagar/{account_id}/pagar", data=payment)
        self.client.post(f"/cuentas-por-pagar/{account_id}/pagar", data=payment)
        self.assertEqual(
            self.query(
                "SELECT saldo_pendiente FROM cuentas_por_pagar WHERE id=?",
                (account_id,),
            ),
            [(20.0,)],
        )
        self.assertEqual(
            self.query("SELECT COUNT(*) FROM pagos_cxp WHERE id_cuenta=?", (account_id,)),
            [(1,)],
        )

    def test_appointment_edit_rejects_occupied_time(self):
        with sqlite_connection(self.db_path) as conn:
            conn.execute("INSERT INTO pacientes (nombre, apellido) VALUES ('Test', 'Patient')")
            conn.execute(
                "INSERT INTO citas (id_paciente, fecha_cita, hora_cita, estado) "
                "VALUES (1, '2026-12-01', '09:00', 'Pendiente')"
            )
            conn.execute(
                "INSERT INTO citas (id_paciente, fecha_cita, hora_cita, estado) "
                "VALUES (2, '2026-12-01', '10:00', 'Pendiente')"
            )
        response = self.client.post(
            "/citas/editar/2",
            data={
                "csrf_token": self.csrf_token,
                "id_paciente": "2",
                "fecha_cita": "2026-12-01",
                "hora_cita": "09:00",
                "descripcion": "Conflict",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.query("SELECT hora_cita FROM citas WHERE id=2"), [("10:00",)])

    def test_appointment_creation_accepts_free_slot_and_rejects_conflict(self):
        appointment = {
            "csrf_token": self.csrf_token,
            "id_paciente": "1",
            "fecha_cita": "2026-12-01",
            "hora_cita": "09:00",
            "descripcion": "Fictional appointment",
        }
        created = self.client.post('/citas/nueva', data=appointment)
        self.assertEqual(created.status_code, 302)
        conflict = self.client.post('/citas/nueva', data=appointment, follow_redirects=True)
        self.assertEqual(conflict.status_code, 200)
        self.assertIn('Ya existe una cita agendada', conflict.get_data(as_text=True))
        self.assertEqual(self.query("SELECT COUNT(*) FROM citas WHERE fecha_cita='2026-12-01' AND hora_cita='09:00'"), [(1,)])

    def test_stock_adjustment_cannot_make_negative_or_change_service_stock(self):
        self.add_product(quantity=3)
        with sqlite_connection(self.db_path) as conn:
            conn.execute(
                "INSERT INTO inventario (nombre, tipo, precio_venta, cantidad, is_deleted) "
                "VALUES ('Test service', 'Servicio', 10, NULL, 0)"
            )
        self.client.post(
            "/inventario/stock/remover/1",
            data={"csrf_token": self.csrf_token, "cantidad_a_remover": "9"},
        )
        self.client.post(
            "/inventario/stock/agregar/2",
            data={"csrf_token": self.csrf_token, "cantidad_a_agregar": "4"},
        )
        self.assertEqual(self.query("SELECT cantidad FROM inventario WHERE id=1"), [(3,)])
        self.assertEqual(self.query("SELECT cantidad FROM inventario WHERE id=2"), [(None,)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM movimientos_inventario"), [(0,)])

    def test_trash_permanent_delete_preserves_referenced_clinical_and_stock_history(self):
        with sqlite_connection(self.db_path) as conn:
            patient_id = conn.execute(
                "INSERT INTO pacientes (nombre, apellido, is_deleted) "
                "VALUES ('Referenced', 'Patient', 1) RETURNING id"
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO expedientes (id_paciente, fecha_expediente) VALUES (?, '2026-10-10')",
                (patient_id,),
            )
            item_id = conn.execute(
                "INSERT INTO inventario (nombre, tipo, precio_venta, cantidad, is_deleted) "
                "VALUES ('Referenced item', 'Producto', 10, 0, 1) RETURNING id"
            ).fetchone()[0]
            sale_id = conn.execute(
                "INSERT INTO ventas (id_paciente, fecha_venta, monto_total, metodo_pago, estado_pago, estado) "
                "VALUES (?, '2026-10-10', 10, 'Efectivo', 'Pagado', 'Completada') RETURNING id",
                (patient_id,),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO ventas_detalle "
                "(id_venta, id_item_inventario, cantidad, precio_unitario, descuento) "
                "VALUES (?, ?, 1, 10, 0)",
                (sale_id, item_id),
            )
            conn.execute(
                "INSERT INTO movimientos_inventario (id_item, tipo_movimiento, cantidad) "
                "VALUES (?, 'Entrada', 1)",
                (item_id,),
            )

        paper_trash = self.client.get('/papelera')
        self.assertEqual(paper_trash.status_code, 200)
        patient_response = self.client.post(
            f'/pacientes/eliminar-permanente/{patient_id}',
            data={'csrf_token': self.csrf_token},
            follow_redirects=True,
        )
        item_response = self.client.post(
            f'/inventario/item/eliminar-permanente/{item_id}',
            data={'csrf_token': self.csrf_token},
            follow_redirects=True,
        )
        self.assertEqual(patient_response.status_code, 200)
        self.assertEqual(item_response.status_code, 200)
        self.assertIn('tiene 1 expediente(s)', patient_response.get_data(as_text=True))
        self.assertIn('conservar el historial', patient_response.get_data(as_text=True))
        self.assertIn('aparece en 1 línea(s) de venta', item_response.get_data(as_text=True))
        self.assertEqual(self.query('SELECT COUNT(*) FROM pacientes WHERE id=?', (patient_id,)), [(1,)])
        self.assertEqual(self.query('SELECT COUNT(*) FROM inventario WHERE id=?', (item_id,)), [(1,)])

    def test_upload_allowlist(self):
        self.assertTrue(clinic_app.allowed_file("scan.PDF"))
        self.assertTrue(clinic_app.allowed_file("photo.webp"))
        self.assertFalse(clinic_app.allowed_file("page.html"))
        self.assertFalse(clinic_app.allowed_file("graphic.svg"))
        self.assertFalse(clinic_app.allowed_file("no-extension"))

    def load_migration_module(self, name):
        spec = importlib.util.spec_from_file_location(name, MIGRATION_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_migration_schema_preserves_username_unique_and_date_type(self):
        migration = self.load_migration_module("migration_schema_test")
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute(
                "CREATE TABLE usuarios (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, creado DATE)"
            )
            ddl = migration.build_create_table_sqlite(conn, "usuarios")
        finally:
            conn.close()
        self.assertIn('UNIQUE ("username")', ddl)
        self.assertIn('"creado" DATE', ddl)
        self.assertEqual(migration.column_type_sqlite_to_postgres("TIME"), "TIME")

    def test_sequence_repair_only_advances_when_behind(self):
        migration = self.load_migration_module("sequence_guard_test")
        self.assertTrue(migration.sequence_needs_advance(1, False, 734))
        self.assertTrue(migration.sequence_needs_advance(733, True, 734))
        self.assertFalse(migration.sequence_needs_advance(734, True, 734))
        self.assertFalse(migration.sequence_needs_advance(1, False, None))

    def test_sequence_sync_command_advances_only_the_stale_sequence(self):
        migration = self.load_migration_module("sequence_sync_test")

        class FakeCursor:
            def __init__(self):
                self.rows = []
                self.row = None
                self.composed_query_count = 0
                self.set_values = []
                self.closed = False

            def execute(self, statement, params=None):
                if isinstance(statement, str) and "information_schema.columns" in statement:
                    self.rows = [("pacientes", "id")]
                elif isinstance(statement, str) and "pg_get_serial_sequence" in statement:
                    self.row = ("public.pacientes_id_seq",)
                elif isinstance(statement, str) and "setval" in statement:
                    self.set_values.append(params)
                else:
                    self.composed_query_count += 1
                    self.row = (1, False) if self.composed_query_count == 1 else (734,)

            def fetchall(self):
                return self.rows

            def fetchone(self):
                return self.row

            def close(self):
                self.closed = True

        class FakeConnection:
            def __init__(self):
                self.fake_cursor = FakeCursor()
                self.committed = False
                self.rolled_back = False
                self.closed = False

            def cursor(self):
                return self.fake_cursor

            def commit(self):
                self.committed = True

            def rollback(self):
                self.rolled_back = True

            def close(self):
                self.closed = True

        class FakePostgres:
            def __init__(self):
                self.fake_connection = FakeConnection()

            def connect(self, url):
                return self.fake_connection

        class FakeSQL:
            def __init__(self, value):
                self.value = value

            def format(self, *identifiers):
                return self.value

        class FakeSQLTools:
            @staticmethod
            def SQL(value):
                return FakeSQL(value)

            @staticmethod
            def Identifier(*parts):
                return ".".join(parts)

        fake_postgres = FakePostgres()
        original_psycopg2 = migration.psycopg2
        original_pg_sql = migration.pg_sql
        migration.psycopg2 = fake_postgres
        migration.pg_sql = FakeSQLTools
        try:
            synchronized = migration.synchronize_postgres_sequences("unused")
        finally:
            migration.psycopg2 = original_psycopg2
            migration.pg_sql = original_pg_sql
        self.assertEqual(synchronized, ["pacientes.id"])
        self.assertEqual(fake_postgres.fake_connection.fake_cursor.set_values, [("public.pacientes_id_seq", 734)])
        self.assertTrue(fake_postgres.fake_connection.committed)
        self.assertFalse(fake_postgres.fake_connection.rolled_back)
        self.assertTrue(fake_postgres.fake_connection.closed)

    def test_migration_refuses_nonempty_postgres_schema(self):
        migration = self.load_migration_module("migration_guard_test")

        class FakeCursor:
            def __init__(self):
                self.statements = []
                self.closed = False

            def execute(self, sql, params=None):
                self.statements.append(sql)

            def fetchone(self):
                return ("existing_table",)

            def close(self):
                self.closed = True

        class FakeConnection:
            def __init__(self):
                self.fake_cursor = FakeCursor()
                self.rolled_back = False
                self.closed = False

            def cursor(self):
                return self.fake_cursor

            def rollback(self):
                self.rolled_back = True

            def close(self):
                self.closed = True

        class FakePostgres:
            def __init__(self):
                self.fake_connection = FakeConnection()

            def connect(self, url):
                return self.fake_connection

        with tempfile.TemporaryDirectory() as temp_dir:
            sqlite_path = pathlib.Path(temp_dir) / "source.sqlite"
            with sqlite_connection(sqlite_path) as conn:
                conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)")
            fake_postgres = FakePostgres()
            old_psycopg2 = migration.psycopg2
            migration.psycopg2 = fake_postgres
            try:
                with self.assertRaisesRegex(RuntimeError, "not empty"):
                    migration.migrate_sqlite_to_postgres(str(sqlite_path), "unused")
            finally:
                migration.psycopg2 = old_psycopg2
            self.assertTrue(fake_postgres.fake_connection.rolled_back)
            self.assertTrue(fake_postgres.fake_connection.closed)
            self.assertTrue(fake_postgres.fake_connection.fake_cursor.closed)
            self.assertFalse(
                any("DROP TABLE" in sql for sql in fake_postgres.fake_connection.fake_cursor.statements)
            )


if __name__ == "__main__":
    unittest.main()
