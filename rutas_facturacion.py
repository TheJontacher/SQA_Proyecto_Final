"""Rutas de la facturación electrónica (FEL) con Digifact: configuración fiscal, emitir y
anular facturas, descargar XML/PDF y consultar el nombre de un NIT."""
import base64
import os
import time

import requests
from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

import database
from comun import (
    ZONA_HORARIA_HORAS,
    D,
    a_local,
    admin_required,
    ahora_utc,
    f,
    get_db,
    paginar_consulta,
    q,
    registrar_auditoria,
)
from digifact import (
    DIGIFACT_BASE_URLS,
    REGIMENES,
    Cifrador,
    DigifactError,
    calcular_lineas_fact,
    cargar_clave_fiscal,
    construir_payload_fact,
    crear_cliente,
    es_consumidor_final,
    normalize_nit,
    pad_nit,
    separar_nombre,
    validar_direccion_fiscal,
)

bp = Blueprint('facturacion', __name__)

_cifrador = None


def cifrador():
    global _cifrador
    if _cifrador is None:
        _cifrador = Cifrador(cargar_clave_fiscal(os.path.join(database.RUTA_BASE, 'instance')))
    return _cifrador


def config_fiscal(db):
    return db.execute("SELECT * FROM config_fiscal WHERE id = 1").fetchone()


def _ahora_texto():
    return ahora_utc().strftime('%Y-%m-%d %H:%M:%S')


# =============================================================================
# --- CONFIGURACIÓN FISCAL (solo administradores) ---
# =============================================================================

@bp.route('/facturacion/configuracion', methods=['GET', 'POST'])
@login_required
@admin_required
def configuracion():
    db = get_db()
    config = config_fiscal(db)

    def mostrar(form):
        return render_template('facturacion/configuracion.html', active_page='config_fiscal', config=config, form=form)

    if request.method == 'POST':
        form = request.form
        for campo, etiqueta in (('nit', 'el NIT'), ('razon_social', 'la razón social'), ('usuario_digifact', 'el usuario de Digifact')):
            if not form.get(campo, '').strip():
                flash(f'Falta {etiqueta}.', 'error')
                return mostrar(form)
        nit = normalize_nit(form.get('nit'))
        if not nit or len(nit) > 12:
            flash('NIT inválido.', 'error')
            return mostrar(form)
        ambiente = form.get('ambiente') or 'test'
        if ambiente not in DIGIFACT_BASE_URLS:
            flash('Ambiente fiscal inválido.', 'error')
            return mostrar(form)
        try:
            establecimiento = int(form.get('codigo_establecimiento') or 1)
            tipo_frase = int(form['tipo_frase']) if form.get('tipo_frase') else None
            escenario = int(form['escenario_frase']) if form.get('escenario_frase') else None
        except ValueError:
            flash('El código de establecimiento, el tipo de frase y el escenario deben ser números.', 'error')
            return mostrar(form)
        if not 1 <= establecimiento <= 9999:
            flash('El código de establecimiento debe estar entre 1 y 9999.', 'error')
            return mostrar(form)
        if bool(tipo_frase) != bool(escenario):
            flash('El tipo de frase y el escenario deben indicarse juntos.', 'error')
            return mostrar(form)
        if tipo_frase is None:
            tipo_frase, escenario = 1, 1
        if tipo_frase < 1 or escenario < 1:
            flash('El tipo de frase y el escenario deben ser números positivos.', 'error')
            return mostrar(form)

        afiliacion = form.get('afiliacion_iva') or 'GEN'
        if afiliacion not in REGIMENES:
            flash('Régimen de IVA inválido.', 'error')
            return mostrar(form)

        if afiliacion == 'PEQ' and tipo_frase != 3:
            tipo_frase, escenario = 3, 1        # FPEQ exige la frase tipo 3; el par 1/1 es el del régimen general

        clave = form.get('password_digifact', '')
        if clave:
            clave_cifrada = cifrador().cifrar(clave)
        elif config and config['password_cifrada']:
            clave_cifrada = config['password_cifrada']
        else:
            flash('Debes indicar la contraseña de Digifact.', 'error')
            return mostrar(form)

        prefijo = f'GT.{pad_nit(nit)}.'
        usuario = form['usuario_digifact'].strip()
        usuario = usuario.removeprefix(prefijo)
        datos = {
            'ambiente': ambiente, 'nit': nit, 'razon_social': form['razon_social'].strip()[:200],
            'nombre_comercial': form.get('nombre_comercial', '').strip()[:200] or None, 'usuario_digifact': usuario,
            'password_cifrada': clave_cifrada, 'afiliacion_iva': afiliacion, 'codigo_establecimiento': establecimiento,
            'tipo_frase': tipo_frase, 'escenario_frase': escenario,
            'direccion': form.get('direccion', '').strip()[:300] or None, 'codigo_postal': form.get('codigo_postal', '').strip() or None,
            'municipio': form.get('municipio', '').strip()[:100] or None, 'departamento': form.get('departamento', '').strip()[:100] or None,
        }
        activar = form.get('activo') == 'on'
        if activar:
            try:
                validar_direccion_fiscal(datos)
            except ValueError as exc:
                flash(f'Se guardó la configuración, pero no se pudo activar: {exc}.', 'warning')
                activar = False
        datos.update({'activo': 1 if activar else 0, 'actualizado_por': current_user.id, 'actualizado_en': _ahora_texto()})

        columnas = list(datos)
        db.execute(f"INSERT OR REPLACE INTO config_fiscal (id, {', '.join(columnas)}) VALUES (1, {', '.join('?' * len(columnas))})",
                   [datos[c] for c in columnas])
        _CACHE_NIT.clear()
        registrar_auditoria('configurar_fel', 'facturacion', f'Actualizó la configuración de facturación electrónica (activa: {activar}, {ambiente}, {afiliacion}).', db=db)
        db.commit()
        flash('Configuración de facturación electrónica guardada.', 'success')
        return redirect(url_for('facturacion.configuracion'))
    return mostrar({})


