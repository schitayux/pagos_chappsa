# Copyright (c) 2026, Josue Velasquez and contributors
# For license information, please see license.txt
"""Estado de cuenta de proveedores — detallado: una línea por documento."""

import frappe
from frappe import _

from pagos_chappsa.estado_cuenta_proveedor import get_detalle
from pagos_chappsa.permisos_proveedor import exigir_accion


def execute(filters=None):
	exigir_accion("estado_cuenta_detallado")
	filters = frappe._dict(filters or {})

	filas = get_detalle(
		empresa=filters.get("empresa"),
		moneda=filters.get("moneda"),
		proveedor=filters.get("proveedor"),
		hasta=filters.get("hasta"),
		incluir_saldados=bool(filters.get("incluir_saldados")),
	)
	for i, f in enumerate(filas, start=1):
		f["linea"] = i

	return get_columns(), filas


def get_columns():
	return [
		{"label": _("#"), "fieldname": "linea", "fieldtype": "Int", "width": 55},
		{"label": _("Proveedor"), "fieldname": "nombre_proveedor", "fieldtype": "Data", "width": 210},
		{"label": _("Factura de compra"), "fieldname": "documento", "fieldtype": "Dynamic Link",
		 "options": "tipo_documento", "width": 150},
		{"label": _("No. interno"), "fieldname": "numero_interno", "fieldtype": "Data", "width": 105},
		{"label": _("Fecha"), "fieldname": "fecha", "fieldtype": "Date", "width": 95},
		{"label": _("Total"), "fieldname": "total", "fieldtype": "Currency", "options": "moneda", "width": 120},
		{"label": _("Abono"), "fieldname": "abono", "fieldtype": "Currency", "options": "moneda", "width": 120},
		{"label": _("Saldo"), "fieldname": "saldo", "fieldtype": "Currency", "options": "moneda", "width": 120},
	]
