import io
import os
import secrets
import sqlite3
import time
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlparse

from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    session,
    url_for,
)
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_wtf.csrf import CSRFError, CSRFProtect
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

import database
import finanzas as fin
from comun import (
    DATABASE,
    EXTENSIONES_IMAGEN,
    D,
    a_datetime,
    a_local,
    admin_required,
    ahora_utc,
    archivo_permitido,
    close_db,
    extension_de,
    f,
    fin_utc,
    get_db,
    hoy_local,
    inicio_utc,
    leer_monto,
    paginar_consulta,
    q,
    registrar_auditoria,
)
from correo import (
    MINUTOS_VALIDEZ_CODIGO,
    PROPOSITO_CORREO,
    PROPOSITO_LOGIN,
    CorreoError,
    comprobar_codigo,
    correo_activo,
    email_valido,
    enviar_codigo,
    segundos_para_reenviar,
)
from digifact import es_consumidor_final, leer_o_crear_secreto, normalize_nit

# =============================================================================
# --- 0. CONFIGURACIÓN DE ZONA HORARIA (GUATEMALA) ---
# =============================================================================
os.environ['TZ'] = 'America/Guatemala'
try:
    time.tzset()
except AttributeError:
    pass

# =============================================================================
# --- 1. CONFIGURACIÓN DE LA APLICACIÓN ---
# =============================================================================

RUTA_BASE = database.RUTA_BASE
UPLOAD_FOLDER = os.path.join(RUTA_BASE, 'uploads')
PRODUCT_UPLOAD_FOLDER = os.path.join(UPLOAD_FOLDER, 'productos')
EXPENSE_UPLOAD_FOLDER = os.path.join(UPLOAD_FOLDER, 'comprobantes_gastos')
INSTANCE_FOLDER = os.path.join(RUTA_BASE, 'instance')

# FLASK_ENV=production en el servidor: las cookies de sesión exigen HTTPS (Secure).
IS_PRODUCTION = os.environ.get('FLASK_ENV') == 'production'
MAX_INTENTOS_LOGIN = 5
MINUTOS_BLOQUEO_LOGIN = 15
MIN_LARGO_PASSWORD = 8
# Contraseñas que venían escritas en el código de versiones anteriores: quien aún las use debe cambiarla.
CLAVES_PREDETERMINADAS = ('46663627',)


def cargar_secret_key():
    """La clave que firma las sesiones sale del entorno o se genera una sola vez y se
    guarda fuera del código (instance/.secret_key). Nunca va escrita en el repositorio."""
    clave = os.environ.get('SECRET_KEY')
    if clave:
        return clave
    return leer_o_crear_secreto(os.path.join(INSTANCE_FOLDER, '.secret_key'), lambda: secrets.token_hex(32).encode()).decode()


for carpeta in (UPLOAD_FOLDER, PRODUCT_UPLOAD_FOLDER, EXPENSE_UPLOAD_FOLDER, INSTANCE_FOLDER):
    os.makedirs(carpeta, exist_ok=True)

app = Flask(__name__)
app.config['SECRET_KEY'] = cargar_secret_key()
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['PRODUCT_UPLOAD_FOLDER'] = PRODUCT_UPLOAD_FOLDER
app.config['EXPENSE_UPLOAD_FOLDER'] = EXPENSE_UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024 * 1024
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = IS_PRODUCTION
app.teardown_appcontext(close_db)

csrf = CSRFProtect(app)

# Crea la base si no existe o la migra sin perder datos (con respaldo previo).
_clave_inicial = database.inicializar_base_de_datos()
if _clave_inicial:
    print(f'\n*** Base nueva creada. Usuario: admin   Contraseña: {_clave_inicial}   (cámbiala al entrar) ***\n')

# --- CONFIGURACIÓN DE LOGIN ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Inicia sesión para continuar.'
login_manager.login_message_category = 'error'


@app.after_request
def cabeceras_de_seguridad(respuesta):
    respuesta.headers.setdefault('X-Content-Type-Options', 'nosniff')
    respuesta.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
    respuesta.headers.setdefault('Referrer-Policy', 'same-origin')
    return respuesta


@app.errorhandler(CSRFError)
def csrf_vencido(error):
    flash('La página estuvo abierta demasiado tiempo o la sesión cambió. Vuelve a intentarlo.', 'error')
    destino = request.referrer if request.referrer and request.host in request.referrer else url_for('dashboard')
    return redirect(destino)


@app.errorhandler(403)
def error_403(error):
    return render_template('error.html', codigo=403, titulo='Sin permiso', mensaje='No tienes permiso para ver esta página.'), 403


@app.errorhandler(404)
def error_404(error):
    return render_template('error.html', codigo=404, titulo='No encontrado', mensaje='La página o el registro que buscas no existe.'), 404


@app.errorhandler(413)
def error_413(error):
    return render_template('error.html', codigo=413, titulo='Archivo muy grande', mensaje='Los archivos subidos no pueden superar 64 MB en total.'), 413


@app.errorhandler(500)
def error_500(error):
    return render_template('error.html', codigo=500, titulo='Algo salió mal', mensaje='Ocurrió un error inesperado. Si pasa de nuevo, avisa al administrador.'), 500


@app.template_filter('local')
def filtro_local(valor, formato='%d/%m/%Y %H:%M'):
    momento = a_local(valor)
    return momento.strftime(formato) if momento else '—'


@app.template_filter('q')
def filtro_q(valor):
    return q(valor)


@app.template_global('url_pagina')
def url_pagina(numero):
    """Misma página con el número de página cambiado (conserva los filtros)."""
    argumentos = {**(request.view_args or {}), **request.args.to_dict(), 'pagina': numero}
    return url_for(request.endpoint, **argumentos)


@app.context_processor
def utilidades_de_plantilla():
    return {'hoy_iso': hoy_local().isoformat()}


@app.template_filter('etiqueta_metodo')
def filtro_etiqueta_metodo(valor):
    return fin.ETIQUETA_METODO.get(valor, valor)


# =============================================================================
# --- 2. SISTEMA DE USUARIOS Y PERMISOS ---
# =============================================================================

class User(UserMixin):
    def __init__(self, id, username, nombre, rol, activo=1):
        self.id = id
        self.username = username
        self.nombre = nombre
        self.rol = rol
        self.activo = activo

    @property
    def is_active(self):
        return bool(self.activo)


def _usuario_desde_fila(fila):
    return User(fila['id'], fila['username'], fila['nombre'], fila['rol'], fila['activo'])


@login_manager.user_loader
def load_user(user_id):
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    try:
        fila = conn.execute("SELECT * FROM usuarios WHERE id = ?", (user_id,)).fetchone()
    finally:
        conn.close()
    return _usuario_desde_fila(fila) if fila and fila['activo'] else None


def es_url_segura(destino):
    """Solo se redirige a rutas de esta misma aplicación (evita el redireccionamiento abierto)."""
    if not destino:
        return False
    partes = urlparse(destino)
    return not partes.scheme and not partes.netloc and destino.startswith('/') and not destino.startswith('//')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        db = get_db()
        usuario = db.execute("SELECT * FROM usuarios WHERE username = ?", (username,)).fetchone()
        ahora = ahora_utc()
        bloqueado = bool(usuario and usuario['bloqueado_hasta'] and a_datetime(usuario['bloqueado_hasta']) > ahora)

        if usuario and usuario['activo'] and not bloqueado and check_password_hash(usuario['password'], password):
            db.execute("UPDATE usuarios SET intentos_fallidos = 0, bloqueado_hasta = NULL WHERE id = ?", (usuario['id'],))
            db.commit()
            destino = request.args.get('next')
            destino = destino if es_url_segura(destino) else None
            if usuario['email'] and correo_activo(db):
                # Contraseña correcta: aún no hay sesión. Falta el código que se envía al correo.
                session.clear()
                session['pre2fa'] = {'id': usuario['id'], 'next': destino, 'default': password in CLAVES_PREDETERMINADAS,
                                     'hasta': (ahora_utc() + timedelta(minutes=MINUTOS_VALIDEZ_CODIGO + 5)).strftime('%Y-%m-%d %H:%M:%S')}
                try:
                    enviar_codigo(db, app.config['SECRET_KEY'], usuario, PROPOSITO_LOGIN, usuario['email'], 'Verificación de inicio de sesión')
                except CorreoError as exc:
                    session.pop('pre2fa', None)
                    registrar_auditoria('2fa_error_envio', 'sesion', f'No se pudo enviar el código a "{usuario["username"]}".', id_usuario=usuario['id'])
                    db.commit()
                    flash(f'No se pudo enviar el código de verificación: {exc} Avisa al administrador.', 'error')
                    return render_template('login.html')
                return redirect(url_for('verificar_login'))
            return _entrar(db, usuario, destino, password in CLAVES_PREDETERMINADAS)

        if usuario and not bloqueado:
            intentos = (usuario['intentos_fallidos'] or 0) + 1
            hasta = None
            if intentos >= MAX_INTENTOS_LOGIN:
                hasta = (ahora + timedelta(minutes=MINUTOS_BLOQUEO_LOGIN)).strftime('%Y-%m-%d %H:%M:%S')
                intentos = 0
            db.execute("UPDATE usuarios SET intentos_fallidos = ?, bloqueado_hasta = ? WHERE id = ?", (intentos, hasta, usuario['id']))
        registrar_auditoria('login_fallido', 'sesion', f'Intento de ingreso fallido para "{username[:40]}"' + (' (cuenta bloqueada).' if bloqueado else '.'),
                            id_usuario=usuario['id'] if usuario else None)
        db.commit()
        # Mensaje genérico: no revela si el usuario existe ni si está bloqueado.
        flash(f'Usuario o contraseña incorrectos. Tras {MAX_INTENTOS_LOGIN} intentos fallidos la cuenta se bloquea {MINUTOS_BLOQUEO_LOGIN} minutos.', 'error')
    return render_template('login.html')


def _entrar(db, usuario, destino, clave_predeterminada, metodo='contraseña'):
    """Abre la sesión (ya verificado todo lo necesario) y decide a dónde ir."""
    registrar_auditoria('login', 'sesion', f'Inició sesión {usuario["username"]} ({metodo}).', id_usuario=usuario['id'])
    db.commit()
    login_user(_usuario_desde_fila(usuario))
    if clave_predeterminada:
        flash('Tu contraseña es la que venía por defecto en el sistema y es conocida. Cámbiala ahora en Usuarios → "Cambiar mi contraseña".', 'warning')
        return redirect(url_for('lista_usuarios') if usuario['rol'] == 'admin' else url_for('dashboard'))
    return redirect(destino or url_for('dashboard'))


def _pendiente_2fa(db):
    """Usuario que ya puso bien su contraseña y espera el código, o (None, None)."""
    datos = session.get('pre2fa')
    if not datos:
        return None, None
    if (a_datetime(datos.get('hasta')) or ahora_utc()) < ahora_utc():
        session.pop('pre2fa', None)
        return None, None
    usuario = db.execute("SELECT * FROM usuarios WHERE id = ?", (datos['id'],)).fetchone()
    if not usuario or not usuario['activo'] or not usuario['email']:
        session.pop('pre2fa', None)
        return None, None
    return usuario, datos


def _correo_oculto(email):
    nombre, _, dominio = email.partition('@')
    return f'{nombre[:2]}{"*" * max(1, len(nombre) - 2)}@{dominio}'


