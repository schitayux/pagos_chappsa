# Copyright (c) 2026, Josue Velasquez and contributors
# For license information, please see license.txt
"""Versión imprimible del estado de cuenta, con logo por compañía.

Se abre desde el botón "Imprimir estado de cuenta" de los reportes:
/estado_cuenta?tipo=resumen|detallado&cliente=&empresa=&moneda=&hasta=
"""

import frappe
from frappe import _
from frappe.utils import flt

from pagos_chappsa.estado_cuenta import get_detalle, get_resumen
from pagos_chappsa.permisos import exigir_accion, exigir_cliente_visible

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.throw(_("Inicie sesión para ver el estado de cuenta."), frappe.PermissionError)

	args = frappe.form_dict
	tipo = (args.get("tipo") or "resumen").lower()
	if tipo not in ("resumen", "detallado"):
		tipo = "resumen"

	exigir_accion("estado_cuenta_resumen" if tipo == "resumen" else "estado_cuenta_detallado")

	cliente = args.get("cliente") or None
	empresa = args.get("empresa") or None
	moneda = args.get("moneda") or None
	hasta = args.get("hasta") or None
	incluir = args.get("incluir_saldados") in ("1", "true", "True")

	if cliente:
		exigir_cliente_visible(cliente)

	context.tipo = tipo
	context.es_resumen = tipo == "resumen"
	context.filtro_cliente = cliente
	context.filtro_empresa = empresa
	context.filtro_moneda = moneda
	context.hasta = hasta
	context.no_cache = 1

	if context.es_resumen:
		filas = get_resumen(empresa, moneda, cliente, hasta, incluir_saldados=incluir)
		context.total = flt(sum(f["total"] for f in filas), 2)
		context.abonado = flt(sum(f["abonado"] for f in filas), 2)
		context.diferencia = flt(sum(f["diferencia"] for f in filas), 2)
	else:
		filas = get_detalle(empresa, moneda, cliente, hasta, incluir_saldados=incluir)
		context.total = flt(sum(f["total"] for f in filas), 2)
		context.abonado = flt(sum(f["abono"] for f in filas), 2)
		context.diferencia = flt(sum(f["saldo"] for f in filas), 2)

	for i, f in enumerate(filas, start=1):
		f["linea"] = i
	context.filas = filas

	# Logo dinámico: la empresa filtrada, o la única que aparezca en los datos.
	empresas = {f.get("empresa") for f in filas if f.get("empresa")} if not context.es_resumen else set()
	nombre_empresa = empresa or (empresas.pop() if len(empresas) == 1 else None)
	context.empresa_doc = frappe.get_cached_doc("Company", nombre_empresa) if nombre_empresa else None

	monedas = {f.get("moneda") for f in filas if f.get("moneda")}
	context.simbolo = ""
	if len(monedas) == 1:
		unica = monedas.pop()
		context.simbolo = frappe.db.get_value("Currency", unica, "symbol") or unica
	context.monedas_mixtas = len(monedas) > 1

	context.impreso_por = frappe.db.get_value("User", frappe.session.user, "full_name") or frappe.session.user
	return context
