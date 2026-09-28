# Copyright (c) 2026, Josue Velasquez and contributors
# For license information, please see license.txt
"""Cálculo de saldos pendientes de pago a proveedores.

Equivalente, para cuentas por pagar, de `pagos_chappsa.saldos`. A diferencia de
aquel módulo, aquí no hay Origen que elegir: el único documento que se paga es
la Factura de Compra.

Convención de signos (igual que en cobranza):

	saldo = valor_original - abonado

	* valor_original > 0  ->  CARGO   (se le debe al proveedor: factura normal)
	* valor_original < 0  ->  CRÉDITO (a favor de la empresa: devolución o anticipo)
"""

import frappe
from frappe import _
from frappe.utils import flt

TOLERANCIA = 0.005

# Documento que representa un anticipo: el propio Pago Proveedor que dejó excedente.
TIPO_ANTICIPO = "Pago Proveedor"

TIPO_ACTIVO = "Purchase Invoice"

CONFIG_DOCUMENTOS = {
	"Purchase Invoice": {
		"proveedor": "supplier",
		"empresa": "company",
		"moneda": "currency",
		"fecha": "posting_date",
		"valor": "grand_total",
		"referencia": "bill_no",
		"filtros": {"docstatus": 1},
		"etiqueta": "Factura de Compra",
	},
}


def get_activo_global():
	"""True si existe un Perfil de Pago Proveedor marcado como Global.

	Es el interruptor de activación: sin un perfil Global configurado, nada de
	`integracion_nativa_proveedor` toca facturas ni pagos nativos.
	"""

	def _calcular():
		return bool(frappe.db.exists("Perfil de Pago Proveedor", {"es_global": 1}))

	return frappe.cache.get_value("pagos_chappsa_proveedor_activo_global", generator=_calcular)


def get_config(tipo_documento):
	cfg = CONFIG_DOCUMENTOS.get(tipo_documento)
	if not cfg:
		frappe.throw(_("Tipo de documento no soportado para pagos a proveedores: {0}").format(tipo_documento))
	return cfg


def get_referencia_campo_proveedor(tipo_documento):
	"""Campo de "número interno" de `tipo_documento`, o None si no aplica.

	Nombre distinto del de `pagos_chappsa.saldos.get_referencia_campo` a propósito:
	ambos se registran como método Jinja global y un mismo nombre pisaría al otro
	en el entorno de plantillas.
	"""
	cfg = CONFIG_DOCUMENTOS.get(tipo_documento)
	return cfg.get("referencia") if cfg else None


# ---------------------------------------------------------------------------
# Abonos ya registrados
# ---------------------------------------------------------------------------
def get_abonos(claves, excluir_pago=None):
	"""Suma de abonos validados por documento.

	:param claves: iterable de tuplas (tipo_documento, documento)
	:param excluir_pago: nombre de un Pago Proveedor a ignorar (el que se edita)
	:return: dict {(tipo_documento, documento): abonado}
	"""
	por_tipo = {}
	for tipo, documento in claves:
		por_tipo.setdefault(tipo, set()).add(documento)

	if not por_tipo:
		return {}

	condiciones, valores = [], []
	for tipo, documentos in por_tipo.items():
		documentos = list(documentos)
		marcadores = ", ".join(["%s"] * len(documentos))
		condiciones.append(f"(d.tipo_documento = %s and d.documento in ({marcadores}))")
		valores.append(tipo)
		valores.extend(documentos)

	sql = """
		select d.tipo_documento, d.documento, sum(d.abono) as abonado
		from `tabDetalle Documento Pago Proveedor` d
		inner join `tabPago Proveedor` p on p.name = d.parent
		where p.docstatus = 1 and ({condiciones})
	""".format(condiciones=" or ".join(condiciones))

	if excluir_pago:
		sql += " and p.name != %s"
		valores.append(excluir_pago)

	sql += " group by d.tipo_documento, d.documento"

	filas = frappe.db.sql(sql, tuple(valores), as_dict=True)
	return {(f.tipo_documento, f.documento): flt(f.abonado) for f in filas}


# ---------------------------------------------------------------------------
# Documentos del proveedor
# ---------------------------------------------------------------------------
def _documentos_cargo(proveedor, empresa=None, tipos=None, aplicar_permisos=True):
	"""Facturas de Compra del proveedor, incluidas las devoluciones."""
	resultado = []
	for tipo in tipos or [TIPO_ACTIVO]:
		cfg = get_config(tipo)
		filtros = dict(cfg["filtros"])
		filtros[cfg["proveedor"]] = proveedor
		if empresa:
			filtros[cfg["empresa"]] = empresa

		campos = [
			"name as documento",
			f"{cfg['fecha']} as fecha_documento",
			f"{cfg['moneda']} as moneda",
			f"{cfg['empresa']} as empresa",
			f"{cfg['valor']} as valor_original",
		]
		if cfg.get("referencia"):
			campos.append(f"{cfg['referencia']} as referencia")

		# get_list (NO get_all): aplica User Permissions y los permission_query_conditions
		# de todas las apps, igual que en cobranza.
		filas = frappe.get_list(tipo, filters=filtros, fields=campos, limit_page_length=0)

		if aplicar_permisos and filas:
			from pagos_chappsa.permisos_proveedor import filtrar_documentos_por_bodega

			visibles = set(filtrar_documentos_por_bodega(tipo, [f.documento for f in filas]))
			filas = [f for f in filas if f.documento in visibles]

		for fila in filas:
			fila["tipo_documento"] = tipo
			fila["etiqueta_tipo"] = cfg["etiqueta"]
			fila["referencia"] = fila.get("referencia") or ""
			resultado.append(fila)

	return resultado