@bp.route('/facturacion/configuracion/probar', methods=['POST'])
@login_required
@admin_required
def probar_conexion():
    """Pide un token a Digifact con las credenciales guardadas. No emite ni anula nada."""
    db = get_db()
    config = config_fiscal(db)
    if config is None:
        flash('Guarda primero la configuración.', 'error')
        return redirect(url_for('facturacion.configuracion'))
    try:
        crear_cliente(config, cifrador())._login()
        flash(f'Conexión correcta con Digifact ({"producción" if config["ambiente"] == "production" else "ambiente de prueba"}).', 'success')
    except DigifactError as exc:
        flash(f'Digifact rechazó las credenciales: {exc}', 'error')
    except requests.RequestException:
        flash('No se pudo comunicar con Digifact. Revisa la conexión a internet.', 'error')
    except RuntimeError as exc:
        flash(str(exc), 'error')
    return redirect(url_for('facturacion.configuracion'))


# =============================================================================
# --- DOCUMENTOS EMITIDOS ---
# =============================================================================

@bp.route('/facturacion/documentos')
@login_required
def documentos():
    db = get_db()
    busqueda = request.args.get('q', '').strip()
    condiciones, params = ['1=1'], []
    if busqueda:
        condiciones.append('(fa.nombre_receptor LIKE ? OR fa.numero_autorizacion LIKE ? OR CAST(fa.id_venta AS TEXT) = ?)')
        params += [f'%{busqueda}%', f'%{busqueda}%', busqueda.lstrip('#')]
    filas, pag = paginar_consulta(db, f"SELECT fa.* FROM facturas fa WHERE {' AND '.join(condiciones)} ORDER BY fa.id DESC", params)
    return render_template('facturacion/documentos.html', active_page='facturacion', documentos=filas, pag=pag, busqueda=busqueda,
                           config=config_fiscal(db))


