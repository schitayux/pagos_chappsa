# Copyright (c) 2026, Josue Velasquez and contributors
# For license information, please see license.txt
"""Existencias por código y almacén.

Las bodegas que se pueden consultar son las del "Perfil de Cobranza" del usuario
(tabla Bodegas, expandiendo sub-bodegas cuando así se marcó). Misma regla que el
resto del módulo: sin perfil, o con "todas las bodegas", no se acota.
"""

import frappe
from frappe import _

from pagos_chappsa.permisos import get_bodegas_permitidas, get_perfil


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"label": _("Código"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 200},
		{"label": _("Descripción"), "fieldname": "descripcion", "fieldtype": "Data", "width": 320},
		{"label": _("Cantidad"), "fieldname": "cantidad", "fieldtype": "Float", "width": 100},
		{"label": _("UdM"), "fieldname": "uom", "fieldtype": "Link", "options": "UOM", "width": 70},
		{"label": _("Almacén"), "fieldname": "almacen", "fieldtype": "Link", "options": "Warehouse", "width": 220},
	]


def _bodegas_del_usuario():
	"""Conjunto de bodegas permitidas, o None si no hay restricción."""
	perfil = get_perfil()
	if perfil.tiene_perfil and not perfil.habilitado:
		return set()
	return get_bodegas_permitidas(perfil)


def get_data(filters):
	permitidas = _bodegas_del_usuario()
	if permitidas is not None and not permitidas:
		frappe.msgprint(_("Su Perfil de Cobranza no tiene bodegas asignadas."))
		return []

	condiciones = ["1 = 1"]
	valores = {}
	if permitidas is not None:
		condiciones.append("b.warehouse IN %(permitidas)s")
		valores["permitidas"] = tuple(permitidas)
	if filters.almacen:
		if permitidas is not None and filters.almacen not in permitidas:
			frappe.throw(_("No tiene acceso a la bodega {0}.").format(frappe.bold(filters.almacen)))
		condiciones.append("b.warehouse = %(almacen)s")
		valores["almacen"] = filters.almacen
	if filters.item_code:
		condiciones.append("b.item_code = %(item_code)s")
		valores["item_code"] = filters.item_code
	if filters.buscar:
		condiciones.append("(b.item_code LIKE %(buscar)s OR i.item_name LIKE %(buscar)s OR i.description LIKE %(buscar)s)")
		valores["buscar"] = f"%{filters.buscar}%"
	if frappe.utils.cint(filters.get("solo_con_existencia", 1)):
		condiciones.append("b.actual_qty != 0")

	return frappe.db.sql(
		f"""
		SELECT b.item_code, i.item_name AS descripcion, b.actual_qty AS cantidad,
			b.stock_uom AS uom, b.warehouse AS almacen
		FROM `tabBin` b
		INNER JOIN `tabItem` i ON i.name = b.item_code
		WHERE {" AND ".join(condiciones)}
		ORDER BY b.warehouse, b.item_code
		""",
		valores,
		as_dict=True,
	)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def bodegas_query(doctype, txt, searchfield, start, page_len, filters):
	"""Desplegable del filtro Almacén: solo las bodegas del perfil."""
	permitidas = _bodegas_del_usuario()
	condiciones, valores = ["w.is_group = 0", "w.name LIKE %(txt)s"], {"txt": f"%{txt}%", "start": start, "page_len": page_len}
	if permitidas is not None:
		if not permitidas:
			return []
		condiciones.append("w.name IN %(permitidas)s")
		valores["permitidas"] = tuple(permitidas)
	return frappe.db.sql(
		f"""SELECT w.name FROM `tabWarehouse` w WHERE {" AND ".join(condiciones)}
		ORDER BY w.name LIMIT %(start)s, %(page_len)s""",
		valores,
	)