def _documentos_anticipo(proveedor, empresa=None, excluir_pago=None):
	"""Excedentes dejados por pagos validados anteriores (anticipos entregados)."""
	filtros = {"proveedor": proveedor, "docstatus": 1, "excedente": (">", TOLERANCIA)}
	if empresa:
		filtros["empresa"] = empresa
	if excluir_pago:
		filtros["name"] = ("!=", excluir_pago)

	resultado = []
	for fila in frappe.get_all(
		TIPO_ANTICIPO,
		filters=filtros,
		fields=["name as documento", "fecha as fecha_documento", "moneda", "empresa", "excedente"],
		limit_page_length=0,
	):
		resultado.append(
			{
				"tipo_documento": TIPO_ANTICIPO,
				"etiqueta_tipo": "Anticipo",
				"documento": fila.documento,
				"fecha_documento": fila.fecha_documento,
				"moneda": fila.moneda,
				"empresa": fila.empresa,
				# Signo negativo: es dinero a favor de la empresa (ya entregado al proveedor).
				"valor_original": -flt(fila.excedente),
				"referencia": "",
			}
		)
	return resultado


def get_documentos_pendientes(
	proveedor, empresa=None, moneda=None, excluir_pago=None, incluir_saldados=False, aplicar_permisos=True
):
	"""Documentos del proveedor con saldo distinto de cero, del más antiguo al más reciente."""
	if not proveedor:
		return []

	documentos = _documentos_cargo(proveedor, empresa, aplicar_permisos=aplicar_permisos)
	documentos += _documentos_anticipo(proveedor, empresa, excluir_pago=excluir_pago)

	if aplicar_permisos:
		from pagos_chappsa.permisos_proveedor import get_empresas_permitidas

		permitidas = get_empresas_permitidas()
		if permitidas is not None:
			documentos = [d for d in documentos if d.get("empresa") in permitidas]

	if moneda:
		documentos = [d for d in documentos if d["moneda"] == moneda]

	abonos = get_abonos([(d["tipo_documento"], d["documento"]) for d in documentos], excluir_pago=excluir_pago)

	pendientes = []
	for d in documentos:
		abonado = abonos.get((d["tipo_documento"], d["documento"]), 0.0)
		saldo = flt(flt(d["valor_original"]) - abonado, 2)

		if not incluir_saldados and abs(saldo) <= TOLERANCIA:
			continue

		d["abonado_previo"] = flt(abonado, 2)
		d["saldo_anterior"] = saldo
		d["clase"] = "Cargo" if flt(d["valor_original"]) >= 0 else "Crédito"
		pendientes.append(d)

	pendientes.sort(key=lambda d: (str(d["fecha_documento"] or ""), d["documento"]))
	return pendientes


def get_saldo_documento(tipo_documento, documento, excluir_pago=None):
	"""Saldo actual de un solo documento (se usa al validar, contra la base de datos)."""
	if tipo_documento == TIPO_ANTICIPO:
		valor_original = -flt(frappe.db.get_value(TIPO_ANTICIPO, documento, "excedente"))
	else:
		cfg = get_config(tipo_documento)
		valor_original = flt(frappe.db.get_value(tipo_documento, documento, cfg["valor"]))

	abonado = get_abonos([(tipo_documento, documento)], excluir_pago=excluir_pago).get(
		(tipo_documento, documento), 0.0
	)
	return flt(valor_original - abonado, 2), flt(valor_original, 2), flt(abonado, 2)


# ---------------------------------------------------------------------------
# Distribución automática
# ---------------------------------------------------------------------------
def distribuir_monto(documentos, monto_disponible):
	"""Reparte `monto_disponible` entre los CARGOS, del más antiguo al más reciente.

	Idéntico en espíritu a `pagos_chappsa.saldos.distribuir_monto`: un crédito
	marcado con `aplicar` = 1 aporta fondos solo hasta lo necesario para cubrir
	los cargos.
	"""
	monto = flt(monto_disponible, 2)
	filas = [dict(d) for d in documentos]
	cargos = [f for f in filas if f.get("clase") == "Cargo"]
	creditos = [f for f in filas if f.get("clase") == "Crédito"]

	total_cargos = flt(sum(flt(f["saldo_anterior"]) for f in cargos), 2)
	faltante = flt(max(total_cargos - monto, 0.0), 2)

	fondos = monto
	for fila in creditos:
		if not fila.get("aplicar") or faltante <= TOLERANCIA:
			fila["abono"] = 0.0
			continue
		usar = min(abs(flt(fila["saldo_anterior"])), faltante)
		fila["abono"] = flt(-usar, 2)
		faltante = flt(faltante - usar, 2)
		fondos = flt(fondos + usar, 2)

	for fila in cargos:
		if fondos <= TOLERANCIA:
			fila["abono"] = 0.0
			continue
		abono = min(flt(fila["saldo_anterior"], 2), fondos)
		fila["abono"] = flt(abono, 2)
		fondos = flt(fondos - abono, 2)

	for fila in filas:
		fila["saldo"] = flt(flt(fila["saldo_anterior"]) - flt(fila["abono"]), 2)

	excedente = flt(fondos, 2) if fondos > TOLERANCIA else 0.0
	return filas, excedente
