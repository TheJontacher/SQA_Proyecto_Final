"""Facturación electrónica de Guatemala (FEL) con Digifact — API NUC.

Cliente y armado del documento (DTE tipo FACT). Portado del proyecto POS-MULTITENNANT
(adaptado de un sistema que ya opera contra Digifact): no se inventó ningún endpoint ni regla fiscal.
Documentación pública: https://documentacion.digifact.com/gt/api

Hechos verificados contra https://testnucgt.digifact.com/api:
- el login usa "GT.<NIT a 12 posiciones>.<usuario>";
- la respuesta trae el token como "Token";
- los endpoints protegidos esperan el JWT directo en Authorization (con el prefijo
  "Bearer " devuelven 401 aunque el token sea válido).

Alcance: emitir y anular facturas (FACT) y consultar el nombre de un NIT. No incluye notas
de crédito ni la conciliación automática de documentos "indeterminados" (cuando Digifact
certificó pero la respuesta no llegó): ese caso se resuelve con soporte de Digifact.
Este módulo no depende de Flask ni de la base de datos.
"""
import os
import re
import time
import uuid
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

import requests
from cryptography.fernet import Fernet, InvalidToken

DIGIFACT_TEST_BASE_URL = 'https://testnucgt.digifact.com/api'
DIGIFACT_PRODUCTION_BASE_URL = 'https://nucgt.digifact.com/gt.com.apinuc/api'
DIGIFACT_BASE_URLS = {'test': DIGIFACT_TEST_BASE_URL, 'production': DIGIFACT_PRODUCTION_BASE_URL}

IVA = Decimal('1.12')
REGIMENES = {'GEN': 'Régimen general (IVA 12%)', 'PEQ': 'Pequeño contribuyente (sin IVA discriminado)'}