@app.route('/login/verificar', methods=['GET', 'POST'])
def verificar_login():
    db = get_db()
    usuario, datos = _pendiente_2fa(db)
    if usuario is None:
        flash('Tu verificación venció. Inicia sesión de nuevo.', 'error')
        return redirect(url_for('login'))
    if request.method == 'POST':
        ok, motivo = comprobar_codigo(db, app.config['SECRET_KEY'], usuario['id'], PROPOSITO_LOGIN, request.form.get('codigo', ''))
        if ok:
            session.pop('pre2fa', None)
            db.execute("UPDATE usuarios SET email_verificado = 1 WHERE id = ?", (usuario['id'],))
            return _entrar(db, usuario, datos.get('next'), datos.get('default'), metodo='contraseña + código por correo')
        registrar_auditoria('2fa_fallido', 'sesion', f'Código de verificación rechazado para "{usuario["username"]}".', id_usuario=usuario['id'])
        db.commit()
        flash(motivo, 'error')
    return render_template('verificar_codigo.html', titulo='Verificación en 2 pasos', correo=_correo_oculto(usuario['email']),
                           minutos=MINUTOS_VALIDEZ_CODIGO, accion=url_for('verificar_login'), reenviar=url_for('reenviar_login'),
                           cancelar=url_for('login'), espera=segundos_para_reenviar(db, usuario['id'], PROPOSITO_LOGIN), publico=True)


@app.route('/login/reenviar', methods=['POST'])
def reenviar_login():
    db = get_db()
    usuario, _ = _pendiente_2fa(db)
    if usuario is None:
        return redirect(url_for('login'))
    espera = segundos_para_reenviar(db, usuario['id'], PROPOSITO_LOGIN)
    if espera:
        flash(f'Espera {espera} segundos antes de pedir otro código.', 'error')
    else:
        try:
            enviar_codigo(db, app.config['SECRET_KEY'], usuario, PROPOSITO_LOGIN, usuario['email'], 'Verificación de inicio de sesión')
            flash('Te enviamos un código nuevo.', 'success')
        except CorreoError as exc:
            flash(f'No se pudo enviar el código: {exc}', 'error')
    return redirect(url_for('verificar_login'))


# --- CORREO DE LA CUENTA (para la verificación en 2 pasos) ---
@app.route('/cuenta/correo', methods=['GET', 'POST'])
@login_required
def cuenta_correo():
    """Registrar o cambiar el correo propio. Solo se guarda cuando se confirma el código enviado a ese correo."""
    db = get_db()
    usuario = db.execute("SELECT * FROM usuarios WHERE id = ?", (current_user.id,)).fetchone()
    if not correo_activo(db):
        flash('El correo saliente aún no está listo: pide al administrador que lo configure y lo pruebe en Configuración.', 'error')
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        espera = segundos_para_reenviar(db, usuario['id'], PROPOSITO_CORREO)
        if not email_valido(email):
            flash('Escribe un correo válido.', 'error')
        elif db.execute("SELECT 1 FROM usuarios WHERE lower(email) = ? AND id <> ?", (email, usuario['id'])).fetchone():
            flash('Ese correo ya lo usa otro usuario.', 'error')
        elif espera:
            flash(f'Espera {espera} segundos antes de pedir otro código.', 'error')
        else:
            try:
                enviar_codigo(db, app.config['SECRET_KEY'], usuario, PROPOSITO_CORREO, email, 'Confirma tu correo')
                session['correo_pendiente'] = email
                return redirect(url_for('cuenta_correo_confirmar'))
            except CorreoError as exc:
                flash(f'No se pudo enviar el código: {exc}', 'error')
    return render_template('cuenta_correo.html', active_page='usuarios', actual=usuario['email'])


@app.route('/cuenta/correo/confirmar', methods=['GET', 'POST'])
@login_required
def cuenta_correo_confirmar():
    db = get_db()
    email = session.get('correo_pendiente')
    if not email:
        return redirect(url_for('cuenta_correo'))
    if request.method == 'POST':
        ok, motivo = comprobar_codigo(db, app.config['SECRET_KEY'], current_user.id, PROPOSITO_CORREO, request.form.get('codigo', ''))
        if ok:
            try:
                db.execute("UPDATE usuarios SET email = ?, email_verificado = 1 WHERE id = ?", (email, current_user.id))
                registrar_auditoria('cambiar_correo', 'usuarios', f'Verificó y guardó su correo ({_correo_oculto(email)}).')
                db.commit()
            except sqlite3.IntegrityError:
                db.rollback()
            session.pop('correo_pendiente', None)
            flash('Correo verificado. Desde ahora recibirás un código en él al iniciar sesión.', 'success')
            return redirect(url_for('dashboard'))
        flash(motivo, 'error')
    return render_template('verificar_codigo.html', titulo='Confirma tu correo', correo=_correo_oculto(email), minutos=MINUTOS_VALIDEZ_CODIGO,
                           accion=url_for('cuenta_correo_confirmar'), reenviar=None, cancelar=url_for('cuenta_correo'), espera=0, publico=False)


RUTAS_SIN_CORREO = ('static', 'logout', 'cuenta_correo', 'cuenta_correo_confirmar')


@app.before_request
def exigir_correo_registrado():
    """Quien aún no tiene correo (usuarios anteriores a la verificación en 2 pasos) debe registrarlo y confirmarlo.
    Solo se exige si el correo saliente ya está configurado y probado; si no, no habría forma de recibir el código."""
    if not current_user.is_authenticated or request.endpoint is None or request.endpoint in RUTAS_SIN_CORREO:
        return None
    db = get_db()
    fila = db.execute("SELECT email FROM usuarios WHERE id = ?", (current_user.id,)).fetchone()
    if fila and not fila['email'] and correo_activo(db):
        flash('Registra y confirma tu correo: lo usaremos para verificar tus inicios de sesión.', 'warning')
        return redirect(url_for('cuenta_correo'))
    return None


@app.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


# --- GESTIÓN DE USUARIOS ---
ROLES_USUARIO = ('usuario', 'admin')


def _admins_activos(db, excluir=None):
    return db.execute("SELECT COUNT(*) FROM usuarios WHERE rol = 'admin' AND activo = 1 AND id <> ?", (excluir or 0,)).fetchone()[0]


@app.route('/usuarios')
@login_required
@admin_required
def lista_usuarios():
    db = get_db()
    usuarios = db.execute("SELECT id, username, nombre, rol, activo, bloqueado_hasta, email FROM usuarios ORDER BY activo DESC, nombre").fetchall()
    return render_template('usuarios.html', active_page='usuarios', usuarios=usuarios, ahora=ahora_utc().strftime('%Y-%m-%d %H:%M:%S'))


@app.route('/usuarios/nuevo', methods=['POST'])
@login_required
@admin_required
def nuevo_usuario():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    nombre = request.form.get('nombre', '').strip()
    rol = request.form.get('rol', '')
    email = request.form.get('email', '').strip().lower()
    if not username or not nombre or rol not in ROLES_USUARIO:
        flash('Nombre, usuario y rol son obligatorios.', 'error')
    elif not email_valido(email):
        flash('El correo es obligatorio y debe ser válido: se usa para la verificación en 2 pasos.', 'error')
    elif get_db().execute("SELECT 1 FROM usuarios WHERE lower(email) = ?", (email,)).fetchone():
        flash('Ese correo ya lo usa otro usuario.', 'error')
    elif len(password) < MIN_LARGO_PASSWORD:
        flash(f'La contraseña debe tener al menos {MIN_LARGO_PASSWORD} caracteres.', 'error')
    else:
        db = get_db()
        try:
            db.execute("INSERT INTO usuarios (username, password, nombre, rol, email) VALUES (?, ?, ?, ?, ?)",
                       (username, generate_password_hash(password), nombre, rol, email))
            registrar_auditoria('crear_usuario', 'usuarios', f'Creó el usuario "{username}" ({rol}, correo {_correo_oculto(email)}).')
            db.commit()
            flash("Usuario creado con éxito.", "success")
        except sqlite3.IntegrityError:
            db.rollback()
            flash("El nombre de usuario ya existe.", "error")
    return redirect(url_for('lista_usuarios'))


@app.route('/usuarios/<int:id>/activar', methods=['POST'])
@login_required
@admin_required
def alternar_usuario(id):
    """Un usuario no se borra (su nombre queda en ventas, cajas y auditoría): se desactiva."""
    db = get_db()
    usuario = db.execute("SELECT * FROM usuarios WHERE id = ?", (id,)).fetchone()
    if usuario is None:
        abort(404)
    if usuario['activo']:
        if usuario['id'] == current_user.id:
            flash('No puedes desactivar tu propio usuario.', 'error')
            return redirect(url_for('lista_usuarios'))
        if usuario['rol'] == 'admin' and _admins_activos(db, excluir=usuario['id']) == 0:
            flash('Debe quedar al menos un administrador activo.', 'error')
            return redirect(url_for('lista_usuarios'))
    nuevo = 0 if usuario['activo'] else 1
    db.execute("UPDATE usuarios SET activo = ?, intentos_fallidos = 0, bloqueado_hasta = NULL WHERE id = ?", (nuevo, id))
    registrar_auditoria('activar_usuario' if nuevo else 'desactivar_usuario', 'usuarios',
                        f'{"Activó" if nuevo else "Desactivó"} al usuario "{usuario["username"]}".')
    db.commit()
    flash('Usuario activado.' if nuevo else 'Usuario desactivado: ya no puede entrar, pero su historial se conserva.', 'success')
    return redirect(url_for('lista_usuarios'))


@app.route('/usuarios/<int:id>/password', methods=['POST'])
@login_required
@admin_required
def restablecer_password(id):
    db = get_db()
    usuario = db.execute("SELECT username FROM usuarios WHERE id = ?", (id,)).fetchone()
    if usuario is None:
        abort(404)
    password = request.form.get('password', '')
    if len(password) < MIN_LARGO_PASSWORD:
        flash(f'La contraseña debe tener al menos {MIN_LARGO_PASSWORD} caracteres.', 'error')
    else:
        db.execute("UPDATE usuarios SET password = ?, intentos_fallidos = 0, bloqueado_hasta = NULL WHERE id = ?",
                   (generate_password_hash(password), id))
        registrar_auditoria('restablecer_password', 'usuarios', f'Restableció la contraseña de "{usuario["username"]}".')
        db.commit()
        flash(f'Contraseña de "{usuario["username"]}" restablecida.', 'success')
    return redirect(url_for('lista_usuarios'))


@app.route('/cuenta/password', methods=['POST'])
@login_required
def cambiar_mi_password():
    db = get_db()
    usuario = db.execute("SELECT password FROM usuarios WHERE id = ?", (current_user.id,)).fetchone()
    nueva = request.form.get('nueva', '')
    if not check_password_hash(usuario['password'], request.form.get('actual', '')):
        flash('La contraseña actual no es correcta.', 'error')
    elif len(nueva) < MIN_LARGO_PASSWORD:
        flash(f'La nueva contraseña debe tener al menos {MIN_LARGO_PASSWORD} caracteres.', 'error')
    else:
        db.execute("UPDATE usuarios SET password = ? WHERE id = ?", (generate_password_hash(nueva), current_user.id))
        registrar_auditoria('cambiar_password', 'usuarios', 'Cambió su propia contraseña.')
        db.commit()
        flash('Tu contraseña fue actualizada.', 'success')
    return redirect(request.referrer if request.referrer and request.host in request.referrer else url_for('dashboard'))


