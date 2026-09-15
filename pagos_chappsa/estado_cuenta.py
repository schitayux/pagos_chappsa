# Copyright (c) 2026, Josue Velasquez and contributors
# For license information, please see license.txt
"""Datos del estado de cuenta de clientes (resumen y detallado).

Toda consulta pasa por `frappe.get_list`, de modo que hereda las reglas nativas de
visibilidad del sitio (User Permissions + permission_query_conditions de cualquier
app). Un vendedor solo ve sus clientes, aquí igual que en el resto de ERPNext.

Los anticipos disponibles del cliente entran como línea de crédito: un estado de
cuenta que ignore dinero ya recibido induciría a cobrar dos veces.
"""

import frappe
from frappe.utils import flt

from pagos_chappsa.saldos import TIPO_ANTICIPO, TOLERANCIA, TIPOS_ACTIVOS, get_config


def clientes_visibles_o_none():
	"""Lista de clientes que el usuario puede ver, o None si no tiene restricción.

	En este sitio conviven DOS mecanismos nativos que no coinciden: `Customer` se
	restringe por Sales Person (aplica a todos los vendedores mapeados), mientras que
	`Delivery Note` solo restringe a una lista fija de dos usuarios. Un estado de
	cuenta armado solo sobre notas de entrega le mostraría a un vendedor clientes que
	ni siquiera puede abrir. Aquí se intersecta con la visibilidad de CLIENTE, que es
	la política que rige "cada quien ve los suyos".
	"""
	from pagos_chappsa.permisos import condicion_clientes_visibles

	if not condicion_clientes_visibles():
		return None
	return frappe.get_list("Customer", fields=["name"], pluck="name", limit_page_length=0)


def _filtros_documento(cfg, empresa, moneda, cliente, hasta):
	filtros = dict(cfg["filtros"])
	if empresa:
		filtros[cfg["empresa"]] = empresa
	if moneda:
		filtros[cfg["moneda"]] = moneda
	if cliente:
		filtros[cfg["cliente"]] = cliente
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
		from `tabDetalle Documento Pago` d
		inner join `tabPago Cliente` p on p.name = d.parent
		where {cond}
		group by d.documento
		""".format(cond=" and ".join(condiciones)),
		valores,
		as_dict=True,
	)
	return {f.documento: flt(f.abonado) for f in filas}


def get_detalle(empresa=None, moneda=None, cliente=None, hasta=None, incluir_saldados=False):
	"""Una fila por documento, ordenado por cliente y fecha.

	Campos: cliente, nombre_cliente, orden_trabajo, tipo_documento, documento,
	numero_interno, fecha, empresa, moneda, total, abono, saldo, clase.
	"""
	from pagos_chappsa.permisos import get_empresas_permitidas

	filas = []
	visibles = None if cliente else clientes_visibles_o_none()
	if visibles is not None and not visibles:
		return []

	# Empresas del Perfil de Cobranza (None = sin restricción).
	empresas_perfil = get_empresas_permitidas()
	if empresas_perfil is not None:
		if empresa and empresa not in empresas_perfil:
			return []
		if not empresa and not empresas_perfil:
			return []

	for tipo in TIPOS_ACTIVOS:
		cfg = get_config(tipo)
		campos = [
			"name as documento",
			f"{cfg['cliente']} as cliente",
			"customer_name as nombre_cliente",
			f"{cfg['fecha']} as fecha",
			f"{cfg['moneda']} as moneda",
			f"{cfg['empresa']} as empresa",
			f"{cfg['valor']} as total",
			f"{cfg['referencia']} as numero_interno",
		]
		if frappe.get_meta(tipo).get_field("custom_orden_ie"):
			campos.append("custom_orden_ie as orden_trabajo")

		filtros = _filtros_documento(cfg, empresa, moneda, cliente, hasta)
		if empresas_perfil is not None and not empresa:
			filtros[cfg["empresa"]] = ("in", empresas_perfil)
		if visibles is not None:
			filtros[cfg["cliente"]] = ("in", visibles)

		# get_list, no get_all: hereda la seguridad nativa.
		documentos = frappe.get_list(tipo, filters=filtros, fields=campos, limit_page_length=0)

		# Y además el alcance por bodegas del Perfil de Cobranza: si a un cobrador se
		# le limitaron las bodegas, su estado de cuenta debe mostrar exactamente los
		# mismos documentos que ve en el panel de pagos, no más.
		if documentos:
			from pagos_chappsa.permisos import filtrar_documentos_por_bodega

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
					"cliente": d.cliente,
					"nombre_cliente": d.nombre_cliente or d.cliente,
					# Solo se arma si la nota trae orden de trabajo (viene del panel de control).
					"orden_trabajo": d.get("orden_trabajo") or "",
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

	filas += _lineas_anticipo(empresa, moneda, cliente, hasta, visibles, empresas_perfil)
	filas.sort(key=lambda f: (f["nombre_cliente"] or "", str(f["fecha"] or ""), f["documento"]))
	return filas


def _lineas_anticipo(empresa, moneda, cliente, hasta, visibles=None, empresas_perfil=None):
	"""Anticipos con saldo a favor, como línea de crédito del estado de cuenta."""
	# Los anticipos viven en Pago Cliente. Quien no puede leer pagos (p. ej. un rol de
	# bodega o ventas con acceso solo al estado de cuenta) simplemente no ve esas
	# líneas, en vez de que el reporte entero falle con PermissionError.
	if not frappe.has_permission(TIPO_ANTICIPO, "read"):
		return []

	filtros = {"docstatus": 1, "anticipo_disponible": (">", TOLERANCIA)}
	if visibles is not None:
		filtros["cliente"] = ("in", visibles)
	if empresas_perfil is not None and not empresa:
		filtros["empresa"] = ("in", empresas_perfil)
	if empresa:
		filtros["empresa"] = empresa
	if moneda:
		filtros["moneda"] = moneda
	if cliente:
		filtros["cliente"] = cliente
	if hasta:
		filtros["fecha"] = ("<=", hasta)

	filas = []
	for p in frappe.get_list(
		TIPO_ANTICIPO,
		filters=filtros,
		fields=["name", "cliente", "nombre_cliente", "fecha", "empresa", "moneda", "anticipo_disponible"],
		limit_page_length=0,
	):
		disponible = flt(p.anticipo_disponible, 2)
		filas.append(
			{
				"cliente": p.cliente,
				"nombre_cliente": p.nombre_cliente or p.cliente,
				"orden_trabajo": "",
				"tipo_documento": TIPO_ANTICIPO,
				"documento": p.name,
				"numero_interno": "",
				"fecha": p.fecha,
				"empresa": p.empresa,
				"moneda": p.moneda,
				# El anticipo no suma cargo; es dinero ya recibido y sin aplicar.
				"total": 0.0,
				"abono": disponible,
				"saldo": flt(-disponible, 2),
				"clase": "Anticipo",
			}
		)
	return filas


def get_resumen(empresa=None, moneda=None, cliente=None, hasta=None, incluir_saldados=False):
	"""Una fila por cliente: total, abonado y diferencia.

	Cuadra exactamente con `get_detalle`: es la misma base agregada por cliente.
	"""
	acumulado = {}
	for f in get_detalle(empresa, moneda, cliente, hasta, incluir_saldados=True):
		clave = (f["cliente"], f["moneda"])
		fila = acumulado.setdefault(
			clave,
			{
				"cliente": f["cliente"],
				"nombre_cliente": f["nombre_cliente"],
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

	resumen.sort(key=lambda f: (f["nombre_cliente"] or ""))
	return resumen