@bp.route('/facturacion/documentos/<int:id_factura>/descargar/<tipo>')
@login_required
def descargar(id_factura, tipo):
    db = get_db()
    factura = db.execute("SELECT * FROM facturas WHERE id = ?", (id_factura,)).fetchone()
    if factura is None:
        abort(404)
    campos = {
        'xml': (factura['xml_base64'], 'application/xml', 'xml'),
        'pdf': (factura['pdf_base64'], 'application/pdf', 'pdf'),
        'cancel_xml': (factura['anulacion_xml_base64'], 'application/xml', 'xml'),
        'cancel_pdf': (factura['anulacion_pdf_base64'], 'application/pdf', 'pdf'),
    }
    if tipo not in campos or not campos[tipo][0]:
        abort(404)
    contenido, mimetype, extension = campos[tipo]
    return Response(base64.b64decode(contenido), mimetype=mimetype,
                    headers={'Content-Disposition': f'attachment; filename="V-{factura["id_venta"]:06d}-{tipo}.{extension}"'})


# =============================================================================
# --- EMITIR Y ANULAR ---
# =============================================================================

def _receptor(form, paciente):
    """(nit, nombre, error). Sin NIT o con 'CF' se factura a Consumidor Final."""
    texto = form.get('nit_receptor', '')
    if es_consumidor_final(texto):
        return 'CF', 'Consumidor Final', None
    nit = normalize_nit(texto)
    if not nit or len(nit) > 12:
        return None, None, 'El NIT del receptor no es válido.'
    nombre = form.get('nombre_receptor', '').strip() or f'{paciente["nombre"]} {paciente["apellido"]}'
    return nit, nombre[:100], None


