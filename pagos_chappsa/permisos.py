# Copyright (c) 2026, Josue Velasquez and contributors
# For license information, please see license.txt
"""Delegación de permisos del módulo de cobranza.

Cada cobrador puede tener un "Perfil de Cobranza" que define su correlativo propio,
qué puede hacer (guardar, validar, cancelar, reimprimir), bajo qué reglas (pagos
parciales, anticipos) y sobre qué documentos (empresas y bodegas).

Criterio de diseño: **sin perfil, sin restricción.** Un usuario con permiso sobre el
DocType y sin perfil trabaja igual que antes. El perfil solo sirve para acotar. Así
crear el primer perfil no deja fuera al resto del equipo de golpe.
"""

import frappe
from frappe import _
from frappe.utils import flt

SERIE_POR_DEFECTO = "PC-.YYYY.-"

# Valores cuando el usuario no tiene perfil: todo permitido, sin acotar.
SIN_RESTRICCION = frappe._dict(
	{
		"tiene_perfil": False,
		"habilitado": 1,
		"serie_recibo": SERIE_POR_DEFECTO,
		"permitir_guardar_borradores": 1,
		"permitir_validar_pagos": 1,
		"permitir_cancelar_borradores": 1,
		"permitir_cancelar_validados": 1,
		"permitir_reimpresion_recibo": 1,
		"permitir_eliminar_pagos": 1,
		"permitir_reporte_recibos": 1,
		"permitir_estado_cuenta_resumen": 1,
		"permitir_estado_cuenta_detallado": 1,
		"permitir_pagos_parciales": 1,
		"permitir_anticipos": 1,
		"todas_las_empresas": 1,
		"todas_las_bodegas": 1,
		"empresas": [],
		"bodegas": [],
	}
)


def get_perfil(usuario=None):
	"""Perfil del usuario, o SIN_RESTRICCION si no tiene."""
	usuario = usuario or frappe.session.user

	if usuario == "Administrator":
		return SIN_RESTRICCION

	nombre = frappe.db.exists("Perfil de Cobranza", usuario)
	if not nombre:
		return SIN_RESTRICCION

	doc = frappe.get_cached_doc("Perfil de Cobranza", nombre)
	perfil = frappe._dict(
		{
			"tiene_perfil": True,
			"nombre": doc.name,
			"habilitado": doc.habilitado,
			"serie_recibo": doc.serie_recibo or SERIE_POR_DEFECTO,
			"permitir_guardar_borradores": doc.permitir_guardar_borradores,
			"permitir_validar_pagos": doc.permitir_validar_pagos,
			"permitir_cancelar_borradores": doc.permitir_cancelar_borradores,
			"permitir_cancelar_validados": doc.permitir_cancelar_validados,
			"permitir_reimpresion_recibo": doc.permitir_reimpresion_recibo,
			"permitir_eliminar_pagos": doc.permitir_eliminar_pagos,
			"permitir_reporte_recibos": doc.permitir_reporte_recibos,
			"permitir_estado_cuenta_resumen": doc.permitir_estado_cuenta_resumen,
			"permitir_estado_cuenta_detallado": doc.permitir_estado_cuenta_detallado,
			"permitir_pagos_parciales": doc.permitir_pagos_parciales,
			"permitir_anticipos": doc.permitir_anticipos,
			"todas_las_empresas": doc.todas_las_empresas,
			"todas_las_bodegas": doc.todas_las_bodegas,
			"empresas": [f.empresa for f in doc.empresas],
			"bodegas": [(f.bodega, f.incluir_subbodegas) for f in doc.bodegas],
		}
	)
	return perfil


def exigir_habilitado(perfil=None):
	perfil = perfil or get_perfil()
	if perfil.tiene_perfil and not perfil.habilitado:
		frappe.throw(
			_("Su perfil de cobranza está deshabilitado. Contacte al encargado de cobros."),
			frappe.PermissionError,
		)