class DigifactError(Exception):
    """Error de comunicación o de negocio devuelto por la API de Digifact."""

    def __init__(self, message, status_code=None, payload=None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {}


# =============================================================================
# --- NIT ---
# =============================================================================

def normalize_nit(nit, *, pad=False):
    """Normaliza un NIT guatemalteco conservando un dígito verificador K."""
    raw = str(nit or '').strip().upper()
    if re.search(r'[^0-9K\s-]', raw):
        return ''
    value = re.sub(r'[\s-]', '', raw)
    if not value or not re.fullmatch(r'\d+K?', value):
        return ''
    return value.zfill(12) if pad else value


def pad_nit(nit):
    """Normaliza un NIT a 12 caracteres con ceros a la izquierda para Digifact."""
    return normalize_nit(nit, pad=True)


def es_consumidor_final(texto):
    return str(texto or '').strip().upper().replace('/', '').replace('.', '').replace(' ', '') in ('', 'CF')


# =============================================================================
# --- CIFRADO DE LA CONTRASEÑA DE DIGIFACT ---
# =============================================================================

def separar_nombre(nombre):
    """Nombre completo de SAT -> (nombres, apellidos), en formato de título.

    SAT suele entregar "APELLIDOS,NOMBRES" ("PEREZ LOPEZ,JUAN CARLOS"). Sin coma se supone
    "NOMBRES APELLIDOS": con 2 palabras, 1+1; con 3, 1+2; con 4 o más, 2 nombres y el resto apellidos.
    Es una ayuda: la persona revisa y corrige en el formulario."""
    texto = re.sub(r'\s+', ' ', str(nombre or '')).strip()
    partes = [p.strip() for p in texto.split(',') if p.strip()]
    if len(partes) == 2:
        apellidos, nombres = partes
    elif len(partes) == 4:
        apellidos, nombres = f'{partes[0]} {partes[1]}', f'{partes[2]} {partes[3]}'
    else:
        palabras = texto.replace(',', ' ').split()
        if len(palabras) <= 1:
            nombres, apellidos = texto.replace(',', ' ').strip(), ''
        else:
            corte = 1 if len(palabras) <= 3 else 2
            nombres, apellidos = ' '.join(palabras[:corte]), ' '.join(palabras[corte:])
    return nombres.title(), apellidos.title()


def leer_o_crear_secreto(ruta, generar):
    """Lee el secreto guardado en ``ruta`` o lo crea. La creación es atómica: si dos procesos
    arrancan a la vez (varios workers del servidor), los dos terminan con el MISMO secreto."""
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    try:
        with open(ruta, 'xb') as archivo:           # 'x': falla si otro proceso ya lo creó
            secreto = generar()
            archivo.write(secreto)
    except FileExistsError:
        secreto = b''
        for _ in range(50):                          # el otro proceso puede estar escribiéndolo todavía
            with open(ruta, 'rb') as archivo:
                secreto = archivo.read().strip()
            if secreto:
                break
            time.sleep(0.1)
        return secreto
    try:
        os.chmod(ruta, 0o600)
    except OSError:
        pass
    return secreto.strip()


def cargar_clave_fiscal(ruta_instancia):
    """Clave Fernet para cifrar la contraseña de Digifact guardada en la base.
    Sale de FISCAL_ENCRYPTION_KEY o, si no existe, se genera una vez y se guarda en
    ``<instancia>/.fiscal_key``. Hay que respaldar esa clave: sin ella no se puede descifrar."""
    clave = os.environ.get('FISCAL_ENCRYPTION_KEY')
    if clave:
        return clave.strip().encode()
    return leer_o_crear_secreto(os.path.join(ruta_instancia, '.fiscal_key'), Fernet.generate_key)


class Cifrador:
    def __init__(self, clave):
        self._fernet = Fernet(clave)

    def cifrar(self, valor):
        return self._fernet.encrypt(valor.encode('utf-8')).decode('utf-8')

    def descifrar(self, token):
        try:
            return self._fernet.decrypt(token.encode('utf-8')).decode('utf-8')
        except InvalidToken:
            raise RuntimeError('No se pudo descifrar la contraseña de Digifact guardada: '
                               'la clave de cifrado cambió. Vuelve a escribir la contraseña en la configuración.')


# =============================================================================
# --- CLIENTE DE LA API ---
# =============================================================================

def _parse_json_response(resp):
    try:
        return resp.json()
    except ValueError:
        return {}


class DigifactClient:
    """Encapsula autenticación y llamadas a la API NUC de Digifact para GT."""

    def __init__(self, *, base_url, username, password, nit, timeout=30):
        self.base_url = base_url.rstrip('/')
        self.raw_username = username
        self.password = password
        self.raw_nit = normalize_nit(nit)
        self.nit = pad_nit(self.raw_nit)
        self.timeout = timeout
        self._token = None
        self._token_expires_at = 0

    @property
    def qualified_username(self):
        """Digifact exige 'GT.<NIT a 12 dígitos>.<usuario>' para autenticar."""
        if self.raw_username.startswith('GT.'):
            return self.raw_username
        return f'GT.{self.nit}.{self.raw_username}'

    def _login(self):
        resp = requests.post(
            f'{self.base_url}/login/get_token',
            json={'Username': self.qualified_username, 'Password': self.password},
            timeout=self.timeout)
        data = _parse_json_response(resp)
        token = data.get('Token') or data.get('token')
        if resp.status_code != 200 or not token:
            raise DigifactError(
                data.get('message') or data.get('response') or 'No se pudo autenticar con Digifact',
                status_code=resp.status_code, payload=data)
        self._token = token
        # Digifact no documenta la duración del token: se renueva cada 25 min.
        self._token_expires_at = time.time() + 25 * 60
        return self._token

    def _ensure_token(self):
        if not self._token or time.time() >= self._token_expires_at:
            self._login()
        return self._token

    def _request(self, method, path, *, params=None, json_body=None, retry_on_401=True):
        token = self._ensure_token()
        resp = requests.request(
            method, f'{self.base_url}{path}', params=params, json=json_body,
            headers={'Authorization': token}, timeout=self.timeout)
        if resp.status_code == 401 and retry_on_401:
            # El token expira sin aviso: al recibir 401 se pide uno nuevo y se reintenta una vez.
            self._token = None
            return self._request(method, path, params=params, json_body=json_body, retry_on_401=False)
        data = _parse_json_response(resp)
        # Digifact a veces responde HTTP 200 con un código de negocio distinto de éxito
        # ("code" en /nuc_json, "Codigo" en /CancelFelGT).
        result_code = data.get('code', data.get('Codigo'))
        business_failed = result_code is not None and str(result_code) != '1'
        if resp.status_code >= 400 or business_failed:
            message = (data.get('message') or data.get('Mensaje') or data.get('description')
                       or data.get('response') or f'Digifact respondió HTTP {resp.status_code}')
            description = data.get('description')
            if description and description.strip() and description.strip() not in message:
                message = f'{message}: {description.strip()}'
            raise DigifactError(message, status_code=resp.status_code, payload=data)
        return data

    @staticmethod
    def _validated_certificate(data, *, formats):
        required = {
            'autorización': data.get('authNumber') or data.get('AuthNumber'),
            'serie': data.get('batch') or data.get('Batch'),
            'número': data.get('serial') or data.get('Serial'),
        }
        requested = {part.strip().upper() for part in formats.split('|') if part.strip()}
        if 'XML' in requested:
            required['XML'] = data.get('responseData1') or data.get('ResponseDATA1')
        if 'PDF' in requested:
            required['PDF'] = data.get('responseData3') or data.get('ResponseDATA3')
        missing = [label for label, value in required.items() if not value]
        if missing:
            raise DigifactError(f'Respuesta de certificación incompleta: falta {", ".join(missing)}', payload=data)
        return data

    def emit_invoice(self, nuc_payload, *, formato='XML|PDF'):
        data = self._request(
            'POST', '/v2/transform/nuc_json',
            params={'TAXID': self.nit, 'FORMAT': formato, 'USERNAME': self.raw_username},
            json_body=nuc_payload)
        return self._validated_certificate(data, formats=formato)

    def cancel_invoice(self, *, auth_number, receiver_taxid, issued_at, reason):
        try:
            cancellation_issued_at = datetime.fromisoformat(issued_at).strftime('%Y-%m-%d %H:%M:%S')
        except (TypeError, ValueError):
            cancellation_issued_at = str(issued_at).replace('T', ' ', 1)
        return self._request(
            'POST', '/CancelFelGT',
            json_body={
                'Taxid': self.raw_nit,
                'Autorizacion': auth_number,
                'IdReceptor': receiver_taxid,
                'FechaEmisionDocumentoAnular': cancellation_issued_at,
                'MotivoAnulacion': reason,
                'Username': self.raw_username,
            })

    def lookup_nit(self, nit):
        """Consulta el nombre registrado en SAT para un NIT (vía Digifact)."""
        normalized = normalize_nit(nit)
        return self._request(
            'GET', '/Shared',
            params={'COUNTRY': 'GT', 'TAXID': self.nit, 'DATA1': 'SHARED_GETINFONITcom',
                    'DATA2': f'NIT|{normalized}', 'USERNAME': self.raw_username})


def crear_cliente(config, cifrador, *, ambiente=None):
    """Cliente listo para usar a partir de una fila de config_fiscal."""
    return DigifactClient(
        base_url=DIGIFACT_BASE_URLS.get(ambiente or config['ambiente'], DIGIFACT_TEST_BASE_URL),
        username=config['usuario_digifact'],
        password=cifrador.descifrar(config['password_cifrada']),
        nit=config['nit'])


# =============================================================================
# --- DOCUMENTO TRIBUTARIO (FACT) ---
# =============================================================================

DIRECCION_FISCAL = {
    'direccion': 'dirección', 'codigo_postal': 'código postal',
    'municipio': 'municipio', 'departamento': 'departamento',
}


def validar_direccion_fiscal(config):
    """Valida los datos de dirección fiscal sin inventar valores genéricos."""
    faltan = [etiqueta for campo, etiqueta in DIRECCION_FISCAL.items() if not str(config[campo] or '').strip()]
    if faltan:
        raise ValueError(f'Falta completar: {", ".join(faltan)}')
    if not re.fullmatch(r'\d{5}', str(config['codigo_postal']).strip()):
        raise ValueError('El código postal fiscal debe tener 5 dígitos')


def calcular_lineas_fact(lineas, descuento_global, con_iva=True):
    """Reparte descuentos e IVA (12%) por línea. Los precios de venta ya incluyen IVA.

    ``lineas``: lista de dicts {description, unit, qty, unit_price, unit_discount, servicio}
    donde ``unit_discount`` es el descuento por unidad de esa línea. ``descuento_global`` se
    prorratea entre las líneas según su peso; la última absorbe el residuo de centavos para que
    la suma de los totales sea exactamente lo cobrado.
    Con ``con_iva=False`` (pequeño contribuyente) el precio es el total de la línea y no se separa IVA.
    Devuelve (líneas calculadas, total del documento).
    """
    centavo, seis = Decimal('0.01'), Decimal('0.000001')
    descuento_global = Decimal(str(descuento_global or 0))
    calculadas = []
    for ln in lineas:
        qty = Decimal(str(ln['qty']))
        precio = Decimal(str(ln['unit_price']))
        bruto = (qty * precio).quantize(centavo, rounding=ROUND_HALF_UP)
        desc_linea = (qty * Decimal(str(ln.get('unit_discount') or 0))).quantize(centavo, rounding=ROUND_HALF_UP)
        calculadas.append({'ln': ln, 'qty': qty, 'precio': precio, 'bruto': bruto,
                           'desc_linea': desc_linea, 'neto': bruto - desc_linea})
    base = sum((c['neto'] for c in calculadas), Decimal(0))
    if descuento_global < 0 or descuento_global > base:
        raise ValueError('El descuento general no puede ser mayor que el subtotal')

    asignado = Decimal(0)
    resultado = []
    n = len(calculadas)
    for idx, c in enumerate(calculadas):
        if idx == n - 1:
            parte = descuento_global - asignado          # la última línea absorbe el residuo
        elif base > 0:
            parte = (descuento_global * c['neto'] / base).quantize(centavo, rounding=ROUND_HALF_UP)
            parte = min(parte, descuento_global - asignado)   # el redondeo nunca reparte de más
        else:
            parte = Decimal(0)
        asignado += parte
        total_item = c['bruto'] - c['desc_linea'] - parte
        if total_item < 0:
            raise ValueError('Los descuentos superan el valor de una línea')
        base_imponible = (total_item / IVA).quantize(seis, rounding=ROUND_HALF_UP) if con_iva else total_item
        resultado.append({
            'description': c['ln']['description'], 'unit': c['ln'].get('unit'), 'servicio': bool(c['ln'].get('servicio')),
            'qty': c['qty'], 'unit_price': c['precio'], 'discount_amount': c['desc_linea'] + parte,
            'taxable_amount': base_imponible, 'tax_amount': (total_item - base_imponible) if con_iva else Decimal(0), 'total_item': total_item,
        })
    return resultado, base - descuento_global


def construir_payload_fact(*, config, emitido_en, nombre_emisor, nombre_sucursal, nit_receptor, nombre_receptor,
                           lineas, total):
    """JSON NUC para certificar en Digifact: FACT (régimen general) o FPEQ (pequeño contribuyente)."""
    validar_direccion_fiscal(config)
    pequeno = (config['afiliacion_iva'] or 'GEN') == 'PEQ'
    items = []
    for numero, ln in enumerate(lineas, start=1):
        item = {
            'Number': str(numero), 'Type': 'Servicio' if ln['servicio'] else 'Bien',
            'Description': (ln['description'] or 'Producto')[:500],
            'Qty': round(float(ln['qty']), 6),
            'UnitOfMeasure': ln.get('unit') or 'UNI',
            'Price': round(float(ln['unit_price']), 6),
            'Totals': {'TotalItem': round(float(ln['total_item']), 6)},
        }
        # Digifact (5004) exige el nodo de impuestos en todo documento. El pequeño contribuyente lo declara
        # con IVA en cero: monto gravable = total del ítem e impuesto 0 (calcular_lineas_fact con con_iva=False).
        item['Taxes'] = {'Tax': [{
            'Code': '1', 'Description': 'IVA',
            'TaxableAmount': round(float(ln['taxable_amount']), 6),
            'Amount': round(float(ln['tax_amount']), 6),
        }]}
        if ln['discount_amount'] > 0:
            item['Discounts'] = {'Discount': [{'Description': 'Descuento', 'Amount': round(float(ln['discount_amount']), 6)}]}
        items.append(item)
    total_iva = round(sum(float(ln['tax_amount']) for ln in lineas), 6)

    vendedor = {
        # El query param TAXID va a 12 posiciones, pero el NITEmisor dentro del DTE conserva el NIT sin ceros de relleno.
        'TaxID': normalize_nit(config['nit']),
        'TaxIDAdditionalInfo': [{'Name': 'AfiliacionIVA', 'Data': None, 'Value': config['afiliacion_iva'] or 'GEN'}],
        'Name': nombre_emisor,
        'BranchInfo': {
            'Code': str(config['codigo_establecimiento'] or 1),
            'Name': (nombre_sucursal or nombre_emisor)[:200],
            'AddressInfo': {
                'Address': config['direccion'].strip(), 'City': config['codigo_postal'].strip(),
                'District': config['municipio'].strip(), 'State': config['departamento'].strip(),
                'Country': 'GT',
            },
        },
    }
    # FACT bajo afiliación GEN siempre requiere una frase tributaria. Sin configurar, se usa el
    # par base 1/1 (ISR sobre utilidades trimestrales).
    # FPEQ no discrimina IVA y Digifact exige la frase tipo 3 (pequeño contribuyente). Si la configuración
    # aún trae el par 1/x del régimen general, se usa 3/1.
    if pequeno:
        tipo_frase = 3
        escenario = (config['escenario_frase'] or 1) if config['tipo_frase'] == 3 else 1
    else:
        tipo_frase, escenario = config['tipo_frase'] or 1, config['escenario_frase'] or 1
    vendedor['AdditionlInfo'] = [
        {'Name': 'TipoFrase', 'Data': '1', 'Value': str(tipo_frase)},
        {'Name': 'Escenario', 'Data': '1', 'Value': str(escenario)},
    ]
    totales = {'TotalTaxes': {'TotalTax': [{'Description': 'IVA', 'Amount': total_iva}]},
               'GrandTotal': {'InvoiceTotal': round(float(total), 2)}}

    return {
        'Version': '1.00', 'CountryCode': 'GT',
        'Header': {'DocType': 'FPEQ' if pequeno else 'FACT', 'IssuedDateTime': emitido_en, 'Currency': 'GTQ'},
        'Seller': vendedor,
        'Buyer': {'TaxID': nit_receptor, 'Name': (nombre_receptor or 'Consumidor Final')[:100]},
        'Items': items,
        'Totals': totales,
        'AdditionalDocumentInfo': {
            'AdditionalInfo': [{
                'Code': str(uuid.uuid4()), 'Type': 'ADENDA',
                'AditionalInfo': [{'Name': 'VALIDAR_REFERENCIA_INTERNA', 'Data': None, 'Value': 'NO_VALIDAR'}],
            }],
        },
    }