@bp.route('/ventas/<int:id_venta>/facturar', methods=['POST'])
@login_required
def facturar(id_venta):
    db = get_db()
    venta = db.execute("SELECT * FROM ventas WHERE id = ?", (id_venta,)).fetchone()
    if venta is None:
        abort(404)
    volver = redirect(url_for('venta_detalle', id_venta=id_venta))
    if venta['estado'] == 'Cancelada':
        flash('Solo se pueden facturar ventas que no estén canceladas.', 'error')
        return volver
    if D(venta['monto_total']) <= 0:
        flash('No se factura una venta con total cero.', 'error')
        return volver

    factura = db.execute("SELECT * FROM facturas WHERE id_venta = ? AND tipo_doc = 'FACT'", (id_venta,)).fetchone()
    if factura and factura['estado'] in ('certificado', 'anulado'):
        flash('Esta venta ya tiene una factura electrónica.', 'error')
        return volver
    if factura and factura['estado'] in ('pendiente', 'indeterminado'):
        flash('La emisión está en proceso o requiere resolución manual con Digifact; no se reintenta para evitar un documento duplicado.', 'error')
        return volver

    config = config_fiscal(db)
    if not config or not config['activo']:
        flash('La facturación electrónica no está activa. Configúrala en Administración → Config. de facturación.', 'error')
        return volver

    paciente = db.execute("SELECT nombre, apellido FROM pacientes WHERE id = ?", (venta['id_paciente'],)).fetchone()
    nit_receptor, nombre_receptor, error = _receptor(request.form, paciente)
    if error:
        flash(error, 'error')
        return volver

    detalle = db.execute("""SELECT vd.cantidad, vd.precio_unitario, vd.descuento, i.nombre, i.tipo FROM ventas_detalle vd
                            JOIN inventario i ON i.id = vd.id_item_inventario WHERE vd.id_venta = ? ORDER BY vd.id""", (id_venta,)).fetchall()
    if not detalle:
        flash('La venta no tiene líneas para facturar.', 'error')
        return volver
    try:
        lineas, total = calcular_lineas_fact(
            [{'description': d['nombre'], 'unit': 'UNI', 'qty': d['cantidad'], 'unit_price': D(d['precio_unitario']),
              'unit_discount': D(d['descuento']), 'servicio': d['tipo'] == 'Servicio'} for d in detalle],
            D(venta['descuento_total']), con_iva=(config['afiliacion_iva'] or 'GEN') != 'PEQ')
        if D(total) != D(venta['monto_total']):
            raise ValueError(f'El total calculado ({q(total)}) no coincide con el de la venta ({q(venta["monto_total"])})')
        # La factura se emite con la fecha y hora de este momento (hora local con su desfase), no con la
        # de la venta: SAT rechaza documentos con fecha atrasada.
        emitido_en = a_local(ahora_utc()).strftime('%Y-%m-%dT%H:%M:%S') + f'{ZONA_HORARIA_HORAS:+03d}:00'
        payload = construir_payload_fact(
            config=config, emitido_en=emitido_en, nombre_emisor=config['razon_social'], nombre_sucursal=config['nombre_comercial'],
            nit_receptor=nit_receptor, nombre_receptor=nombre_receptor, lineas=lineas, total=total)
    except ValueError as exc:
        flash(f'No se puede facturar: {exc}.', 'error')
        return volver

    # Se reserva la factura como 'pendiente' ANTES de llamar a un servicio externo: así un reintento
    # rápido (doble clic) no emite dos veces.
    datos = (config['ambiente'], nit_receptor, nombre_receptor, f(total), emitido_en, normalize_nit(config['nit']), current_user.id)
    if factura is None:
        db.execute("""INSERT INTO facturas (id_venta, tipo_doc, estado, ambiente, nit_receptor, nombre_receptor, total, emision_snapshot,
                      nit_emisor_snapshot, id_usuario) VALUES (?, 'FACT', 'pendiente', ?, ?, ?, ?, ?, ?, ?)""", (id_venta, *datos))
    else:
        db.execute("""UPDATE facturas SET estado = 'pendiente', ambiente = ?, nit_receptor = ?, nombre_receptor = ?, total = ?, emision_snapshot = ?,
                      nit_emisor_snapshot = ?, id_usuario = ?, mensaje_error = NULL WHERE id = ?""", (*datos, factura['id']))
    db.commit()
    id_factura = db.execute("SELECT id FROM facturas WHERE id_venta = ? AND tipo_doc = 'FACT'", (id_venta,)).fetchone()['id']

    try:
        resultado = crear_cliente(config, cifrador()).emit_invoice(payload, formato='XML|PDF')
        autorizacion = resultado.get('authNumber') or resultado.get('AuthNumber')
        db.execute("""UPDATE facturas SET estado = 'certificado', numero_autorizacion = ?, serie = ?, numero = ?, xml_base64 = ?, pdf_base64 = ?
                      WHERE id = ?""",
                   (autorizacion, resultado.get('batch') or resultado.get('Batch'), resultado.get('serial') or resultado.get('Serial'),
                    resultado.get('responseData1') or resultado.get('ResponseDATA1'),
                    resultado.get('responseData3') or resultado.get('ResponseDATA3'), id_factura))
        registrar_auditoria('emitir_fel', 'facturacion', f'Emitió la factura electrónica de la venta #{id_venta} (autorización {autorizacion}).', db=db)
        db.commit()
        flash('Factura electrónica emitida y certificada.', 'success')
    except DigifactError as exc:
        db.execute("UPDATE facturas SET estado = 'error', mensaje_error = ? WHERE id = ?", (str(exc)[:1000], id_factura))
        db.commit()
        flash(f'Digifact rechazó la emisión: {exc}', 'error')
    except requests.RequestException as exc:
        # Pudo certificar remotamente aunque la respuesta no llegó: no se reintenta solo, para no duplicar.
        db.execute("UPDATE facturas SET estado = 'indeterminado', mensaje_error = ? WHERE id = ?", (str(exc)[:1000], id_factura))
        db.commit()
        flash('No se confirmó el resultado de Digifact (falla de red). No reintentes: contacta a soporte de Digifact para verificar '
              'si el documento se certificó antes de emitir uno nuevo.', 'error')
    except RuntimeError as exc:
        db.execute("UPDATE facturas SET estado = 'error', mensaje_error = ? WHERE id = ?", (str(exc)[:1000], id_factura))
        db.commit()
        flash(str(exc), 'error')
    return volver