@app.route('/auditoria')
@login_required
@admin_required
def auditoria():
    db = get_db()
    modulo = request.args.get('modulo', '').strip()
    texto = request.args.get('q', '').strip()
    condiciones, params = ['1=1'], []
    if modulo:
        condiciones.append('a.modulo = ?')
        params.append(modulo)
    if texto:
        condiciones.append('(a.descripcion LIKE ? OR u.nombre LIKE ?)')
        params += [f'%{texto}%', f'%{texto}%']
    filas, pag = paginar_consulta(db, f"""SELECT a.*, u.nombre AS usuario FROM auditoria a LEFT JOIN usuarios u ON u.id = a.id_usuario
                                          WHERE {' AND '.join(condiciones)} ORDER BY a.id DESC""", params, 50)
    modulos = [r[0] for r in db.execute("SELECT DISTINCT modulo FROM auditoria ORDER BY modulo")]
    return render_template('auditoria.html', active_page='auditoria', registros=filas, pag=pag, modulos=modulos, modulo=modulo, texto=texto)


# =============================================================================
# --- 3. FUNCIONES AUXILIARES Y GESTIÓN DE ARCHIVOS ---
# =============================================================================

def calcular_edad(f):
    if not f: return None
    try:
        fn = datetime.strptime(f, '%Y-%m-%d').date()
        hoy = date.today()
        return hoy.year - fn.year - ((hoy.month, hoy.day) < (fn.month, fn.day))
    except (ValueError, TypeError):
        return None


def allowed_file(filename):
    return archivo_permitido(filename)


def save_unique_file(file, folder):
    original_filename = secure_filename(file.filename)
    unique_filename = f"{uuid.uuid4().hex}_{original_filename}"
    file.save(os.path.join(folder, unique_filename))
    return unique_filename


def leer_nit_formulario(texto):
    """(nit normalizado | None, mensaje de error | None). Vacío o 'CF' -> sin NIT."""
    if es_consumidor_final(texto):
        return None, None
    nit = normalize_nit(texto)
    if not nit or len(nit) > 12:
        return None, 'El NIT no es válido (solo números, con K final opcional).'
    return nit, None


def get_caja_abierta(db):
    return fin.turno_abierto(db)


def _entero_o(valor, defecto=0):
    try:
        return int(str(valor).strip())
    except (ValueError, TypeError):
        return defecto


