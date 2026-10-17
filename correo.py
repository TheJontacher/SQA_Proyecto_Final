"""Correo por SMTP (contraseña de aplicación) y códigos de verificación en 2 pasos.

La contraseña SMTP se guarda cifrada en ``config_correo`` (nunca en el código). Los códigos de
verificación son de 6 dígitos, valen 10 minutos, se usan una sola vez y de ellos solo se guarda su huella.
"""
import hashlib
import hmac
import re
import secrets
import smtplib
import ssl
from datetime import timedelta
from email.message import EmailMessage
from email.utils import formataddr

from comun import FORMATO_BD, a_datetime, ahora_utc
from rutas_facturacion import cifrador

MINUTOS_VALIDEZ_CODIGO = 10
MAX_INTENTOS_CODIGO = 5
SEGUNDOS_ENTRE_ENVIOS = 60
PROPOSITO_LOGIN = 'login'
PROPOSITO_CORREO = 'verificar_correo'
SEGURIDADES = ('starttls', 'ssl')

_PATRON_EMAIL = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


class CorreoError(Exception):
    """Mensaje pensado para mostrarse al usuario."""


def email_valido(texto):
    return bool(texto) and len(texto) <= 254 and bool(_PATRON_EMAIL.match(texto))


def config_correo(db):
    return db.execute("SELECT * FROM config_correo WHERE id = 1").fetchone()


def correo_activo(db):
    """El correo está configurado Y un envío de prueba ya salió bien. Solo entonces se exige el código de 2 pasos."""
    config = config_correo(db)
    return config is not None and bool(config['probado_en'])


def marcar_probado(db):
    db.execute("UPDATE config_correo SET probado_en = ? WHERE id = 1", (ahora_utc().strftime(FORMATO_BD),))
    db.commit()


def enviar_correo(config, destinatarios, asunto, texto, html=None):
    """Envía un correo con la configuración guardada. Lanza CorreoError con un motivo legible."""
    if config is None:
        raise CorreoError('El correo saliente no está configurado (Administración → Configuración).')
    if isinstance(destinatarios, str):
        destinatarios = [destinatarios]
    try:
        clave = cifrador().descifrar(config['password_cifrada'])
    except RuntimeError as exc:
        raise CorreoError(str(exc))

    mensaje = EmailMessage()
    mensaje['Subject'] = asunto
    mensaje['From'] = formataddr((config['remitente_nombre'] or 'Clínica', config['usuario']))
    mensaje['To'] = ', '.join(destinatarios)
    mensaje.set_content(texto)
    if html:
        mensaje.add_alternative(html, subtype='html')

    contexto = ssl.create_default_context()
    try:
        if config['seguridad'] == 'ssl':
            servidor = smtplib.SMTP_SSL(config['servidor'], config['puerto'], timeout=25, context=contexto)
        else:
            servidor = smtplib.SMTP(config['servidor'], config['puerto'], timeout=25)
        with servidor:
            if config['seguridad'] != 'ssl':
                servidor.starttls(context=contexto)
            servidor.login(config['usuario'], clave)
            servidor.send_message(mensaje)
    except smtplib.SMTPAuthenticationError:
        raise CorreoError('El servidor de correo rechazó el usuario o la contraseña de aplicación.')
    except smtplib.SMTPRecipientsRefused:
        raise CorreoError('El servidor de correo rechazó la dirección de destino.')
    except (smtplib.SMTPException, OSError) as exc:
        raise CorreoError(f'No se pudo enviar el correo ({type(exc).__name__}).')


# =============================================================================
# --- CÓDIGOS DE VERIFICACIÓN ---
# =============================================================================

def _huella(clave_app, id_usuario, proposito, codigo):
    mensaje = f'{id_usuario}:{proposito}:{codigo}'.encode()
    return hmac.new(clave_app.encode(), mensaje, hashlib.sha256).hexdigest()


