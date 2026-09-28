# Copyright (c) 2026, Josue Velasquez and contributors
# For license information, please see license.txt
"""Datos del estado de cuenta de proveedores (resumen y detallado).

Equivalente, para cuentas por pagar, de `pagos_chappsa.estado_cuenta`.
"""

import frappe
from frappe.utils import flt

from pagos_chappsa.saldos_proveedor import TIPO_ACTIVO, TIPO_ANTICIPO, TOLERANCIA, get_config


def proveedores_visibles_o_none():
	"""Lista de proveedores que el usuario puede ver, o None si no tiene restricción."""
	from pagos_chappsa.permisos_proveedor import condicion_proveedores_visibles

	if not condicion_proveedores_visibles():
		return None
	return frappe.get_list("Supplier", fields=["name"], pluck="name", limit_page_length=0)


def _filtros_documento(cfg, empresa, moneda, proveedor, hasta):
	filtros = dict(cfg["filtros"])
	if empresa:
		filtros[cfg["empresa"]] = empresa
	if moneda:
		filtros[cfg["moneda"]] = moneda
	if proveedor:
		filtros[cfg["proveedor"]] = proveedor
	if hasta:
		filtros[cfg["fecha"]] = ("<=", hasta)
	return filtros


def get_abonos_masivo(tipo_documento, hasta=None):
	"""{documento: abonado} sobre pagos validados, opcionalmente con fecha de corte."""
	condiciones = ["p.docstatus = 1", "d.tipo_documento = %(tipo)s"]
	valores = {"tipo": tipo_documento}
	if hasta:
		condiciones.append("p.fecha <= %(hasta)s")
		valores["hasta"] = hasta

	filas = frappe.db.sql(
		"""
		select d.documento, sum(d.abono) as abonado
		from `tabDetalle Documento Pago Proveedor` d
		inner join `tabPago Proveedor` p on p.name = d.parent
		where {cond}
		group by d.documento
		""".format(cond=" and ".join(condiciones)),
		valores,
		as_dict=True,
	)
	return {f.documento: flt(f.abonado) for f in filas}


def get_detalle(empresa=None, moneda=None, proveedor=None, hasta=None, incluir_saldados=False):
	"""Una fila por documento, ordenado por proveedor y fecha."""
	from pagos_chappsa.permisos_proveedor import get_empresas_permitidas

	filas = []
	visibles = None if proveedor else proveedores_visibles_o_none()
	if visibles is not None and not visibles:
		return []

	empresas_perfil = get_empresas_permitidas()
	if empresas_perfil is not None:
		if empresa and empresa not in empresas_perfil:
			return []
		if not empresa and not empresas_perfil:
			return []

	for tipo in [TIPO_ACTIVO]:
		cfg = get_config(tipo)
		campos = [
			"name as documento",
			f"{cfg['proveedor']} as proveedor",
			"supplier_name as nombre_proveedor",
			f"{cfg['fecha']} as fecha",
			f"{cfg['moneda']} as moneda",
			f"{cfg['empresa']} as empresa",
			f"{cfg['valor']} as total",
			f"{cfg['referencia']} as numero_interno",
		]

		filtros = _filtros_documento(cfg, empresa, moneda, proveedor, hasta)
		if empresas_perfil is not None and not empresa:
			filtros[cfg["empresa"]] = ("in", empresas_perfil)
		if visibles is not None:
			filtros[cfg["proveedor"]] = ("in", visibles)

		documentos = frappe.get_list(tipo, filters=filtros, fields=campos, limit_page_length=0)

		if documentos:
			from pagos_chappsa.permisos_proveedor import filtrar_documentos_por_bodega

			permitidos = set(filtrar_documentos_por_bodega(tipo, [d.documento for d in documentos]))
			documentos = [d for d in documentos if d.documento in permitidos]
		abonos = get_abonos_masivo(tipo, hasta) if documentos else {}

		for d in documentos:
			abono = flt(abonos.get(d.documento, 0.0), 2)
			saldo = flt(flt(d.total) - abono, 2)
			if not incluir_saldados and abs(saldo) <= TOLERANCIA:
				continue
			filas.append(
				{
					"proveedor": d.proveedor,
					"nombre_proveedor": d.nombre_proveedor or d.proveedor,
					"tipo_documento": tipo,
					"documento": d.documento,
					"numero_interno": d.get("numero_interno") or "",
					"fecha": d.fecha,
					"empresa": d.empresa,
					"moneda": d.moneda,
					"total": flt(d.total, 2),
					"abono": abono,
					"saldo": saldo,
					"clase": "Cargo",
				}
			)

	filas += _lineas_anticipo(empresa, moneda, proveedor, hasta, visibles, empresas_perfil)
	filas.sort(key=lambda f: (f["nombre_proveedor"] or "", str(f["fecha"] or ""), f["documento"]))
	return filas