# ---------------------------------------------------------------------------
# Alcance: empresas
# ---------------------------------------------------------------------------
def get_empresas_permitidas(perfil=None):
	"""Lista de empresas que el usuario puede cobrar, o None si no hay restricción."""
	perfil = perfil or get_perfil()
	if perfil.todas_las_empresas:
		return None
	return list(perfil.empresas or [])


def exigir_empresa(empresa, perfil=None):
	permitidas = get_empresas_permitidas(perfil)
	if permitidas is not None and empresa not in permitidas:
		frappe.throw(
			_("No tiene permiso para cobrar documentos de la empresa {0}.").format(frappe.bold(empresa)),
			frappe.PermissionError,
		)


# ---------------------------------------------------------------------------
# Alcance: bodegas (árbol)
# ---------------------------------------------------------------------------
def get_bodegas_permitidas(perfil=None):
	"""Conjunto de bodegas visibles, expandiendo el árbol donde se pidió.

	Devuelve None cuando no hay restricción (que no es lo mismo que un conjunto
	vacío: vacío significaría que no puede ver nada).
	"""
	perfil = perfil or get_perfil()
	if perfil.todas_las_bodegas:
		return None

	permitidas = set()
	for bodega, incluir_hijas in perfil.bodegas or []:
		permitidas.add(bodega)
		if not incluir_hijas:
			continue
		lft, rgt = frappe.db.get_value("Warehouse", bodega, ["lft", "rgt"]) or (None, None)
		if lft is None:
			continue
		# Nested set: todo lo que cuelga de esta bodega en el árbol.
		descendientes = frappe.get_all(
			"Warehouse",
			filters={"lft": (">=", lft), "rgt": ("<=", rgt)},
			pluck="name",
			limit_page_length=0,
		)
		permitidas.update(descendientes)

	return permitidas


def filtrar_documentos_por_bodega(tipo_documento, nombres, perfil=None):
	"""De `nombres`, deja solo los documentos que despachan desde una bodega permitida."""
	permitidas = get_bodegas_permitidas(perfil)
	if permitidas is None or not nombres:
		return nombres
	if not permitidas:
		return []

	tabla_hija = f"tab{tipo_documento} Item"
	marcadores_doc = ", ".join(["%s"] * len(nombres))
	lista_bodegas = list(permitidas)
	marcadores_bod = ", ".join(["%s"] * len(lista_bodegas))

	filas = frappe.db.sql(
		f"""select distinct parent from `{tabla_hija}`
		    where parent in ({marcadores_doc}) and warehouse in ({marcadores_bod})""",
		tuple(nombres) + tuple(lista_bodegas),
	)
	return [f[0] for f in filas]


# ---------------------------------------------------------------------------
# Reglas de cobro
# ---------------------------------------------------------------------------
def exigir_reglas_de_pago(doc, perfil=None):
	"""Valida pagos parciales y anticipos contra el perfil del cobrador."""
	from pagos_chappsa.saldos import TOLERANCIA

	perfil = perfil or get_perfil(doc.get("cobrador") or frappe.session.user)

	if not perfil.permitir_pagos_parciales:
		for fila in doc.detalle_documentos:
			abono, saldo = flt(fila.abono, 2), flt(fila.saldo_anterior, 2)
			if abs(abono) <= TOLERANCIA:
				continue
			if abs(abono - saldo) > TOLERANCIA:
				frappe.throw(
					_(
						"Su perfil no permite pagos parciales: el documento {0} debe abonarse completo "
						"({1}) o quedar en cero."
					).format(frappe.bold(fila.documento), saldo)
				)

	if not perfil.permitir_anticipos:
		if flt(doc.excedente) > TOLERANCIA:
			frappe.throw(
				_(
					"Su perfil no permite manejar anticipos, y este pago dejaría un excedente de {0}. "
					"Baje el monto recibido o aplíquelo a más documentos."
				).format(flt(doc.excedente, 2))
			)
		if any(flt(f.abono) < -TOLERANCIA for f in doc.detalle_documentos):
			frappe.throw(_("Su perfil no permite aplicar anticipos ni notas de crédito del cliente."))