@bp.route('/facturacion/documentos/<int:id_factura>/anular', methods=['POST'])
@login_required
@admin_required
def anular(id_factura):
    db = get_db()
    factura = db.execute("SELECT * FROM facturas WHERE id = ?", (id_factura,)).fetchone()
    if factura is None:
        abort(404)
    volver = redirect(url_for('venta_detalle', id_venta=factura['id_venta']))
    if factura['estado'] != 'certificado':
        flash('Solo se puede anular una factura certificada.', 'error')
        return volver
    motivo = request.form.get('motivo', '').strip()
    if len(motivo) < 5:
        flash('Indica el motivo de anulación (mínimo 5 caracteres).', 'error')
        return volver
    config = config_fiscal(db)
    if config is None:
        flash('No hay configuración fiscal.', 'error')
        return volver
    try:
        cliente = crear_cliente(config, cifrador(), ambiente=factura['ambiente'])
        resultado = cliente.cancel_invoice(auth_number=factura['numero_autorizacion'], receiver_taxid=factura['nit_receptor'],
                                           issued_at=factura['emision_snapshot'], reason=motivo)
        db.execute("""UPDATE facturas SET estado = 'anulado', motivo_anulacion = ?, anulada_en = ?, anulacion_xml_base64 = ?, anulacion_pdf_base64 = ?
                      WHERE id = ?""",
                   (motivo[:300], _ahora_texto(), resultado.get('ResponseDATA1') or resultado.get('responseData1'),
                    resultado.get('ResponseDATA3') or resultado.get('responseData3'), id_factura))
        registrar_auditoria('anular_fel', 'facturacion', f'Anuló la factura electrónica de la venta #{factura["id_venta"]}.', db=db)
        # La anulación externa no se puede revertir: se guarda de inmediato.
        db.commit()
        flash('Factura electrónica anulada.', 'success')
    except DigifactError as exc:
        flash(f'Digifact rechazó la anulación: {exc}', 'error')
    except requests.RequestException:
        flash('No se pudo comunicar con Digifact.', 'error')
    except RuntimeError as exc:
        flash(str(exc), 'error')
    return volver


# =============================================================================
# --- CONSULTA DE NIT ---
# =============================================================================

# Las consultas repetidas del mismo NIT se responden de memoria unos minutos, para no gastar llamadas.
_CACHE_NIT = {}
CACHE_NIT_SEGUNDOS = 600
CACHE_NIT_MAXIMO = 500


def _respuesta_nit(nombre):
    nombres, apellidos = separar_nombre(nombre)
    return {'status': 'success', 'nombre': nombre, 'nombres': nombres, 'apellidos': apellidos}


@bp.route('/nit/<nit>')
@login_required
def consultar_nit(nit):
    """Autocompleta el nombre a partir de un NIT (clientes, proveedores y la configuración fiscal)."""
    if es_consumidor_final(nit):
        return {'status': 'success', 'nombre': 'Consumidor Final'}
    normalizado = normalize_nit(nit)
    if not normalizado:
        return {'status': 'error', 'message': 'El NIT no es válido.'}, 400
    db = get_db()
    config = config_fiscal(db)
    if config is None or not config['activo']:
        return {'status': 'error', 'message': 'La consulta de NIT estará disponible cuando se active la facturación electrónica.'}, 503
    guardado = _CACHE_NIT.get(normalizado)
    if guardado and (time.time() - guardado[0]) < CACHE_NIT_SEGUNDOS:
        return _respuesta_nit(guardado[1])
    try:
        resultado = crear_cliente(config, cifrador()).lookup_nit(normalizado)
        filas = resultado.get('RESPONSE') or []
        nombre = filas[0].get('NOMBRE') if filas else None
        if not nombre:
            return {'status': 'error', 'message': 'No se encontró información para ese NIT.'}, 404
        if len(_CACHE_NIT) >= CACHE_NIT_MAXIMO:
            _CACHE_NIT.clear()
        _CACHE_NIT[normalizado] = (time.time(), nombre)
        return _respuesta_nit(nombre)
    except DigifactError as exc:
        return {'status': 'error', 'message': f'Digifact rechazó la consulta: {exc}'}, 502
    except (requests.RequestException, RuntimeError):
        return {'status': 'error', 'message': 'No se pudo comunicar con Digifact.'}, 502
