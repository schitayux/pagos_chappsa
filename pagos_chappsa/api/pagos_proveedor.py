# Copyright (c) 2026, Josue Velasquez and contributors
# For license information, please see license.txt
"""Endpoints usados por la página "Pagos a Proveedores".

Ruta punteada: pagos_chappsa.api.pagos_proveedor.<funcion>
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

from pagos_chappsa.permisos_proveedor import exigir_accion, exigir_proveedor_visible, get_perfil
from pagos_chappsa.saldos_proveedor import (
	TIPO_ACTIVO,
	TOLERANCIA,
	distribuir_monto,
	get_config,
	get_documentos_pendientes,
)

DOCTYPE_PAGO = "Pago Proveedor"
FORMATO_RECIBO = "Recibo de Pago a Proveedor"


def _validar_lectura():
	if not frappe.has_permission(DOCTYPE_PAGO, "read"):
		frappe.throw(_("No tiene permiso para consultar pagos a proveedores."), frappe.PermissionError)


# ---------------------------------------------------------------------------
# Contexto inicial de la página
# ---------------------------------------------------------------------------
@frappe.whitelist()
def get_contexto():
	_validar_lectura()

	empresas = frappe.get_all(
		"Company", fields=["name", "default_currency", "abbr"], order_by="name", limit_page_length=0
	)
	formas_pago = frappe.get_all(
		"Mode of Payment", filters={"enabled": 1}, fields=["name", "type"], order_by="name", limit_page_length=0
	)

	perfil = get_perfil()

	from pagos_chappsa.integracion_nativa_proveedor import (
		TIPO_A_CAMPO_CUENTAS,
		get_cuenta_gastos_bancarios,
		get_cuentas_anticipo_configuradas,
		get_cuentas_configuradas,
	)

	cuentas_por_forma = {tipo: get_cuentas_configuradas(tipo) for tipo in TIPO_A_CAMPO_CUENTAS}

	return {
		"empresas": empresas,
		"formas_pago": formas_pago,
		"tipos_activos": [TIPO_ACTIVO],
		"cuentas_por_forma": cuentas_por_forma,
		"cuentas_anticipo": get_cuentas_anticipo_configuradas(),
		"permite_gasto_bancario": bool(get_cuenta_gastos_bancarios()),
		"hoy": nowdate(),
		"formato_recibo": FORMATO_RECIBO,
		"puede_crear": frappe.has_permission(DOCTYPE_PAGO, "create") and bool(perfil.permitir_guardar_borradores),
		"puede_validar": frappe.has_permission(DOCTYPE_PAGO, "submit") and bool(perfil.permitir_validar_pagos),
		"perfil": {
			"tiene_perfil": perfil.tiene_perfil,
			"habilitado": perfil.habilitado,
			"serie_recibo": perfil.serie_recibo,
			"permitir_pagos_parciales": perfil.permitir_pagos_parciales,
			"permitir_anticipos": perfil.permitir_anticipos,
			"permitir_reimpresion_recibo": perfil.permitir_reimpresion_recibo,
			"permitir_cancelar_borradores": perfil.permitir_cancelar_borradores,
			"permitir_cancelar_validados": perfil.permitir_cancelar_validados,
		},
	}


@frappe.whitelist()
def get_monedas_con_documentos():
	_validar_lectura()

	monedas = set()
	for tipo in [TIPO_ACTIVO]:
		cfg = get_config(tipo)
		filas = frappe.db.sql(
			"select distinct `{campo}` from `tab{tipo}` where docstatus = 1".format(
				campo=cfg["moneda"], tipo=tipo
			)
		)
		monedas.update(f[0] for f in filas if f[0])

	filas = frappe.db.sql("select distinct moneda from `tabPago Proveedor` where docstatus < 2")
	monedas.update(f[0] for f in filas if f[0])

	return sorted(monedas)


@frappe.whitelist()
def get_cuentas_banco(empresa, moneda=None):
	"""Cuentas de banco de la empresa. Si se indica `moneda`, solo las que están
	denominadas en esa moneda: un pago en USD no debe ofrecer una cuenta en GTQ."""
	_validar_lectura()
	filtros = {"company": empresa, "disabled": 0}
	if not moneda:
		return frappe.get_all(
			"Bank Account", filters=filtros, fields=["name", "bank", "account"], order_by="name", limit_page_length=0
		)

	return frappe.db.sql(
		"""
		select ba.name, ba.bank, ba.account
		from `tabBank Account` ba
		inner join `tabAccount` acc on acc.name = ba.account
		where ba.company = %(empresa)s and ba.disabled = 0 and acc.account_currency = %(moneda)s
		order by ba.name
		""",
		{"empresa": empresa, "moneda": moneda},
		as_dict=True,
	)


# ---------------------------------------------------------------------------
# Documentos pendientes del proveedor
# ---------------------------------------------------------------------------
@frappe.whitelist()
def get_documentos(proveedor, empresa=None, moneda=None, pago=None):
	"""Todo el saldo del proveedor, agrupado en "cuentas" (empresa + moneda)."""
	_validar_lectura()
	exigir_proveedor_visible(proveedor)

	todos = get_documentos_pendientes(proveedor, excluir_pago=pago)

	abreviaturas = dict(
		frappe.get_all("Company", fields=["name", "abbr"], limit_page_length=0, as_list=True)
	)

	cuentas = {}
	for d in todos:
		clave = (d["empresa"], d["moneda"])
		cuenta = cuentas.setdefault(
			clave,
			{
				"empresa": d["empresa"],
				"abbr": abreviaturas.get(d["empresa"]) or d["empresa"],
				"moneda": d["moneda"],
				"saldo": 0.0,
				"documentos": 0,
				"creditos": 0,
			},
		)
		cuenta["saldo"] = flt(cuenta["saldo"] + flt(d["saldo_anterior"]), 2)
		cuenta["documentos"] += 1
		if d["clase"] == "Crédito":
			cuenta["creditos"] += 1

	lista_cuentas = sorted(cuentas.values(), key=lambda c: -abs(c["saldo"]))

	seleccionada = None
	if empresa and moneda:
		seleccionada = next(
			(c for c in lista_cuentas if c["empresa"] == empresa and c["moneda"] == moneda), None
		)
	if not seleccionada:
		seleccionada = lista_cuentas[0] if lista_cuentas else None

	if seleccionada:
		empresa, moneda = seleccionada["empresa"], seleccionada["moneda"]
		documentos = [d for d in todos if d["empresa"] == empresa and d["moneda"] == moneda]
	else:
		documentos = []
		empresa = empresa or frappe.defaults.get_user_default("Company") or frappe.db.get_default("Company")
		moneda = (
			moneda
			or frappe.db.get_value("Supplier", proveedor, "default_currency")
			or frappe.db.get_value("Company", empresa, "default_currency")
		)

	return {
		"documentos": documentos,
		"cuentas": lista_cuentas,
		"empresa": empresa,
		"moneda": moneda,
		"nombre_proveedor": frappe.db.get_value("Supplier", proveedor, "supplier_name"),
	}


@frappe.whitelist()
def distribuir(documentos, monto):
	_validar_lectura()
	documentos = frappe.parse_json(documentos)
	filas, excedente = distribuir_monto(documentos, flt(monto))
	return {"documentos": filas, "excedente": excedente}


# ---------------------------------------------------------------------------
# Guardar / validar el pago
# ---------------------------------------------------------------------------
@frappe.whitelist()
def guardar_pago(payload, validar=0):
	"""Crea o actualiza un Pago Proveedor en borrador; con validar=1 además lo valida."""
	datos = frappe.parse_json(payload)
	validar = int(validar or 0)

	nombre = datos.get("name")

	if validar and not nombre:
		frappe.throw(
			_("Guarde primero el pago como borrador; luego podrá validarlo."),
			title=_("Falta guardar el borrador"),
		)

	if nombre:
		doc = frappe.get_doc(DOCTYPE_PAGO, nombre)
		if doc.docstatus != 0:
			frappe.throw(_("El pago {0} ya fue validado o cancelado y no se puede modificar.").format(nombre))
		doc.set("detalle_documentos", [])
		doc.set("detalle_formas_pago", [])
	else:
		doc = frappe.new_doc(DOCTYPE_PAGO)

	doc.empresa = datos.get("empresa")
	doc.proveedor = datos.get("proveedor")
	doc.fecha = getdate(datos.get("fecha") or nowdate())
	doc.moneda = datos.get("moneda")
	doc.tipo_cambio = flt(datos.get("tipo_cambio"))
	doc.no_recibo_manual = (datos.get("no_recibo_manual") or "").strip()
	doc.monto_recibido = flt(datos.get("monto_recibido"))
	doc.comentarios = datos.get("comentarios")
	doc.cuenta_anticipo = datos.get("cuenta_anticipo") or None

	for fila in datos.get("documentos") or []:
		abono = flt(fila.get("abono"))
		if abs(abono) <= TOLERANCIA:
			continue
		doc.append(
			"detalle_documentos",
			{
				"tipo_documento": fila.get("tipo_documento"),
				"documento": fila.get("documento"),
				"fecha_documento": fila.get("fecha_documento"),
				"referencia": fila.get("referencia"),
				"abono": abono,
				"comentario": fila.get("comentario"),
			},
		)

	for fila in datos.get("formas_pago") or []:
		if flt(fila.get("monto")) <= 0:
			continue
		doc.append(
			"detalle_formas_pago",
			{
				"tipo": fila.get("tipo"),
				"forma_pago": fila.get("forma_pago"),
				"fecha": fila.get("fecha") or doc.fecha,
				"no_documento": fila.get("no_documento"),
				"banco": fila.get("banco"),
				"cuenta_banco": fila.get("cuenta_banco"),
				"cuenta_contable": fila.get("cuenta_contable"),
				"gasto_bancario": flt(fila.get("gasto_bancario")),
				"monto": flt(fila.get("monto")),
				"comentario": fila.get("comentario"),
			},
		)

	doc.save()

	if validar:
		doc.submit()

	return {
		"name": doc.name,
		"docstatus": doc.docstatus,
		"estado": doc.estado,
		"no_recibo_manual": doc.no_recibo_manual,
	}


@frappe.whitelist()
def get_pago(name):
	"""Devuelve un pago listo para recargarlo en la página."""
	_validar_lectura()
	doc = frappe.get_doc(DOCTYPE_PAGO, name)
	doc.check_permission("read")
	exigir_proveedor_visible(doc.proveedor)

	perfil = get_perfil()
	solo_lectura = doc.docstatus != 0

	if solo_lectura:
		documentos = [
			{
				"tipo_documento": f.tipo_documento,
				"etiqueta_tipo": "Anticipo" if f.tipo_documento == DOCTYPE_PAGO else "Factura de Compra",
				"documento": f.documento,
				"clase": f.clase,
				"fecha_documento": f.fecha_documento,
				"empresa": doc.empresa,
				"moneda": doc.moneda,
				"referencia": f.referencia or "",
				"valor_original": flt(f.valor_original),
				"abonado_previo": flt(f.abonado_previo),
				"saldo_anterior": flt(f.saldo_anterior),
				"abono": flt(f.abono),
				"saldo": flt(f.saldo),
			}
			for f in doc.detalle_documentos
		]
		cuentas = []
	else:
		datos = get_documentos(doc.proveedor, doc.empresa, doc.moneda, pago=doc.name)
		documentos = datos["documentos"]
		cuentas = datos["cuentas"]

		abonos = {(f.tipo_documento, f.documento): flt(f.abono) for f in doc.detalle_documentos}
		vistos = set()
		for d in documentos:
			clave = (d["tipo_documento"], d["documento"])
			d["abono"] = abonos.get(clave, 0.0)
			if clave in abonos:
				vistos.add(clave)
		for f in doc.detalle_documentos:
			if (f.tipo_documento, f.documento) in vistos:
				continue
			documentos.append(
				{
					"tipo_documento": f.tipo_documento,
					"etiqueta_tipo": "Anticipo" if f.tipo_documento == DOCTYPE_PAGO else "Factura de Compra",
					"documento": f.documento,
					"clase": f.clase,
					"fecha_documento": f.fecha_documento,
					"empresa": doc.empresa,
					"moneda": doc.moneda,
					"referencia": f.referencia or "",
					"valor_original": flt(f.valor_original),
					"abonado_previo": flt(f.abonado_previo),
					"saldo_anterior": flt(f.saldo_anterior),
					"abono": flt(f.abono),
					"saldo": flt(f.saldo),
				}
			)

	formas = [
		{
			"tipo": f.tipo,
			"forma_pago": f.forma_pago,
			"fecha": f.fecha,
			"monto": flt(f.monto),
			"no_documento": f.no_documento or "",
			"banco": f.banco or "",
			"cuenta_banco": f.cuenta_banco or "",
			"cuenta_contable": f.cuenta_contable or "",
			"gasto_bancario": flt(f.gasto_bancario),
			"comentario": f.comentario or "",
		}
		for f in doc.detalle_formas_pago
	]

	return {
		"name": doc.name,
		"docstatus": doc.docstatus,
		"estado": doc.estado,
		"cuenta_anticipo": doc.cuenta_anticipo or "",
		"solo_lectura": solo_lectura,
		"proveedor": doc.proveedor,
		"nombre_proveedor": doc.nombre_proveedor,
		"empresa": doc.empresa,
		"moneda": doc.moneda,
		"fecha": doc.fecha,
		"no_recibo_manual": doc.no_recibo_manual or "",
		"tipo_cambio": flt(doc.tipo_cambio),
		"monto_recibido": flt(doc.monto_recibido),
		"comentarios": doc.comentarios or "",
		"cobrador": doc.cobrador,
		"nombre_cobrador": doc.nombre_cobrador,
		"documentos": documentos,
		"cuentas": cuentas,
		"formas_pago": formas,
		"puede_imprimir": bool(doc.docstatus == 1 and perfil.permitir_reimpresion_recibo),
	}


@frappe.whitelist()
def registrar_reimpresion(name):
	exigir_accion("reimprimir")
	docstatus = frappe.db.get_value(DOCTYPE_PAGO, name, "docstatus")
	if docstatus != 1:
		frappe.throw(_("Solo se puede imprimir el comprobante de un pago validado."))
	return {"formato": FORMATO_RECIBO, "name": name}


@frappe.whitelist()
def get_payment_entries(name):
	"""Payment Entry(s) nativos de ERPNext generados por este Pago Proveedor
	(directos o de un anticipo suyo), para el botón "Ver entrada de pago"."""
	doc = frappe.get_doc(DOCTYPE_PAGO, name)
	doc.check_permission("read")
	exigir_proveedor_visible(doc.proveedor)

	return frappe.get_all(
		"Payment Entry",
		filters={"custom_pagos_chappsa_pago_proveedor": name},
		fields=["name", "docstatus"],
		order_by="creation",
	)


@frappe.whitelist()
def actualizar_no_recibo(name, no_recibo):
	doc = frappe.get_doc(DOCTYPE_PAGO, name)
	doc.check_permission("write")
	exigir_proveedor_visible(doc.proveedor)

	if doc.docstatus == 2:
		frappe.throw(_("El pago {0} está cancelado y ya no se puede modificar.").format(name))

	doc.no_recibo_manual = (no_recibo or "").strip()
	doc.save()

	return {"name": doc.name, "no_recibo_manual": doc.no_recibo_manual}


@frappe.whitelist()
def get_pagos_recientes(proveedor=None, empresa=None, limite=20):
	"""Últimos pagos, sin importar el estado: un pago cancelado desde el page o
	desde su Payment Entry en ERPNext debe seguir viéndose aquí (con estado
	Cancelado), no desaparecer de la lista."""
	_validar_lectura()
	filtros = {}
	if proveedor:
		filtros["proveedor"] = proveedor
	if empresa:
		filtros["empresa"] = empresa

	return frappe.get_list(
		DOCTYPE_PAGO,
		filters=filtros,
		fields=[
			"name",
			"no_recibo_manual",
			"fecha",
			"proveedor",
			"nombre_proveedor",
			"moneda",
			"monto_recibido",
			"total_aplicado",
			"excedente",
			"anticipo_disponible",
			"estado",
			"docstatus",
			"cobrador",
			"nombre_cobrador",
		],
		order_by="fecha desc, creation desc",
		limit_page_length=int(limite or 20),
	)