def _lineas_anticipo(empresa, moneda, proveedor, hasta, visibles=None, empresas_perfil=None):
	"""Anticipos con saldo a favor de la empresa, como línea de crédito del estado de cuenta."""
	if not frappe.has_permission(TIPO_ANTICIPO, "read"):
		return []

	filtros = {"docstatus": 1, "anticipo_disponible": (">", TOLERANCIA)}
	if visibles is not None:
		filtros["proveedor"] = ("in", visibles)
	if empresas_perfil is not None and not empresa:
		filtros["empresa"] = ("in", empresas_perfil)
	if empresa:
		filtros["empresa"] = empresa
	if moneda:
		filtros["moneda"] = moneda
	if proveedor:
		filtros["proveedor"] = proveedor
	if hasta:
		filtros["fecha"] = ("<=", hasta)

	filas = []
	for p in frappe.get_list(
		TIPO_ANTICIPO,
		filters=filtros,
		fields=["name", "proveedor", "nombre_proveedor", "fecha", "empresa", "moneda", "anticipo_disponible"],
		limit_page_length=0,
	):
		disponible = flt(p.anticipo_disponible, 2)
		filas.append(
			{
				"proveedor": p.proveedor,
				"nombre_proveedor": p.nombre_proveedor or p.proveedor,
				"tipo_documento": TIPO_ANTICIPO,
				"documento": p.name,
				"numero_interno": "",
				"fecha": p.fecha,
				"empresa": p.empresa,
				"moneda": p.moneda,
				"total": 0.0,
				"abono": disponible,
				"saldo": flt(-disponible, 2),
				"clase": "Anticipo",
			}
		)
	return filas


def get_resumen(empresa=None, moneda=None, proveedor=None, hasta=None, incluir_saldados=False):
	"""Una fila por proveedor: total, abonado y diferencia."""
	acumulado = {}
	for f in get_detalle(empresa, moneda, proveedor, hasta, incluir_saldados=True):
		clave = (f["proveedor"], f["moneda"])
		fila = acumulado.setdefault(
			clave,
			{
				"proveedor": f["proveedor"],
				"nombre_proveedor": f["nombre_proveedor"],
				"moneda": f["moneda"],
				"total": 0.0,
				"abonado": 0.0,
				"diferencia": 0.0,
				"documentos": 0,
			},
		)
		fila["total"] = flt(fila["total"] + f["total"], 2)
		fila["abonado"] = flt(fila["abonado"] + f["abono"], 2)
		if f["clase"] == "Cargo":
			fila["documentos"] += 1

	resumen = []
	for fila in acumulado.values():
		fila["diferencia"] = flt(fila["total"] - fila["abonado"], 2)
		if not incluir_saldados and abs(fila["diferencia"]) <= TOLERANCIA:
			continue
		resumen.append(fila)

	resumen.sort(key=lambda f: (f["nombre_proveedor"] or ""))
	return resumen
