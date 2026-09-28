# Copyright (c) 2026, Josue Velasquez and contributors
# For license information, please see license.txt
"""Estado de cuenta de proveedores — resumen: una línea por proveedor."""

import frappe
from frappe import _

from pagos_chappsa.estado_cuenta_proveedor import get_resumen
from pagos_chappsa.permisos_proveedor import exigir_accion


def execute(filters=None):
	exigir_accion("estado_cuenta_resumen")
	filters = frappe._dict(filters or {})

	filas = get_resumen(
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
		{"label": _("Proveedor"), "fieldname": "proveedor", "fieldtype": "Link", "options": "Supplier", "width": 165},
		{"label": _("Nombre"), "fieldname": "nombre_proveedor", "fieldtype": "Data", "width": 260},
		{"label": _("Moneda"), "fieldname": "moneda", "fieldtype": "Link", "options": "Currency", "width": 75},
		{"label": _("Documentos"), "fieldname": "documentos", "fieldtype": "Int", "width": 95},
		{"label": _("Total"), "fieldname": "total", "fieldtype": "Currency", "options": "moneda", "width": 130},
		{"label": _("Abonado"), "fieldname": "abonado", "fieldtype": "Currency", "options": "moneda", "width": 130},
		{"label": _("Diferencia"), "fieldname": "diferencia", "fieldtype": "Currency", "options": "moneda", "width": 130},
	]