def procesar_formulario_inventario(conn, item_id=None):
    """Valida y guarda un artículo. Devuelve un mensaje de error o None si se guardó."""
    nombre = request.form.get('nombre', '').strip()
    tipo = request.form.get('tipo')
    descripcion = request.form.get('descripcion', '')
    codigo_barras = request.form.get('codigo_barras', '')
    precio_venta = leer_monto(request.form.get('precio_venta'), permitir_cero=True)
    precio_compra = request.form.get('precio_compra')
    porcentaje_ganancia = request.form.get('porcentaje_ganancia')
    cantidad = request.form.get('cantidad')
    fecha_vencimiento = request.form.get('fecha_vencimiento')
    ruta_foto = request.form.get('ruta_foto_actual')

    if not nombre or tipo not in ('Producto', 'Servicio'):
        return 'El nombre y el tipo (Producto o Servicio) son obligatorios.'
    if precio_venta is None:
        return 'El precio de venta no es válido.'
    if precio_compra:
        precio_compra = leer_monto(precio_compra, permitir_cero=True)
        if precio_compra is None:
            return 'El precio de compra no es válido.'
        precio_compra = f(precio_compra)
    else:
        precio_compra = None
    try:
        porcentaje_ganancia = float(porcentaje_ganancia) if porcentaje_ganancia else None
    except ValueError:
        return 'El porcentaje de ganancia no es válido.'
    fecha_vencimiento = fecha_vencimiento or None

    if 'foto' in request.files:
        file = request.files['foto']
        if file.filename != '':
            if extension_de(file.filename) not in EXTENSIONES_IMAGEN:
                return 'La foto debe ser una imagen (jpg, png, gif, webp o bmp).'
            ruta_foto = save_unique_file(file, app.config['PRODUCT_UPLOAD_FOLDER'])

    cant_val = max(_entero_o(cantidad, 0), 0)
    if tipo == 'Servicio':
        precio_compra = porcentaje_ganancia = fecha_vencimiento = ruta_foto = None
        cant_val = None

    if item_id:
        conn.execute("UPDATE inventario SET nombre=?, descripcion=?, tipo=?, codigo_barras=?, precio_venta=?, precio_compra=?, porcentaje_ganancia=?, cantidad=?, fecha_vencimiento=?, ruta_foto=? WHERE id=?",
                     (nombre, descripcion, tipo, codigo_barras, f(precio_venta), precio_compra, porcentaje_ganancia, cant_val, fecha_vencimiento, ruta_foto, item_id))
    else:
        conn.execute("INSERT INTO inventario (nombre, descripcion, tipo, codigo_barras, precio_venta, precio_compra, porcentaje_ganancia, cantidad, fecha_vencimiento, ruta_foto) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (nombre, descripcion, tipo, codigo_barras, f(precio_venta), precio_compra, porcentaje_ganancia, cant_val, fecha_vencimiento, ruta_foto))
    conn.commit()
    return None


# =============================================================================
# --- 4. RUTA DASHBOARD ---
# =============================================================================

@app.route('/')
@login_required
def dashboard():
    conn = get_db()
    stats = {}
    hoy = hoy_local()
    hoy_iso = hoy.strftime('%Y-%m-%d')
    stats['total_pacientes'] = conn.execute('SELECT COUNT(id) FROM pacientes WHERE is_deleted = 0').fetchone()[0]
    ventas = conn.execute("SELECT COALESCE(SUM(monto_total), 0) FROM ventas WHERE estado != 'Cancelada' AND fecha_venta BETWEEN ? AND ?",
                          (inicio_utc(hoy), fin_utc(hoy))).fetchone()[0]
    stats['ventas_hoy'] = D(ventas)
    stats['bajo_stock'] = conn.execute("SELECT COUNT(id) FROM inventario WHERE tipo='Producto' AND COALESCE(cantidad, 0) < 5 AND is_deleted = 0").fetchone()[0]
    fecha_limite = (hoy + timedelta(days=90)).strftime('%Y-%m-%d')
    stats['por_vencer'] = conn.execute("SELECT COUNT(id) FROM inventario WHERE tipo='Producto' AND fecha_vencimiento BETWEEN ? AND ? AND is_deleted = 0", (hoy_iso, fecha_limite)).fetchone()[0]
    proximas_citas = conn.execute("SELECT c.id, c.fecha_cita, c.hora_cita, c.descripcion, p.nombre, p.apellido FROM citas c JOIN pacientes p ON c.id_paciente = p.id WHERE c.fecha_cita >= ? AND c.estado = 'Pendiente' ORDER BY c.fecha_cita ASC, c.hora_cita ASC LIMIT 10", (hoy_iso,)).fetchall()

    inv_stats = conn.execute("""
        SELECT SUM(COALESCE(cantidad, 0) * COALESCE(precio_compra, 0)), SUM(COALESCE(cantidad, 0) * COALESCE(precio_venta, 0))
        FROM inventario WHERE tipo = 'Producto' AND is_deleted = 0
    """).fetchone()
    costo_total = inv_stats[0] or 0
    venta_total = inv_stats[1] or 0
    stats['inv_costo'] = costo_total
    stats['inv_venta'] = venta_total
    stats['inv_ganancia'] = venta_total - costo_total

    dinero = fin.resumen_dinero(conn)
    avisos = []
    if es_admin_actual():
        problemas = fin.problemas_de_conciliacion(conn)
        if problemas:
            avisos.append(('La conciliación del dinero tiene diferencias sin resolver.', 'dinero.fondo'))
        vencidas = conn.execute("SELECT COUNT(*), COALESCE(SUM(saldo_pendiente), 0) FROM cuentas_por_pagar WHERE estado IN ('Pendiente','Parcial') AND fecha_vencimiento < ?", (hoy_iso,)).fetchone()
        if vencidas[0]:
            avisos.append((f'{vencidas[0]} cuenta(s) por pagar vencida(s) por {q(vencidas[1])}.', 'dinero.cxp_lista'))
    cobros_vencidos = conn.execute("SELECT COUNT(*) FROM cuentas_por_cobrar WHERE estado IN ('Pendiente','Parcial') AND fecha_vencimiento IS NOT NULL AND fecha_vencimiento < ?", (hoy_iso,)).fetchone()[0]
    if cobros_vencidos:
        avisos.append((f'{cobros_vencidos} cuenta(s) por cobrar vencida(s).', 'dinero.cxc_lista'))
    return render_template('index.html', active_page='dashboard', stats=stats, proximas_citas=proximas_citas, dinero=dinero, avisos=avisos)


def es_admin_actual():
    return current_user.is_authenticated and current_user.rol == 'admin'


# =============================================================================
# --- 5. APIs (JSON ENDPOINTS) ---
# =============================================================================

@app.route('/api/pacientes/buscar')
@login_required
def buscar_pacientes():
    q_txt = f"%{request.args.get('q', '', type=str)}%"
    db = get_db()
    pacientes_db = db.execute("SELECT * FROM pacientes WHERE (nombre LIKE ? OR apellido LIKE ? OR (nombre || ' ' || apellido) LIKE ? OR telefono LIKE ? OR nit LIKE ?) AND is_deleted = 0 ORDER BY nombre LIMIT 200", (q_txt, q_txt, q_txt, q_txt, q_txt)).fetchall()
    return jsonify([dict(p, edad=calcular_edad(p['fecha_nacimiento'])) for p in pacientes_db])


@app.route('/api/expediente/buscar/<int:id_paciente>')
@login_required
def buscar_expedientes(id_paciente):
    q_txt = f"%{request.args.get('q', '', type=str)}%"
    db = get_db()
    expedientes_db = db.execute("SELECT * FROM expedientes WHERE id_paciente = ? AND (CAST(id_expediente AS TEXT) LIKE ? OR fecha_expediente LIKE ? OR motivo_consulta LIKE ?) ORDER BY fecha_expediente DESC", (id_paciente, q_txt, q_txt, q_txt)).fetchall()
    return jsonify([dict(row) for row in expedientes_db])


@app.route('/api/inventario/buscar')
@login_required
def api_inventario_buscar():
    query = request.args.get('q', '', type=str)
    filtro_costo_cero = request.args.get('costo_cero') == 'true'
    filtro_bajo_stock = request.args.get('bajo_stock') == 'true'
    filtro_por_vencer = request.args.get('por_vencer') == 'true'
    limite = request.args.get('limite', type=int)
    conn = get_db()
    base_sql = "SELECT * FROM inventario"
    conditions = ["is_deleted = 0"]
    params = []
    if query:
        conditions.append("(nombre LIKE ? OR tipo LIKE ? OR codigo_barras LIKE ?)")
        params.extend([f"%{query}%", f"%{query}%", f"%{query}%"])
    if filtro_costo_cero:
        conditions.append("(tipo = 'Producto' AND (precio_compra IS NULL OR precio_compra = 0))")
    if filtro_bajo_stock:
        conditions.append("(tipo = 'Producto' AND COALESCE(cantidad, 0) < 5)")
    if filtro_por_vencer:
        hoy = date.today().strftime('%Y-%m-%d'); fecha_limite = (date.today() + timedelta(days=90)).strftime('%Y-%m-%d')
        conditions.append("(fecha_vencimiento BETWEEN ? AND ?)"); params.extend([hoy, fecha_limite])
    order_by = "ORDER BY tipo DESC, nombre"
    if filtro_por_vencer:
        order_by = "ORDER BY fecha_vencimiento ASC, nombre"
    sql = f"{base_sql} WHERE {' AND '.join(conditions)} {order_by}"
    if limite:
        sql += f" LIMIT {min(max(limite, 1), 500)}"
    items_db = conn.execute(sql, tuple(params)).fetchall()
    items = []
    for item in items_db:
        item_dict = dict(item)
        precio_venta = item_dict.get('precio_venta') or 0
        precio_compra = item_dict.get('precio_compra') or 0
        cantidad = item_dict.get('cantidad') or 0
        if item_dict['tipo'] == 'Producto':
            ganancia_unidad = precio_venta - precio_compra
            item_dict['ganancia_unidad'] = ganancia_unidad
            item_dict['ganancia_total'] = ganancia_unidad * cantidad
            item_dict['por_vencer'] = False
            if item_dict.get('fecha_vencimiento'):
                try:
                    fecha_venc = datetime.strptime(item_dict['fecha_vencimiento'], '%Y-%m-%d').date()
                    if date.today() <= fecha_venc < (date.today() + timedelta(days=90)):
                        item_dict['por_vencer'] = True
                except ValueError:
                    pass
        else:
            item_dict['ganancia_unidad'] = 0
            item_dict['ganancia_total'] = 0
            item_dict['por_vencer'] = False
        items.append(item_dict)
    return jsonify(items)


@app.route('/api/citas/horas_ocupadas/<string:fecha>')
@login_required
def api_horas_ocupadas(fecha):
    try:
        datetime.strptime(fecha, '%Y-%m-%d')
    except ValueError:
        return jsonify({'error': 'Formato de fecha inválido'}), 400
    conn = get_db()
    horas_db = conn.execute("SELECT hora_cita FROM citas WHERE fecha_cita = ? AND estado = 'Pendiente'", (fecha,)).fetchall()
    return jsonify([row['hora_cita'] for row in horas_db])


@app.route('/api/citas/historial/buscar')
@login_required
def api_citas_historial_buscar():
    query = request.args.get('q', '', type=str)
    filtro_estado = request.args.get('estado', 'Todos')
    conn = get_db()
    sql = """
        SELECT c.id, c.fecha_cita, c.hora_cita, c.descripcion, c.estado, p.nombre, p.apellido,
               GROUP_CONCAT(i.nombre || ' (' || vd.cantidad || ')', ', ') as productos_usados
        FROM citas c
        JOIN pacientes p ON c.id_paciente = p.id
        LEFT JOIN ventas v ON v.id_cita = c.id AND v.estado != 'Cancelada'
        LEFT JOIN ventas_detalle vd ON vd.id_venta = v.id
        LEFT JOIN inventario i ON vd.id_item_inventario = i.id
        WHERE c.estado IN ('Completada', 'Cancelada')
    """
    params = []
    if query:
        search_term = f"%{query}%"
        sql += " AND (p.nombre LIKE ? OR p.apellido LIKE ?)"
        params.extend([search_term, search_term])
    if filtro_estado in ['Completada', 'Cancelada']:
        sql += " AND c.estado = ?"
        params.append(filtro_estado)
    sql += " GROUP BY c.id ORDER BY c.fecha_cita DESC, c.hora_cita DESC LIMIT 500"
    citas_db = conn.execute(sql, tuple(params)).fetchall()
    return jsonify([dict(row) for row in citas_db])


@app.route('/api/inventario/historial/buscar')
@login_required
def api_inventario_historial_buscar():
    query = request.args.get('q', '', type=str)
    conn = get_db()
    search_term = f"%{query}%"
    sql = "SELECT datetime(mi.fecha, 'localtime') as fecha, mi.tipo_movimiento, mi.cantidad, mi.descripcion, i.nombre as nombre_item FROM movimientos_inventario mi JOIN inventario i ON mi.id_item = i.id WHERE i.nombre LIKE ? OR mi.descripcion LIKE ? ORDER BY mi.fecha DESC LIMIT 1000"
    movimientos_db = conn.execute(sql, (search_term, search_term)).fetchall()
    return jsonify([dict(row) for row in movimientos_db])


# =============================================================================
# --- 6. RUTAS DE ARCHIVOS (UPLOADS) ---
# =============================================================================

EXTENSIONES_EN_PANTALLA = EXTENSIONES_IMAGEN | {'pdf'}


def _servir(carpeta, filename):
    # Lo que no es imagen ni PDF se descarga en vez de abrirse en el navegador.
    return send_from_directory(carpeta, filename, as_attachment=extension_de(filename) not in EXTENSIONES_EN_PANTALLA)


@app.route('/uploads/<path:filename>')
@login_required
def uploaded_file(filename):
    return _servir(app.config['UPLOAD_FOLDER'], filename)


@app.route('/uploads/productos/<filename>')
@login_required
def uploaded_product_file(filename):
    return _servir(app.config['PRODUCT_UPLOAD_FOLDER'], filename)


@app.route('/uploads/comprobantes/<filename>')
@login_required
def uploaded_expense_file(filename):
    return _servir(app.config['EXPENSE_UPLOAD_FOLDER'], filename)


# =============================================================================
# --- 7. MÓDULOS DE LA APLICACIÓN ---
# =============================================================================

# --- PACIENTES ---
@app.route('/pacientes')
@login_required
def pacientes():
    db = get_db()
    pacientes_db = db.execute('SELECT * FROM pacientes WHERE is_deleted = 0 ORDER BY nombre, apellido').fetchall()
    return render_template('pacientes.html', active_page='pacientes', pacientes=[dict(p, edad=calcular_edad(p['fecha_nacimiento'])) for p in pacientes_db])


@app.route('/pacientes/nuevo', methods=['POST'])
@login_required
def nuevo_paciente():
    n, a = request.form.get('nombre', '').strip(), request.form.get('apellido', '').strip()
    nit, error_nit = leer_nit_formulario(request.form.get('nit', ''))
    if not n or not a:
        flash('El nombre y el apellido son obligatorios.', 'error')
    elif error_nit:
        flash(error_nit, 'error')
    else:
        db = get_db()
        db.execute('INSERT INTO pacientes (nombre, apellido, telefono, fecha_nacimiento, nit) VALUES (?, ?, ?, ?, ?)',
                   (n, a, request.form.get('telefono', '').strip(), request.form.get('fecha_nacimiento') or None, nit))
        db.commit()
        flash('Paciente agregado con éxito.', 'success')
    return redirect(url_for('pacientes'))


@app.route('/paciente/<int:id_paciente>/expedientes')
@login_required
def expedientes_lista(id_paciente):
    db = get_db()
    paciente = db.execute('SELECT * FROM pacientes WHERE id = ?', (id_paciente,)).fetchone()
    if paciente is None:
        abort(404)
    expedientes = db.execute('SELECT * FROM expedientes WHERE id_paciente = ? ORDER BY fecha_expediente DESC', (id_paciente,)).fetchall()
    return render_template('expedientes_lista.html', active_page='pacientes', paciente=paciente, expedientes=expedientes)


# --- EXPEDIENTES ---
CAMPOS_EXPEDIENTE = (
    'motivo_consulta', 'enfermedades', 'vasos_agua', 'alimentos', 'problemas_hormonales', 'alergias', 'menstruacion',
    'caida_cabello', 'caspa', 'horas_sueno', 'productos_corporales', 'otros_sintomas', 'skincare_limpieza',
    'skincare_hidratacion', 'skincare_ojos', 'skincare_fotoproteccion', 'skincare_noche', 'skincare_tratamiento',
    'sustancias_ingeridas', 'skincare_nuevo_limpieza', 'skincare_nuevo_hidratacion', 'skincare_nuevo_ojos',
    'skincare_nuevo_fotoproteccion', 'skincare_nuevo_noche', 'skincare_nuevo_tratamiento',
)


def _actualizar_datos_paciente(db, id_paciente):
    """Guarda los datos del paciente que vienen en el formulario del expediente. Devuelve un error o None."""
    nombre = request.form.get('p_nombre', '').strip()
    apellido = request.form.get('p_apellido', '').strip()
    if not nombre or not apellido:
        return 'El nombre y el apellido del paciente son obligatorios.'
    nit, error_nit = leer_nit_formulario(request.form.get('p_nit', ''))
    if error_nit:
        return error_nit
    db.execute("UPDATE pacientes SET nombre=?, apellido=?, fecha_nacimiento=?, telefono=?, nit=? WHERE id=?",
               (nombre, apellido, request.form.get('p_fecha_nacimiento') or None, request.form.get('p_telefono', '').strip(), nit, id_paciente))
    return None


@app.route('/paciente/<int:id_paciente>/expediente/nuevo', methods=['GET', 'POST'])
@login_required
def expediente_nuevo(id_paciente):
    db = get_db()
    paciente = db.execute('SELECT * FROM pacientes WHERE id = ?', (id_paciente,)).fetchone()
    if paciente is None:
        abort(404)
    if request.method == 'POST':
        error = _actualizar_datos_paciente(db, id_paciente)
        if error:
            db.rollback()
            flash(error, 'error')
            return redirect(url_for('expediente_nuevo', id_paciente=id_paciente))
        columnas = ('id_paciente', 'fecha_expediente') + CAMPOS_EXPEDIENTE
        valores = (id_paciente, request.form.get('fecha_expediente') or hoy_local().isoformat()) + tuple(request.form.get(c, '') for c in CAMPOS_EXPEDIENTE)
        cur = db.execute(f"INSERT INTO expedientes ({', '.join(columnas)}) VALUES ({', '.join('?' * len(columnas))})", valores)
        db.commit()
        flash('Expediente creado y datos del paciente actualizados.', 'success')
        return redirect(url_for('expediente_editar', id_expediente=cur.lastrowid))
    return render_template('expediente_form.html', active_page='pacientes', accion="Crear", paciente=paciente, expediente=None, sesiones=[], documentos=[], edad=calcular_edad(paciente['fecha_nacimiento']), today=date.today())


@app.route('/expediente/editar/<int:id_expediente>', methods=['GET', 'POST'])
@login_required
def expediente_editar(id_expediente):
    db = get_db()
    expediente = db.execute('SELECT * FROM expedientes WHERE id_expediente = ?', (id_expediente,)).fetchone()
    if expediente is None:
        abort(404)
    id_paciente = expediente['id_paciente']
    if request.method == 'POST':
        error = _actualizar_datos_paciente(db, id_paciente)
        if error:
            db.rollback()
            flash(error, 'error')
            return redirect(url_for('expediente_editar', id_expediente=id_expediente))
        asignaciones = ', '.join(f'{c}=?' for c in ('fecha_expediente',) + CAMPOS_EXPEDIENTE)
        valores = (request.form.get('fecha_expediente') or expediente['fecha_expediente'],) + tuple(request.form.get(c, '') for c in CAMPOS_EXPEDIENTE)
        db.execute(f"UPDATE expedientes SET {asignaciones} WHERE id_expediente=?", valores + (id_expediente,))
        db.commit()
        flash('Expediente y datos del paciente actualizados.', 'success')
        return redirect(url_for('expediente_editar', id_expediente=id_expediente))
    expediente = db.execute('SELECT * FROM expedientes WHERE id_expediente = ?', (id_expediente,)).fetchone()
    paciente = db.execute('SELECT * FROM pacientes WHERE id = ?', (expediente['id_paciente'],)).fetchone()
    sesiones = db.execute('SELECT * FROM sesiones WHERE id_expediente = ? ORDER BY fecha_sesion DESC', (id_expediente,)).fetchall()
    documentos = db.execute('SELECT * FROM documentos WHERE id_expediente = ? AND is_deleted = 0 ORDER BY fecha_subida DESC', (id_expediente,)).fetchall()
    return render_template('expediente_form.html', active_page='pacientes', accion="Editar", paciente=paciente, expediente=expediente, sesiones=sesiones, documentos=documentos, edad=calcular_edad(paciente['fecha_nacimiento']), today=date.today())


@app.route('/sesion/nueva/<int:id_expediente>', methods=['POST'])
@login_required
def sesion_nueva(id_expediente):
    db = get_db()
    if db.execute('SELECT 1 FROM expedientes WHERE id_expediente = ?', (id_expediente,)).fetchone() is None:
        abort(404)
    tratamiento = request.form.get('tratamiento', '').strip()
    if not tratamiento:
        flash('Indica el tratamiento de la sesión.', 'error')
        return redirect(url_for('expediente_editar', id_expediente=id_expediente) + '#documentos')
    db.execute('INSERT INTO sesiones (id_expediente, fecha_sesion, tratamiento, resultados) VALUES (?, ?, ?, ?)',
               (id_expediente, request.form.get('fecha_sesion') or hoy_local().isoformat(), tratamiento, request.form.get('resultados', '')))
    db.commit()
    flash('Nueva sesión agregada.', 'success')
    return redirect(url_for('expediente_editar', id_expediente=id_expediente) + '#documentos')


@app.route('/documento/subir/<int:id_expediente>', methods=['POST'])
@login_required
def subir_documento(id_expediente):
    db = get_db()
    if db.execute('SELECT 1 FROM expedientes WHERE id_expediente = ?', (id_expediente,)).fetchone() is None:
        abort(404)
    destino = redirect(url_for('expediente_editar', id_expediente=id_expediente) + '#documentos')
    files = request.files.getlist('documento')
    if not files or files[0].filename == '':
        flash('No se seleccionó ningún archivo.', 'error')
        return destino
    uploads, rechazados = 0, []
    for file in files:
        if not file or not file.filename:
            continue
        if not allowed_file(file.filename):
            rechazados.append(file.filename)
            continue
        unique_name = save_unique_file(file, app.config['UPLOAD_FOLDER'])
        db.execute('INSERT INTO documentos (id_expediente, nombre_archivo, ruta_archivo) VALUES (?, ?, ?)', (id_expediente, unique_name, unique_name))
        uploads += 1
    if uploads > 0:
        db.commit()
        flash(f'{uploads} documento(s) subido(s) con éxito.', 'success')
    if rechazados:
        flash(f'No se aceptó este tipo de archivo: {", ".join(rechazados[:3])}. Se permiten PDF, imágenes, Word, Excel y texto.', 'error')
    return destino


# --- INVENTARIO ---
def _enriquecer_item(item, dias_alerta):
    item_dict = dict(item)
    precio_venta, precio_compra, cantidad = item_dict.get('precio_venta') or 0, item_dict.get('precio_compra') or 0, item_dict.get('cantidad') or 0
    item_dict['ganancia_unidad'] = item_dict['ganancia_total'] = 0
    item_dict['por_vencer'] = False
    if item_dict['tipo'] == 'Producto':
        ganancia_unidad = precio_venta - precio_compra
        item_dict['ganancia_unidad'] = ganancia_unidad
        item_dict['ganancia_total'] = ganancia_unidad * cantidad
        if item_dict.get('fecha_vencimiento'):
            try:
                fecha_venc = datetime.strptime(item_dict['fecha_vencimiento'], '%Y-%m-%d').date()
                if date.today() <= fecha_venc < (date.today() + timedelta(days=dias_alerta)):
                    item_dict['por_vencer'] = True
            except ValueError:
                pass
    return item_dict


@app.route('/inventario')
@login_required
def inventario():
    conn = get_db()
    items_db = conn.execute('SELECT * FROM inventario WHERE is_deleted = 0 ORDER BY tipo DESC, nombre').fetchall()
    items = [_enriquecer_item(item, 20) for item in items_db]
    return render_template('inventario.html', active_page='inventario', items=items)


@app.route('/inventario/exportar-xlsx')
@login_required
def inventario_exportar_xlsx():
    conn = get_db()
    productos = conn.execute(
        "SELECT nombre, cantidad, precio_compra, precio_venta, fecha_vencimiento "
        "FROM inventario WHERE is_deleted = 0 AND tipo = 'Producto' AND COALESCE(cantidad, 0) > 0 "
        "ORDER BY nombre"
    ).fetchall()

    COLOR_PETROLEO = "1F7A7A"
    COLOR_ARENA = "EDC988"
    COLOR_FONDO = "F3E6E0"

    wb = Workbook()
    ws = wb.active
    ws.title = "Inventario"

    headers = ["Producto", "Cantidad", "Costo Unit. (Q)", "Venta Unit. (Q)", "Costo Total (Q)", "Venta Total (Q)", "Vencimiento"]
    ws.merge_cells('A1:G1')
    titulo = ws['A1']
    titulo.value = "REPORTE GENERAL DE INVENTARIO"
    titulo.font = Font(name="Calibri", size=16, bold=True, color=COLOR_PETROLEO)
    titulo.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[1].height = 26

    ws.merge_cells('A2:G2')
    subtitulo = ws['A2']
    subtitulo.value = f"Generado el {a_local(ahora_utc()).strftime('%d/%m/%Y %H:%M')}"
    subtitulo.font = Font(name="Calibri", size=10, italic=True, color="666666")
    subtitulo.alignment = Alignment(horizontal='center')

    header_row = 4
    thin = Side(style='thin', color="D9D9D9")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=header_row, column=col_idx, value=header)
        cell.font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        cell.fill = PatternFill(start_color=COLOR_PETROLEO, end_color=COLOR_PETROLEO, fill_type="solid")
        cell.alignment = Alignment(horizontal='center', vertical='center')
        cell.border = border

    total_costo = 0.0
    total_venta = 0.0
    row_idx = header_row + 1
    for prod in productos:
        cantidad = prod['cantidad'] or 0
        costo_unit = prod['precio_compra'] or 0
        venta_unit = prod['precio_venta'] or 0
        costo_total = costo_unit * cantidad
        venta_total = venta_unit * cantidad
        total_costo += costo_total
        total_venta += venta_total

        valores = [prod['nombre'], cantidad, costo_unit, venta_unit, costo_total, venta_total, prod['fecha_vencimiento'] or 'N/A']
        for col_idx, valor in enumerate(valores, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=valor)
            cell.border = border
            if col_idx in (3, 4, 5, 6):
                cell.number_format = '"Q"#,##0.00'
                cell.alignment = Alignment(horizontal='right')
            elif col_idx in (2, 7):
                cell.alignment = Alignment(horizontal='center')
            if row_idx % 2 == 0:
                cell.fill = PatternFill(start_color=COLOR_FONDO, end_color=COLOR_FONDO, fill_type="solid")
        row_idx += 1

    total_row = row_idx
    ws.merge_cells(start_row=total_row, start_column=1, end_row=total_row, end_column=4)
    total_label = ws.cell(row=total_row, column=1, value="TOTALES")
    total_label.font = Font(bold=True, color=COLOR_PETROLEO)
    total_label.alignment = Alignment(horizontal='right', vertical='center')
    total_label.fill = PatternFill(start_color=COLOR_ARENA, end_color=COLOR_ARENA, fill_type="solid")
    for col in range(1, 5):
        ws.cell(row=total_row, column=col).fill = PatternFill(start_color=COLOR_ARENA, end_color=COLOR_ARENA, fill_type="solid")

    for col_idx, total_val in ((5, total_costo), (6, total_venta)):
        cell = ws.cell(row=total_row, column=col_idx, value=total_val)
        cell.number_format = '"Q"#,##0.00'
        cell.font = Font(bold=True, color=COLOR_PETROLEO)
        cell.fill = PatternFill(start_color=COLOR_ARENA, end_color=COLOR_ARENA, fill_type="solid")
        cell.alignment = Alignment(horizontal='right')
    ws.cell(row=total_row, column=7).fill = PatternFill(start_color=COLOR_ARENA, end_color=COLOR_ARENA, fill_type="solid")

    ws.freeze_panes = f"A{header_row + 1}"
    ws.auto_filter.ref = f"A{header_row}:G{header_row}"

    column_widths = {1: 34, 2: 11, 3: 15, 4: 15, 5: 16, 6: 16, 7: 14}
    for col_idx, width in column_widths.items():
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    filename = f"Inventario_{hoy_local().isoformat()}.xlsx"
    return send_file(
        buffer,
        as_attachment=True,
        download_name=filename,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


@app.route('/inventario/item/nuevo', methods=['GET', 'POST'])
@login_required
def inventario_nuevo():
    if request.method == 'POST':
        error = procesar_formulario_inventario(get_db())
        if error:
            flash(error, 'error')
            return redirect(url_for('inventario_nuevo'))
        flash('Ítem de inventario creado con éxito.', 'success')
        return redirect(url_for('inventario'))
    return render_template('inventario_form.html', active_page='inventario', accion="Añadir", item=None)


@app.route('/inventario/item/editar/<int:id>', methods=['GET', 'POST'])
@login_required
def inventario_editar(id):
    conn = get_db()
    item = conn.execute('SELECT * FROM inventario WHERE id = ?', (id,)).fetchone()
    if item is None:
        abort(404)
    if request.method == 'POST':
        error = procesar_formulario_inventario(conn, item_id=id)
        if error:
            flash(error, 'error')
            return redirect(url_for('inventario_editar', id=id))
        flash('Ítem de inventario actualizado con éxito.', 'success')
        return redirect(url_for('inventario'))
    return render_template('inventario_form.html', active_page='inventario', accion="Editar", item=item)


@app.route('/inventario/stock/agregar/<int:id>', methods=['POST'])
@login_required
def inventario_agregar_stock(id):
    cantidad, desc = request.form.get('cantidad_a_agregar', 0, type=int), request.form.get('descripcion_movimiento') or 'Entrada de stock manual.'
    conn = get_db()
    item = conn.execute("SELECT tipo FROM inventario WHERE id = ? AND is_deleted = 0", (id,)).fetchone()
    if item is None or item['tipo'] != 'Producto':
        flash('Solo se puede agregar stock a un producto.', 'error')
    elif cantidad > 0:
        conn.execute('UPDATE inventario SET cantidad = CAST(COALESCE(cantidad, 0) AS INTEGER) + ? WHERE id = ?', (cantidad, id))
        conn.execute("INSERT INTO movimientos_inventario (id_item, tipo_movimiento, cantidad, descripcion) VALUES (?, 'Entrada', ?, ?)", (id, cantidad, desc))
        registrar_auditoria('entrada_stock', 'inventario', f'Agregó {cantidad} unidad(es) al artículo #{id}: {desc}', db=conn)
        conn.commit()
        flash(f'Se agregaron {cantidad} unidades al stock.', 'success')
    else:
        flash('La cantidad a agregar debe ser mayor que cero.', 'error')
    return redirect(url_for('inventario'))


@app.route('/inventario/stock/remover/<int:id>', methods=['POST'])
@login_required
@admin_required
def inventario_remover_stock(id):
    cantidad_a_remover = request.form.get('cantidad_a_remover', 0, type=int)
    descripcion = request.form.get('descripcion_movimiento') or 'Salida de stock manual.'
    conn = get_db()
    if cantidad_a_remover <= 0:
        flash('La cantidad a remover debe ser mayor que cero.', 'error')
        return redirect(url_for('inventario'))
    # Descuento atómico: nunca deja el stock en negativo aunque haya dos operaciones a la vez.
    cur = conn.execute("UPDATE inventario SET cantidad = CAST(COALESCE(cantidad, 0) AS INTEGER) - ? WHERE id = ? AND tipo = 'Producto' AND CAST(COALESCE(cantidad, 0) AS INTEGER) >= ?",
                       (cantidad_a_remover, id, cantidad_a_remover))
    if cur.rowcount != 1:
        conn.rollback()
        flash('No hay suficiente stock para remover la cantidad indicada.', 'error')
        return redirect(url_for('inventario'))
    conn.execute("INSERT INTO movimientos_inventario (id_item, tipo_movimiento, cantidad, descripcion) VALUES (?, 'Salida', ?, ?)", (id, cantidad_a_remover, descripcion))
    registrar_auditoria('salida_stock', 'inventario', f'Retiró {cantidad_a_remover} unidad(es) del artículo #{id}: {descripcion}', db=conn)
    conn.commit()
    flash(f'Se removieron {cantidad_a_remover} unidades del stock.', 'success')
    return redirect(url_for('inventario'))


@app.route('/inventario/historial')
@login_required
def inventario_historial():
    return render_template('inventario_historial.html', active_page='inventario_historial')


# --- CITAS ---
@app.route('/citas')
@login_required
def citas():
    conn = get_db()
    citas_pendientes = conn.execute("SELECT c.id, c.fecha_cita, c.hora_cita, c.descripcion, c.estado, p.nombre, p.apellido FROM citas c JOIN pacientes p ON c.id_paciente = p.id WHERE c.estado = 'Pendiente' ORDER BY c.fecha_cita ASC, c.hora_cita ASC").fetchall()
    return render_template('citas.html', active_page='citas', citas_pendientes=citas_pendientes)


@app.route('/citas/historial')
@login_required
def citas_historial():
    return render_template('citas_historial.html', active_page='citas')


def _hora_ocupada(conn, fecha, hora, excepto_id=None):
    return conn.execute("SELECT 1 FROM citas WHERE fecha_cita = ? AND hora_cita = ? AND estado = 'Pendiente' AND id <> ?",
                        (fecha, hora, excepto_id or 0)).fetchone() is not None


@app.route('/citas/nueva', methods=['GET', 'POST'])
@login_required
def cita_nueva():
    conn = get_db()
    if request.method == 'POST':
        id_paciente, fecha, hora, desc = request.form.get('id_paciente', type=int), request.form.get('fecha_cita'), request.form.get('hora_cita'), request.form.get('descripcion')
        if not id_paciente or not fecha or not hora:
            flash('Paciente, fecha y hora son obligatorios.', 'error')
        elif conn.execute('SELECT 1 FROM pacientes WHERE id = ? AND is_deleted = 0', (id_paciente,)).fetchone() is None:
            flash('El paciente seleccionado no existe.', 'error')
        elif _hora_ocupada(conn, fecha, hora):
            flash(f'Ya existe una cita agendada para el {fecha} a las {hora}.', 'error')
        else:
            conn.execute('INSERT INTO citas (id_paciente, fecha_cita, hora_cita, descripcion) VALUES (?, ?, ?, ?)', (id_paciente, fecha, hora, desc))
            conn.commit()
            flash('Cita agendada con éxito.', 'success')
            return redirect(url_for('citas'))
    pacientes = conn.execute('SELECT id, nombre, apellido FROM pacientes WHERE is_deleted = 0 ORDER BY nombre, apellido').fetchall()
    return render_template('cita_form.html', active_page='citas', accion="Agendar", pacientes=pacientes, cita=request.form)


@app.route('/citas/editar/<int:id>', methods=['GET', 'POST'])
@login_required
def cita_editar(id):
    conn = get_db()
    if conn.execute('SELECT 1 FROM citas WHERE id = ?', (id,)).fetchone() is None:
        abort(404)
    if request.method == 'POST':
        id_paciente, fecha, hora, desc = request.form.get('id_paciente', type=int), request.form.get('fecha_cita'), request.form.get('hora_cita'), request.form.get('descripcion')
        if not id_paciente or not fecha or not hora:
            flash('Todos los campos son obligatorios.', 'error')
        elif _hora_ocupada(conn, fecha, hora, excepto_id=id):
            flash(f'Ya existe otra cita agendada para el {fecha} a las {hora}.', 'error')
        else:
            conn.execute('UPDATE citas SET id_paciente=?, fecha_cita=?, hora_cita=?, descripcion=? WHERE id=?', (id_paciente, fecha, hora, desc, id))
            conn.commit()
            flash('Cita actualizada correctamente.', 'success')
            return redirect(url_for('citas'))

    cita = conn.execute("""
        SELECT c.*, p.nombre, p.apellido
        FROM citas c
        JOIN pacientes p ON c.id_paciente = p.id
        WHERE c.id = ?
    """, (id,)).fetchone()

    pacientes = conn.execute('SELECT id, nombre, apellido FROM pacientes WHERE is_deleted = 0 ORDER BY nombre, apellido').fetchall()
    return render_template('cita_form.html', active_page='citas', accion="Editar", pacientes=pacientes, cita=cita)


@app.route('/citas/atender/<int:id_cita>')
@login_required
def atender_cita(id_cita):
    db = get_db()
    caja_abierta = fin.turno_abierto(db)
    cita = db.execute("SELECT c.*, p.nombre, p.apellido FROM citas c JOIN pacientes p ON c.id_paciente = p.id WHERE c.id = ?", (id_cita,)).fetchone()
    if not cita:
        flash('Cita no encontrada.', 'error')
        return redirect(url_for('citas'))
    return render_template('venta_form.html', active_page='ventas', accion="Atender", cita_data=cita, caja_abierta=caja_abierta, token=uuid.uuid4().hex, metodos=fin.METODOS_VENTA)


@app.route('/citas/completar/<int:id>', methods=['POST'])
@login_required
def cita_completar(id):
    conn = get_db()
    conn.execute("UPDATE citas SET estado = 'Completada' WHERE id = ?", (id,))
    conn.commit()
    flash('Cita marcada como completada.', 'success')
    return redirect(url_for('citas'))


@app.route('/citas/cancelar/<int:id>', methods=['POST'])
@login_required
def cita_cancelar(id):
    conn = get_db()
    conn.execute("UPDATE citas SET estado = 'Cancelada' WHERE id = ?", (id,))
    conn.commit()
    flash('Cita cancelada.', 'success')
    return redirect(url_for('citas'))


# --- VENTAS ---
def _filtro_fecha_ventas(filtro, columna='v.fecha_venta'):
    hoy = hoy_local()
    if filtro == 'hoy':
        return f'{columna} BETWEEN ? AND ?', [inicio_utc(hoy), fin_utc(hoy)]
    if filtro == 'ayer':
        ayer = hoy - timedelta(days=1)
        return f'{columna} BETWEEN ? AND ?', [inicio_utc(ayer), fin_utc(ayer)]
    if filtro == 'mes':
        return f'{columna} BETWEEN ? AND ?', [inicio_utc(hoy.replace(day=1)), fin_utc(hoy)]
    return None, []


@app.route('/ventas')
@login_required
def ventas():
    db = get_db()
    texto = request.args.get('q', '').strip()
    filtro_fecha = request.args.get('fecha', 'todas')
    metodo = request.args.get('metodo', '')
    estado = request.args.get('estado', '')
    condiciones, params = ['1=1'], []
    if texto:
        condiciones.append("(p.nombre LIKE ? OR p.apellido LIKE ? OR (p.nombre || ' ' || p.apellido) LIKE ? OR CAST(v.id AS TEXT) = ? OR v.referencia_pago LIKE ?)")
        params += [f'%{texto}%'] * 3 + [texto.lstrip('#'), f'%{texto}%']
    condicion_fecha, p_fecha = _filtro_fecha_ventas(filtro_fecha)
    if condicion_fecha:
        condiciones.append(condicion_fecha)
        params += p_fecha
    if metodo in fin.METODOS_VENTA:
        condiciones.append('v.metodo_pago = ?')
        params.append(metodo)
    if estado == 'cancelada':
        condiciones.append("v.estado = 'Cancelada'")
    elif estado == 'pendiente':
        condiciones.append("v.estado <> 'Cancelada' AND v.saldo_pendiente > 0")
    elif estado == 'completada':
        condiciones.append("v.estado <> 'Cancelada'")
    where = ' AND '.join(condiciones)
    base = f"""FROM ventas v LEFT JOIN pacientes p ON p.id = v.id_paciente
               LEFT JOIN usuarios u ON u.id = v.id_usuario
               LEFT JOIN facturas fa ON fa.id_venta = v.id AND fa.tipo_doc = 'FACT'
               WHERE {where}"""
    filas, pag = paginar_consulta(
        db, f"""SELECT v.id, v.fecha_venta, v.monto_total, v.metodo_pago, v.referencia_pago, v.saldo_pendiente, v.estado_pago,
                       v.estado, p.nombre, p.apellido, u.nombre AS vendedor, fa.estado AS estado_factura {base}
                ORDER BY v.fecha_venta DESC, v.id DESC""", params)
    resumen = db.execute(f"""SELECT COUNT(*), COALESCE(SUM(v.monto_total), 0), COALESCE(SUM(v.saldo_pendiente), 0)
                             FROM ventas v LEFT JOIN pacientes p ON p.id = v.id_paciente
                             WHERE {where} AND v.estado <> 'Cancelada'""", params).fetchone()
    return render_template('ventas.html', active_page='ventas', ventas=filas, pag=pag, caja=fin.turno_abierto(db),
                           filtros={'q': texto, 'fecha': filtro_fecha, 'metodo': metodo, 'estado': estado},
                           resumen={'cantidad': resumen[0], 'total': D(resumen[1]), 'por_cobrar': D(resumen[2])},
                           metodos=fin.METODOS_VENTA)


def _valor_o_error(valor, minimo, maximo):
    try:
        numero = int(str(valor).strip())
    except (ValueError, TypeError):
        return None
    return numero if minimo <= numero <= maximo else None


def _leer_carrito(db):
    """Valida las líneas del formulario de venta. Devuelve (lineas, error)."""
    ids = request.form.getlist('item_ids[]')
    cantidades = request.form.getlist('quantities[]')
    descuentos = request.form.getlist('discounts[]')
    if not ids or not (len(ids) == len(cantidades) == len(descuentos)):
        return None, 'El carrito está vacío o es inválido.'
    lineas = {}
    for id_raw, cant_raw, desc_raw in zip(ids, cantidades, descuentos):
        id_item = _valor_o_error(id_raw, 1, 10**9)
        cantidad = _valor_o_error(cant_raw, 1, 100000)
        descuento = leer_monto(desc_raw or '0', permitir_cero=True)
        if id_item is None or cantidad is None or descuento is None:
            return None, 'Una línea del carrito tiene datos inválidos (cantidad o descuento).'
        if id_item in lineas:                       # la misma línea repetida: se suman las cantidades
            lineas[id_item]['cantidad'] += cantidad
            continue
        item = db.execute('SELECT * FROM inventario WHERE id = ? AND is_deleted = 0', (id_item,)).fetchone()
        if item is None:
            return None, 'Un artículo del carrito ya no existe en el inventario.'
        lineas[id_item] = {'item': item, 'cantidad': cantidad, 'descuento': descuento}
    for linea in lineas.values():
        item, precio = linea['item'], D(linea['item']['precio_venta'])
        linea['precio'] = precio
        linea['es_producto'] = str(item['tipo']).strip().lower() == 'producto'
        if linea['descuento'] > precio:
            return None, f"El descuento de '{item['nombre']}' no puede ser mayor que su precio."
        if linea['es_producto'] and (item['cantidad'] or 0) < linea['cantidad']:
            return None, f"No hay stock suficiente para '{item['nombre']}'."
    return list(lineas.values()), None


def _registrar_venta(db, turno):
    volver = redirect(url_for('venta_nueva'))
    if turno is None:
        flash('Debes abrir caja antes de vender.', 'error')
        return redirect(url_for('dinero.caja'))

    # Cada pantalla de venta trae un token único: si llega otra vez (doble clic, recargar, "atrás"),
    # no se duplica la venta, se muestra la que ya se hizo.
    token = request.form.get('token', '').strip()[:64]
    usados = session.get('venta_tokens', {})
    if token and token in usados:
        flash('Esa venta ya estaba registrada; no se duplicó.', 'success')
        return redirect(url_for('venta_detalle', id_venta=usados[token]))

    paciente = db.execute('SELECT id, nombre, apellido FROM pacientes WHERE id = ? AND is_deleted = 0',
                          (request.form.get('id_paciente', type=int),)).fetchone()
    if paciente is None:
        flash('Selecciona un paciente válido.', 'error')
        return volver
    lineas, error = _leer_carrito(db)
    if error:
        flash(error, 'error')
        return volver

    metodo = request.form.get('metodo_pago', '')
    referencia = request.form.get('referencia_pago', '').strip()[:100]
    if metodo not in fin.METODOS_VENTA:
        flash('Selecciona un método de pago válido.', 'error')
        return volver
    if metodo in fin.METODOS_CON_REFERENCIA and not referencia:
        flash('La referencia (número de autorización o boleta) es obligatoria para este método de pago.', 'error')
        return volver

    subtotal = sum(((l['precio'] - l['descuento']) * l['cantidad'] for l in lineas), Decimal(0))
    descuento_general = leer_monto(request.form.get('descuento_general') or '0', permitir_cero=True)
    if descuento_general is None or descuento_general > subtotal:
        flash('El descuento general no es válido (no puede ser mayor que el subtotal).', 'error')
        return volver
    total = subtotal - descuento_general
    if metodo == fin.CREDITO and total <= 0:
        flash('Una venta a crédito debe tener un total mayor que cero.', 'error')
        return volver

    id_cita = request.form.get('id_cita', type=int)
    if id_cita and db.execute("SELECT 1 FROM citas WHERE id = ? AND id_paciente = ?", (id_cita, paciente['id'])).fetchone() is None:
        id_cita = None

    try:
        a_credito = metodo == fin.CREDITO
        cur = db.execute(
            """INSERT INTO ventas (id_paciente, monto_total, descuento_total, metodo_pago, referencia_pago, saldo_pendiente,
                                   estado_pago, id_cita, id_caja, id_usuario) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (paciente['id'], f(total), f(descuento_general), metodo, referencia, f(total) if a_credito else 0,
             'Pendiente' if a_credito else 'Pagado', id_cita, turno['id'], current_user.id))
        id_venta = cur.lastrowid

        for l in lineas:
            item = l['item']
            db.execute("INSERT INTO ventas_detalle (id_venta, id_item_inventario, cantidad, precio_unitario, descuento, costo_unitario) VALUES (?,?,?,?,?,?)",
                       (id_venta, item['id'], l['cantidad'], f(l['precio']), f(l['descuento']),
                        item['precio_compra'] if l['es_producto'] else None))
            if l['es_producto']:
                # Descuento atómico: si otra venta se llevó el stock mientras tanto, falla y se deshace todo.
                bajo = db.execute("UPDATE inventario SET cantidad = CAST(COALESCE(cantidad, 0) AS INTEGER) - ? WHERE id = ? AND CAST(COALESCE(cantidad, 0) AS INTEGER) >= ?",
                                  (l['cantidad'], item['id'], l['cantidad']))
                if bajo.rowcount != 1:
                    raise ValueError(f"No hay stock suficiente para '{item['nombre']}'.")
                db.execute("INSERT INTO movimientos_inventario (id_item, tipo_movimiento, cantidad, descripcion) VALUES (?, 'Salida por Venta', ?, ?)",
                           (item['id'], l['cantidad'], f'Venta #{id_venta}'))

        # El dinero: cada venta deja su movimiento (o su cuenta por cobrar).
        referencia_dinero = f'venta-{id_venta}'
        detalle_pago = f' ({metodo.lower()}{", ref. " + referencia if referencia else ""})'
        concepto = f'Venta #{id_venta} — {paciente["nombre"]} {paciente["apellido"]}'
        if total > 0:
            if a_credito:
                db.execute("INSERT INTO cuentas_por_cobrar (id_paciente, id_venta, monto, saldo_pendiente, id_usuario) VALUES (?,?,?,?,?)",
                           (paciente['id'], id_venta, f(total), f(total), current_user.id))
            elif metodo == 'Efectivo':
                fin.mov_caja(db, turno['id'], 'venta', total, concepto, current_user.id, referencia_dinero)
            else:
                cuenta = fin.destino_de_cobro(metodo)
                fin.mov_fondo(db, cuenta, fin.tipo_fondo_de_venta(metodo), total, concepto + detalle_pago, current_user.id, referencia_dinero)

        if id_cita:
            db.execute("UPDATE citas SET estado = 'Completada' WHERE id = ?", (id_cita,))
        registrar_auditoria('crear_venta', 'ventas', f'Registró la venta #{id_venta} por {q(total)} ({metodo}).', db=db)
        db.commit()
    # Keep rollback and user feedback for any failure in the multi-table sale transaction.
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        flash(f'No se registró la venta: {exc}', 'error')
        return volver

    if token:
        usados[token] = id_venta
        session['venta_tokens'] = dict(list(usados.items())[-20:])
    flash(f'Venta #{id_venta} registrada.', 'success')
    return redirect(url_for('venta_detalle', id_venta=id_venta))


@app.route('/ventas/nueva', methods=['GET', 'POST'])
@login_required
def venta_nueva():
    db = get_db()
    turno = fin.turno_abierto(db)
    if request.method == 'POST':
        return _registrar_venta(db, turno)
    return render_template('venta_form.html', active_page='ventas', accion="Registrar", caja_abierta=turno, token=uuid.uuid4().hex, metodos=fin.METODOS_VENTA)


def motivo_no_cancelable(db, venta):
    """Texto que explica por qué una venta no se puede cancelar, o None si se puede."""
    factura = db.execute("SELECT estado FROM facturas WHERE id_venta = ? AND tipo_doc = 'FACT'", (venta['id'],)).fetchone()
    if factura is not None and factura['estado'] in ('certificado', 'indeterminado', 'pendiente'):
        return 'Esta venta tiene una factura electrónica vigente (o en proceso): anúlala primero en Facturación.'
    cuenta = db.execute("SELECT id FROM cuentas_por_cobrar WHERE id_venta = ? ORDER BY id DESC LIMIT 1", (venta['id'],)).fetchone()
    if cuenta is not None and db.execute("SELECT 1 FROM abonos WHERE id_cuenta = ? LIMIT 1", (cuenta['id'],)).fetchone():
        return ('Esta venta a crédito ya tiene abonos registrados, por eso no se puede cancelar. '
                'Una venta a crédito solo se cancela mientras no tenga abonos.')
    return None


def origenes_reembolso(db, venta):
    """Opciones [(clave, etiqueta, saldo)] de dónde puede salir el dinero al cancelar una venta
    ya cobrada. La primera es la sugerida. Las ventas anteriores al control de caja pueden
    cancelarse sin movimiento de dinero ('ninguno')."""
    total = D(venta['monto_total'])
    if total <= 0 or venta['metodo_pago'] == fin.CREDITO:
        return []
    if venta['metodo_pago'] == 'Efectivo':
        claves = ['caja', 'fondo']
        turno_venta = db.execute("SELECT estado FROM cierres_caja WHERE id = ?", (venta['id_caja'],)).fetchone() if venta['id_caja'] else None
        if turno_venta is None or turno_venta['estado'] != 'Abierta':
            claves = ['fondo', 'caja']
    elif venta['metodo_pago'] == 'Tarjeta':
        claves = ['por_consolidar', 'fondo']
    else:
        claves = ['fondo']
    opciones = []
    for clave in claves:
        if clave == 'caja' and fin.turno_abierto(db) is None:
            continue
        opciones.append((clave, fin.ORIGENES_ETIQUETA[clave].capitalize(), fin.saldo_origen(db, clave)))
    if venta['fecha_venta'] < fin.tesoreria_desde(db):
        opciones.append(('ninguno', 'No devolver dinero (venta anterior al control de caja)', None))
    return opciones


@app.route('/ventas/cancelar/<int:id_venta>', methods=['POST'])
@login_required
@admin_required
def cancelar_venta(id_venta):
    db = get_db()
    venta = db.execute('SELECT * FROM ventas WHERE id = ?', (id_venta,)).fetchone()
    if not venta:
        flash('Venta no encontrada.', 'error')
        return redirect(url_for('ventas'))
    volver = redirect(url_for('venta_detalle', id_venta=id_venta))
    if venta['estado'] == 'Cancelada':
        flash('Esta venta ya está cancelada.', 'warning')
        return volver
    motivo = request.form.get('motivo', '').strip()
    if len(motivo) < 5:
        flash('Explica el motivo de la cancelación (mínimo 5 caracteres).', 'error')
        return volver
    bloqueo = motivo_no_cancelable(db, venta)
    if bloqueo:
        flash(bloqueo, 'error')
        return volver

    # Si la venta se cobró, el dinero tiene que regresar de algún lado.
    opciones = origenes_reembolso(db, venta)
    origen = None
    if opciones:
        origen = request.form.get('origen') or opciones[0][0]
        if origen not in [o[0] for o in opciones]:
            flash('Selecciona de dónde sale el dinero a devolver.', 'error')
            return volver
        if origen != 'ninguno':
            error = fin.validar_salida(db, origen, venta['monto_total'])
            if error:
                flash(error, 'error')
                return volver

    try:
        for item in db.execute("SELECT * FROM ventas_detalle WHERE id_venta = ?", (id_venta,)).fetchall():
            prod = db.execute("SELECT tipo FROM inventario WHERE id = ?", (item['id_item_inventario'],)).fetchone()
            if prod and str(prod['tipo']).strip().lower() == 'producto':
                db.execute("UPDATE inventario SET cantidad = CAST(COALESCE(cantidad, 0) AS INTEGER) + ? WHERE id = ?", (item['cantidad'], item['id_item_inventario']))
                db.execute("INSERT INTO movimientos_inventario (id_item, tipo_movimiento, cantidad, descripcion) VALUES (?, 'Entrada por Cancelación', ?, ?)",
                           (item['id_item_inventario'], item['cantidad'], f'Cancelación Venta #{id_venta}'))
        db.execute("UPDATE cuentas_por_cobrar SET estado = ?, saldo_pendiente = 0, motivo_anulacion = ? WHERE id_venta = ?",
                   (fin.ANULADA, f'Cancelación de la venta #{id_venta}', id_venta))
        if origen and origen != 'ninguno':
            fin.registrar_salida(db, origen, D(venta['monto_total']), f'Cancelación de venta #{id_venta}: {motivo}', current_user.id,
                                 tipo_caja='anulacion_venta', tipo_fondo='anulacion_venta', referencia=f'anulacion-venta-{id_venta}')
        db.execute("""UPDATE ventas SET estado = 'Cancelada', saldo_pendiente = 0, motivo_cancelacion = ?, cancelada_en = ?, cancelada_por = ?
                      WHERE id = ?""", (motivo[:255], ahora_utc().strftime('%Y-%m-%d %H:%M:%S'), current_user.id, id_venta))
        devolucion = f' Dinero devuelto desde {fin.ORIGENES_ETIQUETA[origen]}.' if origen and origen != 'ninguno' else ''
        registrar_auditoria('cancelar_venta', 'ventas', f'Canceló la venta #{id_venta} ({q(venta["monto_total"])}). Motivo: {motivo}.{devolucion}', db=db)
        db.commit()
        flash(f'Venta #{id_venta} cancelada. El stock fue devuelto al inventario.', 'success')
    # Keep rollback and user feedback for any failure in the cancellation transaction.
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        flash(f'Error al cancelar la venta: {exc}', 'error')
    return volver


@app.route('/venta/detalle/<int:id_venta>')
@login_required
def venta_detalle(id_venta):
    db = get_db()
    venta = db.execute("""SELECT v.*, p.nombre, p.apellido, p.telefono, p.nit, u.nombre AS vendedor
                          FROM ventas v LEFT JOIN pacientes p ON v.id_paciente = p.id LEFT JOIN usuarios u ON u.id = v.id_usuario
                          WHERE v.id = ?""", (id_venta,)).fetchone()
    if not venta:
        flash('No encontrada.', 'error')
        return redirect(url_for('ventas'))
    detalles = db.execute("SELECT vd.*, i.nombre as nombre_item, i.tipo AS tipo_item FROM ventas_detalle vd JOIN inventario i ON vd.id_item_inventario = i.id WHERE vd.id_venta = ?", (id_venta,)).fetchall()
    cuenta = db.execute("SELECT * FROM cuentas_por_cobrar WHERE id_venta = ? ORDER BY id DESC LIMIT 1", (id_venta,)).fetchone()
    abonos = db.execute("SELECT a.*, u.nombre AS usuario FROM abonos a LEFT JOIN usuarios u ON u.id = a.id_usuario WHERE a.id_cuenta = ? ORDER BY a.fecha_abono DESC, a.id DESC", (cuenta['id'],)).fetchall() if cuenta else []
    subtotal = sum((D(d['precio_unitario']) - D(d['descuento'])) * d['cantidad'] for d in detalles)
    factura = db.execute("SELECT * FROM facturas WHERE id_venta = ? AND tipo_doc = 'FACT'", (id_venta,)).fetchone()
    config = db.execute("SELECT activo, ambiente FROM config_fiscal WHERE id = 1").fetchone()
    puede_facturar = bool(config and config['activo'] and venta['estado'] != 'Cancelada' and D(venta['monto_total']) > 0
                          and (factura is None or factura['estado'] == 'error'))
    no_cancelable = motivo_no_cancelable(db, venta) if venta['estado'] != 'Cancelada' else None
    opciones = origenes_reembolso(db, venta) if venta['estado'] != 'Cancelada' else []
    return render_template('venta_detalle.html', active_page='ventas', venta=venta, detalles=detalles, abonos=abonos, subtotal=subtotal,
                           cuenta=cuenta, factura=factura, fel_activo=bool(config and config['activo']), puede_facturar=puede_facturar,
                           no_cancelable=no_cancelable, opciones_reembolso=opciones)


# =============================================================================
# --- 8. PAPELERA Y ELIMINACIÓN PERMANENTE ---
# =============================================================================

@app.route('/papelera')
@login_required
@admin_required
def eliminados_recientemente():
    db = get_db()
    p_el = db.execute('SELECT * FROM pacientes WHERE is_deleted = 1 ORDER BY deleted_at DESC').fetchall()
    i_el = db.execute('SELECT * FROM inventario WHERE is_deleted = 1 ORDER BY deleted_at DESC').fetchall()
    return render_template('eliminados.html', active_page='eliminados_recientemente', pacientes=p_el, items_inventario=i_el)


def _historial_del_paciente(db, id_paciente):
    """Cuántos registros dependen de este paciente (impiden borrarlo para siempre)."""
    return {
        'expedientes': db.execute("SELECT COUNT(*) FROM expedientes WHERE id_paciente = ?", (id_paciente,)).fetchone()[0],
        'citas': db.execute("SELECT COUNT(*) FROM citas WHERE id_paciente = ?", (id_paciente,)).fetchone()[0],
        'ventas': db.execute("SELECT COUNT(*) FROM ventas WHERE id_paciente = ?", (id_paciente,)).fetchone()[0],
    }


@app.route('/pacientes/eliminar/<int:id>', methods=['POST'])
@login_required
@admin_required
def eliminar_paciente(id):
    db = get_db()
    debe = db.execute("SELECT COUNT(*) FROM cuentas_por_cobrar WHERE id_paciente = ? AND estado IN ('Pendiente','Parcial')", (id,)).fetchone()[0]
    if debe:
        flash('Este paciente tiene cuentas por cobrar pendientes: cóbralas o anúlalas antes de enviarlo a la papelera.', 'error')
        return redirect(url_for('pacientes'))
    db.execute("UPDATE pacientes SET is_deleted = 1, deleted_at = CURRENT_TIMESTAMP WHERE id = ?", (id,))
    registrar_auditoria('eliminar_paciente', 'pacientes', f'Envió a la papelera al paciente #{id}.', db=db)
    db.commit()
    flash('Paciente movido a la papelera.', 'success')
    return redirect(url_for('pacientes'))


@app.route('/pacientes/restaurar/<int:id>', methods=['POST'])
@login_required
@admin_required
def restaurar_paciente(id):
    db = get_db()
    db.execute("UPDATE pacientes SET is_deleted = 0, deleted_at = NULL WHERE id = ?", (id,))
    db.commit()
    flash('Paciente restaurado.', 'success')
    return redirect(url_for('eliminados_recientemente'))


@app.route('/pacientes/eliminar-permanente/<int:id>', methods=['POST'])
@login_required
@admin_required
def eliminar_paciente_permanente(id):
    db = get_db()
    historial = _historial_del_paciente(db, id)
    if any(historial.values()):
        flash(f'No se puede borrar para siempre: tiene {historial["expedientes"]} expediente(s), {historial["citas"]} cita(s) y '
              f'{historial["ventas"]} venta(s) registradas. Déjalo en la papelera para conservar el historial.', 'error')
        return redirect(url_for('eliminados_recientemente'))
    db.execute("DELETE FROM pacientes WHERE id = ? AND is_deleted = 1", (id,))
    registrar_auditoria('eliminar_paciente_permanente', 'pacientes', f'Borró definitivamente al paciente #{id}.', db=db)
    db.commit()
    flash('Paciente eliminado permanentemente.', 'success')
    return redirect(url_for('eliminados_recientemente'))


@app.route('/inventario/item/eliminar/<int:id>', methods=['POST'])
@login_required
@admin_required
def inventario_eliminar(id):
    db = get_db()
    db.execute('UPDATE inventario SET is_deleted = 1, deleted_at = CURRENT_TIMESTAMP WHERE id = ?', (id,))
    db.commit()
    flash('Ítem movido a papelera.', 'success')
    return redirect(url_for('inventario'))


@app.route('/inventario/item/restaurar/<int:id>', methods=['POST'])
@login_required
@admin_required
def inventario_restaurar(id):
    db = get_db()
    db.execute('UPDATE inventario SET is_deleted = 0, deleted_at = NULL WHERE id = ?', (id,))
    db.commit()
    flash('Ítem restaurado.', 'success')
    return redirect(url_for('eliminados_recientemente'))


@app.route('/inventario/item/eliminar-permanente/<int:id>', methods=['POST'])
@login_required
@admin_required
def inventario_eliminar_permanente(id):
    db = get_db()
    vendido = db.execute("SELECT COUNT(*) FROM ventas_detalle WHERE id_item_inventario = ?", (id,)).fetchone()[0]
    if vendido:
        flash(f'No se puede borrar para siempre: aparece en {vendido} línea(s) de venta. Déjalo en la papelera para conservar el historial.', 'error')
        return redirect(url_for('eliminados_recientemente'))
    it = db.execute("SELECT ruta_foto FROM inventario WHERE id = ? AND is_deleted = 1", (id,)).fetchone()
    if it is None:
        return redirect(url_for('eliminados_recientemente'))
    if it['ruta_foto']:
        try:
            os.remove(os.path.join(app.config['PRODUCT_UPLOAD_FOLDER'], it['ruta_foto']))
        except OSError:
            pass
    db.execute('DELETE FROM movimientos_inventario WHERE id_item = ?', (id,))
    db.execute('DELETE FROM inventario WHERE id = ?', (id,))
    db.commit()
    flash('Ítem borrado permanentemente.', 'success')
    return redirect(url_for('eliminados_recientemente'))


@app.route('/documento/eliminar/<int:id_documento>', methods=['POST'])
@login_required
@admin_required
def eliminar_documento(id_documento):
    db = get_db()
    doc = db.execute('SELECT id_expediente FROM documentos WHERE id_documento = ?', (id_documento,)).fetchone()
    if doc is None:
        abort(404)
    db.execute('UPDATE documentos SET is_deleted = 1, deleted_at = CURRENT_TIMESTAMP WHERE id_documento = ?', (id_documento,))
    db.commit()
    return redirect(url_for('expediente_editar', id_expediente=doc['id_expediente']) + '#documentos')


@app.route('/expediente/<int:id_expediente>/documentos_eliminados')
@login_required
@admin_required
def documentos_eliminados(id_expediente):
    db = get_db()
    expediente = db.execute('SELECT * FROM expedientes WHERE id_expediente = ?', (id_expediente,)).fetchone()
    if expediente is None:
        abort(404)
    paciente = db.execute('SELECT * FROM pacientes WHERE id = ?', (expediente['id_paciente'],)).fetchone()
    docs = db.execute('SELECT * FROM documentos WHERE id_expediente = ? AND is_deleted = 1 ORDER BY deleted_at DESC', (id_expediente,)).fetchall()
    return render_template('documentos_eliminados.html', paciente=paciente, expediente=expediente, documentos=docs)


@app.route('/documento/restaurar/<int:id_documento>', methods=['POST'])
@login_required
@admin_required
def restaurar_documento(id_documento):
    db = get_db()
    doc = db.execute('SELECT id_expediente FROM documentos WHERE id_documento = ?', (id_documento,)).fetchone()
    if doc is None:
        abort(404)
    db.execute('UPDATE documentos SET is_deleted = 0, deleted_at = NULL WHERE id_documento = ?', (id_documento,))
    db.commit()
    return redirect(url_for('documentos_eliminados', id_expediente=doc['id_expediente']))


@app.route('/documento/eliminar-permanente/<int:id_documento>', methods=['POST'])
@login_required
@admin_required
def eliminar_documento_permanente(id_documento):
    db = get_db()
    doc = db.execute('SELECT id_expediente, ruta_archivo FROM documentos WHERE id_documento = ?', (id_documento,)).fetchone()
    if doc is None:
        return redirect(url_for('pacientes'))
    try:
        os.remove(os.path.join(app.config['UPLOAD_FOLDER'], doc['ruta_archivo']))
    except OSError:
        pass
    db.execute('DELETE FROM documentos WHERE id_documento = ?', (id_documento,))
    db.commit()
    return redirect(url_for('documentos_eliminados', id_expediente=doc['id_expediente']))


# =============================================================================
# --- 9. MÓDULOS DE DINERO Y FACTURACIÓN ---
# =============================================================================
# Las rutas viven en sus propios archivos para no inflar este: caja, fondo, cuentas
# por cobrar y pagar, gastos, proveedores y reportes (rutas_dinero.py); facturación
# electrónica con Digifact (rutas_facturacion.py).
from rutas_dinero import bp as dinero_bp  # noqa: E402
from rutas_facturacion import bp as facturacion_bp  # noqa: E402
from rutas_ia import bp as ia_bp  # noqa: E402

app.register_blueprint(dinero_bp)
app.register_blueprint(facturacion_bp)
app.register_blueprint(ia_bp)


# Direcciones de la versión anterior: se mantienen para favoritos y enlaces guardados.
@app.route('/creditos')
@login_required
def creditos():
    return redirect(url_for('dinero.cxc_lista'))


@app.route('/pagos')
@login_required
def pagos():
    return redirect(url_for('dinero.cxp_lista'))


if __name__ == '__main__':
    # El servidor de desarrollo con depuración activa permite ejecutar código desde el navegador:
    # solo se enciende si se pide explícitamente y nunca en producción.
    app.run(debug=os.environ.get('FLASK_DEBUG') == '1' and not IS_PRODUCTION)
