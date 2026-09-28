# Copyright (c) 2026, Josue Velasquez and contributors
# For license information, please see license.txt
"""Consulta de pagos a proveedores emitidos por estado.

Filtros: proveedor, rango de fechas y estado (Borrador / Validado / Cancelado).
Respeta la visibilidad de proveedores del usuario (reglas nativas del sitio).
"""

import frappe
from frappe import _

from pagos_chappsa.permisos_proveedor import exigir_accion, pago_proveedor_query

ESTADOS = {"Borrador": 0, "Validado": 1, "Cancelado": 2}


def execute(filters=None):
	exigir_accion("reporte_recibos")
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"label": _("Pago"), "fieldname": "name", "fieldtype": "Link", "options": "Pago Proveedor", "width": 160},
		{"label": _("No. comprobante"), "fieldname": "no_recibo_manual", "fieldtype": "Data", "width": 140},
		{"label": _("Estado"), "fieldname": "estado", "fieldtype": "Data", "width": 95},
		{"label": _("Fecha"), "fieldname": "fecha", "fieldtype": "Date", "width": 95},
		{"label": _("Proveedor"), "fieldname": "proveedor", "fieldtype": "Link", "options": "Supplier", "width": 165},
		{"label": _("Nombre del proveedor"), "fieldname": "nombre_proveedor", "fieldtype": "Data", "width": 200},
		{"label": _("Registrado por"), "fieldname": "nombre_cobrador", "fieldtype": "Data", "width": 150},
		{"label": _("Empresa"), "fieldname": "empresa", "fieldtype": "Link", "options": "Company", "width": 190},
		{"label": _("Moneda"), "fieldname": "moneda", "fieldtype": "Link", "options": "Currency", "width": 70},
		{"label": _("Pagado"), "fieldname": "monto_recibido", "fieldtype": "Currency", "options": "moneda", "width": 115},
		{"label": _("Aplicado"), "fieldname": "total_aplicado", "fieldtype": "Currency", "options": "moneda", "width": 115},
		{"label": _("Excedente"), "fieldname": "excedente", "fieldtype": "Currency", "options": "moneda", "width": 110},
		{"label": _("Anticipo disp."), "fieldname": "anticipo_disponible", "fieldtype": "Currency", "options": "moneda", "width": 120},
		{"label": _("Comentarios"), "fieldname": "comentarios", "fieldtype": "Data", "width": 220},
	]


def get_data(filters):
	condiciones = ["1 = 1"]
	valores = {}

	estado = filters.get("estado")
	if estado and estado in ESTADOS:
		condiciones.append("p.docstatus = %(docstatus)s")
		valores["docstatus"] = ESTADOS[estado]
	else:
		condiciones.append("p.docstatus < 2")

	if filters.get("proveedor"):
		condiciones.append("p.proveedor = %(proveedor)s")
		valores["proveedor"] = filters.proveedor
	if filters.get("desde"):
		condiciones.append("p.fecha >= %(desde)s")
		valores["desde"] = filters.desde
	if filters.get("hasta"):
		condiciones.append("p.fecha <= %(hasta)s")
		valores["hasta"] = filters.hasta
	if filters.get("empresa"):
		condiciones.append("p.empresa = %(empresa)s")
		valores["empresa"] = filters.empresa
	if filters.get("cobrador"):
		condiciones.append("p.cobrador = %(cobrador)s")
		valores["cobrador"] = filters.cobrador

	restriccion = pago_proveedor_query()
	if restriccion:
		condiciones.append(restriccion.replace("`tabPago Proveedor`", "p"))

	return frappe.db.sql(
		"""
		select p.name, p.no_recibo_manual, p.estado, p.fecha, p.proveedor, p.nombre_proveedor, p.nombre_cobrador,
		       p.empresa, p.moneda, p.monto_recibido, p.total_aplicado, p.excedente,
		       p.anticipo_disponible, p.comentarios
		from `tabPago Proveedor` p
		where {condiciones}
		order by p.fecha desc, p.name desc
		""".format(condiciones=" and ".join(condiciones)),
		valores,
		as_dict=True,
	)
