# Copyright (c) 2026, Josue Velasquez and contributors
# For license information, please see license.txt
"""Cobros por empleado y día.

Una fila por (día, cobrador, empresa, moneda), con el desglose por forma de pago.
Se apoya en el campo `cobrador` del Pago Cliente, no en `owner`: quien digita puede
no ser quien cobró.
"""

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"label": _("Fecha"), "fieldname": "fecha", "fieldtype": "Date", "width": 95},
		{"label": _("Cobrador"), "fieldname": "cobrador", "fieldtype": "Link", "options": "User", "width": 170},
		{"label": _("Nombre"), "fieldname": "nombre_cobrador", "fieldtype": "Data", "width": 165},
		{"label": _("Empresa"), "fieldname": "empresa", "fieldtype": "Link", "options": "Company", "width": 200},
		{"label": _("Moneda"), "fieldname": "moneda", "fieldtype": "Link", "options": "Currency", "width": 70},
		{"label": _("Recibos"), "fieldname": "recibos", "fieldtype": "Int", "width": 75},
		{"label": _("Efectivo"), "fieldname": "efectivo", "fieldtype": "Currency", "options": "moneda", "width": 110},
		{"label": _("Transferencia"), "fieldname": "transferencia", "fieldtype": "Currency", "options": "moneda", "width": 120},
		{"label": _("Cheque"), "fieldname": "cheque", "fieldtype": "Currency", "options": "moneda", "width": 110},
		{"label": _("Retención"), "fieldname": "retencion", "fieldtype": "Currency", "options": "moneda", "width": 110},
		{"label": _("Total recibido"), "fieldname": "total_recibido", "fieldtype": "Currency", "options": "moneda", "width": 125},
		{"label": _("Aplicado a docs."), "fieldname": "aplicado", "fieldtype": "Currency", "options": "moneda", "width": 130},
		{"label": _("Créditos usados"), "fieldname": "creditos", "fieldtype": "Currency", "options": "moneda", "width": 125},
		{"label": _("Excedente"), "fieldname": "excedente", "fieldtype": "Currency", "options": "moneda", "width": 110},
	]


def get_condiciones(filters):
	condiciones = ["p.docstatus = %(docstatus)s"]
	valores = {"docstatus": 0 if filters.get("solo_borradores") else 1}

	if filters.get("desde"):
		condiciones.append("p.fecha >= %(desde)s")
		valores["desde"] = filters.desde
	if filters.get("hasta"):
		condiciones.append("p.fecha <= %(hasta)s")
		valores["hasta"] = filters.hasta
	if filters.get("cobrador"):
		condiciones.append("p.cobrador = %(cobrador)s")
		valores["cobrador"] = filters.cobrador
	if filters.get("empresa"):
		condiciones.append("p.empresa = %(empresa)s")
		valores["empresa"] = filters.empresa
	if filters.get("moneda"):
		condiciones.append("p.moneda = %(moneda)s")
		valores["moneda"] = filters.moneda

	return " and ".join(condiciones), valores


def get_data(filters):
	where, valores = get_condiciones(filters)

	# Totales del encabezado por día/cobrador/empresa/moneda
	encabezados = frappe.db.sql(
		f"""
		select p.fecha, p.cobrador, p.nombre_cobrador, p.empresa, p.moneda,
		       count(*) as recibos,
		       sum(p.monto_recibido) as total_recibido,
		       sum(p.total_aplicado) as aplicado,
		       sum(p.total_creditos_aplicados) as creditos,
		       sum(p.excedente) as excedente
		from `tabPago Cliente` p
		where {where}
		group by p.fecha, p.cobrador, p.empresa, p.moneda
		""",
		valores,
		as_dict=True,
	)
	if not encabezados:
		return []

	# Desglose por forma de pago, agrupado igual
	formas = frappe.db.sql(
		f"""
		select p.fecha, p.cobrador, p.empresa, p.moneda, f.tipo, sum(f.monto) as monto
		from `tabPago Cliente` p
		inner join `tabDetalle Forma Pago` f on f.parent = p.name
		where {where}
		group by p.fecha, p.cobrador, p.empresa, p.moneda, f.tipo
		""",
		valores,
		as_dict=True,
	)

	por_tipo = {}
	for f in formas:
		clave = (f.fecha, f.cobrador, f.empresa, f.moneda)
		por_tipo.setdefault(clave, {})[f.tipo] = flt(f.monto)

	datos = []
	for e in encabezados:
		clave = (e.fecha, e.cobrador, e.empresa, e.moneda)
		tipos = por_tipo.get(clave, {})
		datos.append(
			{
				"fecha": e.fecha,
				"cobrador": e.cobrador,
				"nombre_cobrador": e.nombre_cobrador,
				"empresa": e.empresa,
				"moneda": e.moneda,
				"recibos": e.recibos,
				"efectivo": tipos.get("Efectivo", 0.0),
				"transferencia": tipos.get("Transferencia", 0.0),
				"cheque": tipos.get("Cheque", 0.0),
				"retencion": tipos.get("Retención", 0.0),
				"total_recibido": flt(e.total_recibido),
				"aplicado": flt(e.aplicado),
				"creditos": flt(e.creditos),
				"excedente": flt(e.excedente),
			}
		)

	datos.sort(key=lambda d: (str(d["fecha"]), d["nombre_cobrador"] or "", d["empresa"] or ""))
	return datos
