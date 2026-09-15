# Copyright (c) 2026, Josue Velasquez and contributors
# For license information, please see license.txt
"""Endpoints usados por la página "Pagos".

Ruta punteada: pagos_chappsa.api.pagos.<funcion>
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

from pagos_chappsa.permisos import exigir_accion, exigir_cliente_visible, get_perfil
from pagos_chappsa.saldos import (
	TIPOS_ACTIVOS,
	get_config,
	TOLERANCIA,
	distribuir_monto,
	get_documentos_pendientes,
)

DOCTYPE_PAGO = "Pago Cliente"
FORMATO_RECIBO = "Recibo de Pago"


def _validar_lectura():
	if not frappe.has_permission(DOCTYPE_PAGO, "read"):
		frappe.throw(_("No tiene permiso para consultar pagos."), frappe.PermissionError)


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

	return {
		"empresas": empresas,
		"formas_pago": formas_pago,
		"tipos_activos": TIPOS_ACTIVOS,
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
	"""Monedas que realmente aparecen en documentos, para no ofrecer las 148 del catálogo.

	Se consultan los tipos activos y los pagos ya registrados. Son códigos de moneda,
	no datos del negocio, así que basta con una consulta directa.
	"""
	_validar_lectura()

	monedas = set()
	for tipo in TIPOS_ACTIVOS:
		cfg = get_config(tipo)
		filas = frappe.db.sql(
			"select distinct `{campo}` from `tab{tipo}` where docstatus = 1".format(
				campo=cfg["moneda"], tipo=tipo
			)
		)
		monedas.update(f[0] for f in filas if f[0])

	filas = frappe.db.sql("select distinct moneda from `tabPago Cliente` where docstatus < 2")
	monedas.update(f[0] for f in filas if f[0])

	return sorted(monedas)


@frappe.whitelist()
def get_cuentas_banco(empresa):
	_validar_lectura()
	return frappe.get_all(
		"Bank Account",
		filters={"company": empresa, "disabled": 0},
		fields=["name", "bank", "account"],
		order_by="name",
		limit_page_length=0,
	)


# ---------------------------------------------------------------------------
# Documentos pendientes del cliente
# ---------------------------------------------------------------------------
@frappe.whitelist()
def get_documentos(cliente, empresa=None, moneda=None, pago=None):
	"""Todo el saldo del cliente, agrupado en "cuentas" (empresa + moneda).

	La página solo exige el cliente: aquí se calcula en qué empresas y monedas tiene
	saldo y se elige sola la cuenta con mayor saldo pendiente. Si el cliente solo
	tiene saldo en un lado —el caso normal— el usuario no elige nada.
	"""
	_validar_lectura()
	# El desplegable de cliente ya filtra por permisos, pero la API es alcanzable
	# directamente: hay que revalidar aquí o la restricción sería solo cosmética.
	exigir_cliente_visible(cliente)

	# Una sola consulta trae todo; el agrupado y el filtrado se hacen en memoria.
	todos = get_documentos_pendientes(cliente, excluir_pago=pago)

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

	# Mayor saldo primero: es la cuenta que el usuario casi siempre quiere cobrar.
	lista_cuentas = sorted(cuentas.values(), key=lambda c: -abs(c["saldo"]))

	# Si no se pidió una cuenta concreta (o la pedida ya no tiene saldo), se elige sola.
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
		# Cliente sin saldo: aun así hay que poder registrar un anticipo puro.
		documentos = []
		empresa = empresa or frappe.defaults.get_user_default("Company") or frappe.db.get_default("Company")
		moneda = (
			moneda
			or frappe.db.get_value("Customer", cliente, "default_currency")
			or frappe.db.get_value("Company", empresa, "default_currency")
		)

	return {
		"documentos": documentos,
		"cuentas": lista_cuentas,
		"empresa": empresa,
		"moneda": moneda,
		"nombre_cliente": frappe.db.get_value("Customer", cliente, "customer_name"),
	}


@frappe.whitelist()
def distribuir(documentos, monto):
	"""Distribución automática del más antiguo al más reciente (referencia del servidor)."""
	_validar_lectura()
	documentos = frappe.parse_json(documentos)
	filas, excedente = distribuir_monto(documentos, flt(monto))
	return {"documentos": filas, "excedente": excedente}


# ---------------------------------------------------------------------------
# Guardar / validar el pago
# ---------------------------------------------------------------------------
@frappe.whitelist()
def guardar_pago(payload, validar=0):
	"""Crea o actualiza un Pago Cliente en borrador; con validar=1 además lo valida."""
	datos = frappe.parse_json(payload)
	validar = int(validar or 0)

	nombre = datos.get("name")

	# Regla de flujo: no se valida "de una". Primero queda el borrador guardado y
	# revisable, y la validación es una segunda acción explícita sobre ese borrador.
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
	doc.cliente = datos.get("cliente")
	doc.fecha = getdate(datos.get("fecha") or nowdate())
	doc.moneda = datos.get("moneda")
	doc.no_recibo_manual = (datos.get("no_recibo_manual") or "").strip()
	doc.monto_recibido = flt(datos.get("monto_recibido"))
	doc.comentarios = datos.get("comentarios")

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
	"""Devuelve un pago listo para recargarlo en la página.

	* Borrador  -> se recalculan los saldos pendientes EXCLUYENDO este pago y se
	               superponen sus abonos, de modo que se pueda seguir editando con
	               saldos frescos.
	* Validado  -> se devuelve el detalle guardado tal cual, sin recalcular: es el
	               registro histórico y la pantalla queda en solo lectura.
	"""
	_validar_lectura()
	doc = frappe.get_doc(DOCTYPE_PAGO, name)
	doc.check_permission("read")
	exigir_cliente_visible(doc.cliente)

	perfil = get_perfil()
	solo_lectura = doc.docstatus != 0

	if solo_lectura:
		documentos = [
			{
				"tipo_documento": f.tipo_documento,
				"etiqueta_tipo": "Anticipo" if f.tipo_documento == DOCTYPE_PAGO else "Nota de Entrega",
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
		datos = get_documentos(doc.cliente, doc.empresa, doc.moneda, pago=doc.name)
		documentos = datos["documentos"]
		cuentas = datos["cuentas"]

		abonos = {(f.tipo_documento, f.documento): flt(f.abono) for f in doc.detalle_documentos}
		vistos = set()
		for d in documentos:
			clave = (d["tipo_documento"], d["documento"])
			d["abono"] = abonos.get(clave, 0.0)
			if clave in abonos:
				vistos.add(clave)
		# Un documento abonado que ya no figura como pendiente (lo saldó otro pago
		# mientras tanto) igual debe verse, o el borrador perdería esa línea en silencio.
		for f in doc.detalle_documentos:
			if (f.tipo_documento, f.documento) in vistos:
				continue
			documentos.append(
				{
					"tipo_documento": f.tipo_documento,
					"etiqueta_tipo": "Anticipo" if f.tipo_documento == DOCTYPE_PAGO else "Nota de Entrega",
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
			"comentario": f.comentario or "",
		}
		for f in doc.detalle_formas_pago
	]

	return {
		"name": doc.name,
		"docstatus": doc.docstatus,
		"estado": doc.estado,
		"solo_lectura": solo_lectura,
		"cliente": doc.cliente,
		"nombre_cliente": doc.nombre_cliente,
		"empresa": doc.empresa,
		"moneda": doc.moneda,
		"fecha": doc.fecha,
		"no_recibo_manual": doc.no_recibo_manual or "",
		"monto_recibido": flt(doc.monto_recibido),
		"comentarios": doc.comentarios or "",
		"cobrador": doc.cobrador,
		"nombre_cobrador": doc.nombre_cobrador,
		"documentos": documentos,
		"cuentas": cuentas,
		"formas_pago": formas,
		# El recibo solo existe para pagos validados: un borrador no es comprobante de nada.
		"puede_imprimir": bool(doc.docstatus == 1 and perfil.permitir_reimpresion_recibo),
	}


@frappe.whitelist()
def registrar_reimpresion(name):
	"""Valida el permiso de reimpresión antes de abrir el recibo."""
	exigir_accion("reimprimir")
	docstatus = frappe.db.get_value(DOCTYPE_PAGO, name, "docstatus")
	if docstatus != 1:
		frappe.throw(_("Solo se puede imprimir el recibo de un pago validado."))
	return {"formato": FORMATO_RECIBO, "name": name}


@frappe.whitelist()
def actualizar_no_recibo(name, no_recibo):
	"""Corrige el No. de recibo manual, incluso con el pago ya validado.

	Es un dato que se transcribe a mano de un talonario físico, así que tiene que
	poder corregirse sin cancelar el pago. El campo es allow_on_submit y el
	documento revalida obligatoriedad y unicidad en before_update_after_submit.
	"""
	doc = frappe.get_doc(DOCTYPE_PAGO, name)
	doc.check_permission("write")
	exigir_cliente_visible(doc.cliente)

	if doc.docstatus == 2:
		frappe.throw(_("El pago {0} está cancelado y ya no se puede modificar.").format(name))

	doc.no_recibo_manual = (no_recibo or "").strip()
	doc.save()

	return {"name": doc.name, "no_recibo_manual": doc.no_recibo_manual}


@frappe.whitelist()
def get_pagos_recientes(cliente=None, empresa=None, limite=20):
	_validar_lectura()
	filtros = {"docstatus": ("<", 2)}
	if cliente:
		filtros["cliente"] = cliente
	if empresa:
		filtros["empresa"] = empresa

	# get_list para que aplique pago_cliente_query (ver hooks.py).
	return frappe.get_list(
		DOCTYPE_PAGO,
		filters=filtros,
		fields=[
			"name",
			"no_recibo_manual",
			"fecha",
			"cliente",
			"nombre_cliente",
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