def segundos_para_reenviar(db, id_usuario, proposito):
    """0 si ya se puede enviar otro código; si no, cuántos segundos faltan."""
    fila = db.execute("SELECT creado_en FROM codigos_verificacion WHERE id_usuario = ? AND proposito = ? ORDER BY id DESC LIMIT 1",
                      (id_usuario, proposito)).fetchone()
    if not fila:
        return 0
    pasado = (ahora_utc() - a_datetime(fila['creado_en'])).total_seconds()
    return max(0, int(SEGUNDOS_ENTRE_ENVIOS - pasado))


def enviar_codigo(db, clave_app, usuario, proposito, email, titulo_correo):
    """Genera un código nuevo (invalida los anteriores), lo guarda con su huella y lo envía por correo.
    Hace commit. Lanza CorreoError si no se puede enviar (y entonces el código no queda activo)."""
    codigo = f'{secrets.randbelow(10 ** 6):06d}'
    db.execute("UPDATE codigos_verificacion SET usado = 1 WHERE id_usuario = ? AND proposito = ? AND usado = 0",
               (usuario['id'], proposito))
    expira = (ahora_utc() + timedelta(minutes=MINUTOS_VALIDEZ_CODIGO)).strftime(FORMATO_BD)
    db.execute("INSERT INTO codigos_verificacion (id_usuario, proposito, email, codigo_hash, expira_en) VALUES (?, ?, ?, ?, ?)",
               (usuario['id'], proposito, email, _huella(clave_app, usuario['id'], proposito, codigo), expira))
    texto = (f'Hola {usuario["nombre"]},\n\nTu código de verificación es: {codigo}\n\n'
             f'Vence en {MINUTOS_VALIDEZ_CODIGO} minutos. Si no fuiste tú, ignora este mensaje y cambia tu contraseña.\n')
    html = (f'<div style="font-family:Arial,sans-serif;max-width:420px;margin:auto;padding:24px;border:1px solid #e5e7eb;border-radius:12px">'
            f'<h2 style="color:#23807A;margin-top:0">{titulo_correo}</h2><p>Hola {_esc(usuario["nombre"])}, tu código de verificación es:</p>'
            f'<p style="font-size:34px;letter-spacing:8px;font-weight:bold;text-align:center;margin:20px 0">{codigo}</p>'
            f'<p style="color:#6b7280;font-size:13px">Vence en {MINUTOS_VALIDEZ_CODIGO} minutos. Si no fuiste tú, ignora este mensaje y cambia tu contraseña.</p></div>')
    try:
        enviar_correo(config_correo(db), email, f'{codigo} es tu código de verificación', texto, html)
    except CorreoError:
        db.rollback()
        raise
    db.commit()


def comprobar_codigo(db, clave_app, id_usuario, proposito, codigo):
    """Devuelve (ok, motivo). Cuenta los intentos fallidos y gasta el código al acertar."""
    fila = db.execute("SELECT * FROM codigos_verificacion WHERE id_usuario = ? AND proposito = ? AND usado = 0 ORDER BY id DESC LIMIT 1",
                      (id_usuario, proposito)).fetchone()
    if not fila:
        return False, 'No hay un código vigente. Pide uno nuevo.'
    if a_datetime(fila['expira_en']) < ahora_utc():
        db.execute("UPDATE codigos_verificacion SET usado = 1 WHERE id = ?", (fila['id'],))
        db.commit()
        return False, 'El código venció. Pide uno nuevo.'
    if fila['intentos'] >= MAX_INTENTOS_CODIGO:
        db.execute("UPDATE codigos_verificacion SET usado = 1 WHERE id = ?", (fila['id'],))
        db.commit()
        return False, 'Demasiados intentos fallidos. Pide un código nuevo.'
    esperado = _huella(clave_app, id_usuario, proposito, (codigo or '').strip())
    if not hmac.compare_digest(esperado, fila['codigo_hash']):
        db.execute("UPDATE codigos_verificacion SET intentos = intentos + 1 WHERE id = ?", (fila['id'],))
        db.commit()
        return False, 'Código incorrecto.'
    db.execute("UPDATE codigos_verificacion SET usado = 1 WHERE id = ?", (fila['id'],))
    db.commit()
    return True, ''


def _esc(texto):
    return (str(texto).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;'))
