# Copyright (c) 2026, Josue Velasquez and contributors
# For license information, please see license.txt
"""Consulta de recibos emitidos por estado.

Filtros: cliente, rango de fechas y estado (Borrador / Validado / Cancelado).
Respeta la visibilidad de clientes del usuario (reglas nativas del sitio).
"""

import frappe
from frappe import _

from pagos_chappsa.permisos import exigir_accion, pago_cliente_query

ESTADOS = {"Borrador": 0, "Validado": 1, "Cancelado": 2}


def execute(filters=None):
	exigir_accion("reporte_recibos")
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"label": _("Recibo"), "fieldname": "name", "fieldtype": "Link", "options": "Pago Cliente", "width": 160},
		{"label": _("No. recibo manual"), "fieldname": "no_recibo_manual", "fieldtype": "Data", "width": 140},
		{"label": _("Estado"), "fieldname": "estado", "fieldtype": "Data", "width": 95},
		{"label": _("Fecha"), "fieldname": "fecha", "fieldtype": "Date", "width": 95},
		{"label": _("Cliente"), "fieldname": "cliente", "fieldtype": "Link", "options": "Customer", "width": 165},
		{"label": _("Nombre del cliente"), "fieldname": "nombre_cliente", "fieldtype": "Data", "width": 200},
		{"label": _("Cobrador"), "fieldname": "nombre_cobrador", "fieldtype": "Data", "width": 150},
		{"label": _("Empresa"), "fieldname": "empresa", "fieldtype": "Link", "options": "Company", "width": 190},
		{"label": _("Moneda"), "fieldname": "moneda", "fieldtype": "Link", "options": "Currency", "width": 70},
		{"label": _("Recibido"), "fieldname": "monto_recibido", "fieldtype": "Currency", "options": "moneda", "width": 115},
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
		# Sin filtro explícito se muestran borradores y validados, no los cancelados.
		condiciones.append("p.docstatus < 2")

	if filters.get("cliente"):
		condiciones.append("p.cliente = %(cliente)s")
		valores["cliente"] = filters.cliente
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

	# Misma visibilidad que la list view: se reutiliza el hook, no se reimplementa.
	restriccion = pago_cliente_query()
	if restriccion:
		condiciones.append(restriccion.replace("`tabPago Cliente`", "p"))

	return frappe.db.sql(
		"""
		select p.name, p.no_recibo_manual, p.estado, p.fecha, p.cliente, p.nombre_cliente, p.nombre_cobrador,
		       p.empresa, p.moneda, p.monto_recibido, p.total_aplicado, p.excedente,
		       p.anticipo_disponible, p.comentarios
		from `tabPago Cliente` p
		where {condiciones}
		order by p.fecha desc, p.name desc
		""".format(condiciones=" and ".join(condiciones)),
		valores,
		as_dict=True,
	)
