# Copyright (c) 2026, Josue Velasquez and contributors
# For license information, please see license.txt
"""Préstamos vigentes del cliente, para el KPI de la página "Pagos".

Ruta punteada: pagos_chappsa.api.prestamos.get_prestamos
"""

import frappe
from frappe.utils import flt, getdate, nowdate

from pagos_chappsa.api.pagos import _validar_lectura
from pagos_chappsa.permisos import exigir_cliente_visible

TIPO_TRANSFERENCIA_PRESTAMO = "Préstamo"
TIPO_ALMACEN_PRESTAMO = "Prestamo"
RANGOS_DIAS = [("d0_7", 0, 7), ("d8_15", 8, 15), ("d16_30", 16, 30), ("d30_mas", 31, None)]


def _vendedor_restringido():
	"""Sales Person del usuario si está limitado a su cartera; None si ve todo."""
	try:
		from modificaciones_interoptic.permissions.comun import vendedor_de
	except ImportError:
		return None
	return vendedor_de()


@frappe.whitelist()
def get_prestamos(cliente):
	"""Préstamos (Stock Entry con custom_tipo_transferencia = Préstamo) que siguen vigentes.

	Vigente = el serial que salió en esa transferencia sigue Activo en la bodega de
	préstamo del cliente (Warehouse.custom_cliente + custom_tipo_almacen = Prestamo).
	Si un serial se prestó más de una vez a la misma bodega, cuenta solo en la
	transferencia más reciente. Valor = custom_precio_consigna del renglón.
	La antigüedad se mide desde la fecha de creación de la transferencia.
	"""
	_validar_lectura()
	exigir_cliente_visible(cliente)

	vacio = {"filas": [], "totales": [], "rangos": [r[0] for r in RANGOS_DIAS]}
	bodegas = frappe.get_all(
		"Warehouse",
		filters={"custom_cliente": cliente, "custom_tipo_almacen": TIPO_ALMACEN_PRESTAMO},
		pluck="name",
	)
	if not bodegas:
		return vacio

	condicion_vendedor, valores = "", {"bodegas": bodegas, "tipo": TIPO_TRANSFERENCIA_PRESTAMO}
	vendedor = _vendedor_restringido()
	if vendedor:
		condicion_vendedor = "AND IFNULL(se.custom_vendedor, '') IN ('', %(vendedor)s)"
		valores["vendedor"] = vendedor

	renglones = frappe.db.sql(
		f"""
		SELECT se.name AS stock_entry, se.creation, se.posting_date, se.company,
			se.custom_vendedor AS vendedor, sed.t_warehouse AS bodega, sed.serial_no,
			sed.custom_precio_consigna AS precio,
			IFNULL(sed.custom_moneda_consigna, 'GTQ') AS moneda
		FROM `tabStock Entry Detail` sed
		INNER JOIN `tabStock Entry` se ON se.name = sed.parent
		WHERE se.docstatus = 1
			AND se.custom_tipo_transferencia = %(tipo)s
			AND sed.t_warehouse IN %(bodegas)s
			AND IFNULL(sed.serial_no, '') != ''
			{condicion_vendedor}
		ORDER BY se.creation
		""",
		valores,
		as_dict=True,
	)
	if not renglones:
		return vacio

	# (serial, bodega) -> renglón de la transferencia más reciente (ORDER BY creation).
	ultimo = {}
	for r in renglones:
		for serial in (r.serial_no or "").split("\n"):
			serial = serial.strip()
			if serial:
				ultimo[(serial, r.bodega)] = r

	activos = set(
		frappe.db.sql(
			"""SELECT name, warehouse FROM `tabSerial No`
			WHERE status = 'Active' AND warehouse IN %(bodegas)s""",
			{"bodegas": bodegas},
		)
	)

	hoy = getdate(nowdate())
	filas = {}
	for (serial, bodega), r in ultimo.items():
		if (serial, bodega) not in activos:
			continue
		fila = filas.setdefault(
			r.stock_entry,
			{
				"stock_entry": r.stock_entry,
				"fecha": str(getdate(r.creation)),
				"dias": (hoy - getdate(r.creation)).days,
				"empresa": r.company,
				"bodega": r.bodega,
				"vendedor": r.vendedor,
				"moneda": r.moneda,
				"unidades": 0,
				"total": 0.0,
				**{rango: 0.0 for rango, _d, _h in RANGOS_DIAS},
			},
		)
		fila["unidades"] += 1
		fila["total"] = flt(fila["total"] + flt(r.precio), 2)

	totales = {}
	for fila in filas.values():
		rango = next(n for n, desde, hasta in RANGOS_DIAS if hasta is None or fila["dias"] <= hasta)
		fila[rango] = fila["total"]
		t = totales.setdefault(
			fila["moneda"],
			{"moneda": fila["moneda"], "total": 0.0, "unidades": 0, "transferencias": 0,
			 **{n: 0.0 for n, _d, _h in RANGOS_DIAS}},
		)
		t["total"] = flt(t["total"] + fila["total"], 2)
		t[rango] = flt(t[rango] + fila["total"], 2)
		t["unidades"] += fila["unidades"]
		t["transferencias"] += 1

	return {
		"filas": sorted(filas.values(), key=lambda f: f["fecha"]),
		"totales": list(totales.values()),
		"rangos": [r[0] for r in RANGOS_DIAS],
	}