def exigir_accion(accion, perfil=None):
	"""accion: guardar_borrador | validar | cancelar_borrador | cancelar_validado | reimprimir"""
	perfil = perfil or get_perfil()
	exigir_habilitado(perfil)

	mapa = {
		"guardar_borrador": ("permitir_guardar_borradores", _("guardar borradores de pago")),
		"validar": ("permitir_validar_pagos", _("validar pagos")),
		"cancelar_borrador": ("permitir_cancelar_borradores", _("cancelar borradores")),
		"cancelar_validado": ("permitir_cancelar_validados", _("cancelar pagos ya validados")),
		"reimprimir": ("permitir_reimpresion_recibo", _("reimprimir recibos")),
		"eliminar": ("permitir_eliminar_pagos", _("eliminar pagos")),
		"reporte_recibos": ("permitir_reporte_recibos", _("consultar el reporte de recibos emitidos")),
		"estado_cuenta_resumen": ("permitir_estado_cuenta_resumen", _("ver el estado de cuenta resumen")),
		"estado_cuenta_detallado": ("permitir_estado_cuenta_detallado", _("ver el estado de cuenta detallado")),
	}
	campo, descripcion = mapa[accion]
	if not perfil.get(campo):
		frappe.throw(_("Su perfil de cobranza no le permite {0}.").format(descripcion), frappe.PermissionError)


# ---------------------------------------------------------------------------
# Visibilidad de clientes — reglas NATIVAS de ERPNext/Frappe
#
# Esta app NO reimplementa la seguridad de ventas. La hereda:
# `DatabaseQuery.build_match_conditions()` compone, para el usuario actual, tanto
# los **User Permissions** nativos (empresa, custom_sociedad, etc.) como los
# `permission_query_conditions` que registre CUALQUIER app instalada — entre ellos
# el de `modificaciones_interoptic`, que ata `Customer.custom_vendedor` al
# `Sales Person` cuyo `custom_user` es el usuario.
#
# Consecuencia deseada: si mañana cambian esas reglas allá, la cobranza las sigue
# sin tocar una línea aquí.
# ---------------------------------------------------------------------------
def condicion_clientes_visibles(user=None):
	"""Condición SQL sobre `tabCustomer` con las reglas del usuario. '' = sin restricción."""
	from frappe.model.db_query import DatabaseQuery

	user = user or frappe.session.user
	if user == "Administrator":
		return ""
	try:
		return DatabaseQuery("Customer", user=user).build_match_conditions() or ""
	except Exception:
		# Ante cualquier duda, restringir en vez de abrir.
		frappe.log_error(frappe.get_traceback(), "pagos_chappsa: condicion_clientes_visibles")
		return "1 = 0"


def cliente_visible(cliente, user=None):
	if not cliente:
		return False
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	return bool(
		frappe.get_list("Customer", filters={"name": cliente}, fields=["name"], limit=1, ignore_ifnull=True)
	)


def exigir_cliente_visible(cliente):
	if not cliente_visible(cliente):
		frappe.throw(
			_("No tiene permiso para ver el cliente {0}.").format(frappe.bold(cliente)),
			frappe.PermissionError,
		)


def pago_cliente_query(user=None):
	"""permission_query_conditions para Pago Cliente.

	Un cobrador ve sus propios pagos y los de los clientes que ya puede ver por las
	reglas nativas. Registrado en hooks.py, así aplica también a list view y reportes.
	"""
	user = user or frappe.session.user
	if user == "Administrator":
		return ""

	condicion = condicion_clientes_visibles(user)
	if not condicion:
		return ""

	usuario = frappe.db.escape(user)
	return (
		f"(`tabPago Cliente`.`cobrador` = {usuario} or `tabPago Cliente`.`cliente` in "
		f"(select `tabCustomer`.`name` from `tabCustomer` where {condicion}))"
	)
