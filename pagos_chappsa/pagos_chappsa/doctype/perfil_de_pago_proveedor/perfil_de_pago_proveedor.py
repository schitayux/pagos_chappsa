# Copyright (c) 2026, Josue Velasquez and contributors
# For license information, please see license.txt
"""Perfil de Pagos a Proveedores.

Es el equivalente, para cuentas por pagar, del Perfil de Cobranza. A diferencia
de aquel, aquí no existe un "Origen" que elegir: el único documento que se paga
es la Factura de Compra, así que el perfil marcado como Global solo sirve para
(a) activar la integración con pagos nativos de ERPNext y (b) alojar la
configuración de cuentas contables por forma de pago.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class PerfildePagoProveedor(Document):
	def autoname(self):
		# El perfil Global puede no representar a ningún usuario (es_global sin
		# usuario): es puro interruptor de sistema, así que no puede nombrarse con
		# el campo usuario como hacen los perfiles normales.
		if self.es_global and not self.usuario:
			self.name = "Perfil Global de Pagos a Proveedores"

	def validate(self):
		if self.usuario:
			# Serie de comprobante y alcance por empresa/bodega solo tienen sentido
			# para el perfil de un usuario real; un Global sin usuario es únicamente
			# el interruptor de activación y no debe exigirlos.
			self.validar_serie()
			self.validar_alcance()
		self.validar_global()

	def on_update(self):
		sincronizar_series_en_pago_proveedor()
		frappe.cache.delete_value("pagos_chappsa_perfiles_proveedor")
		frappe.cache.delete_value("pagos_chappsa_proveedor_activo_global")

	def on_trash(self):
		sincronizar_series_en_pago_proveedor(excluir=self.name)
		frappe.cache.delete_value("pagos_chappsa_perfiles_proveedor")
		frappe.cache.delete_value("pagos_chappsa_proveedor_activo_global")

	def validar_serie(self):
		serie = (self.serie_recibo or "").strip()
		self.serie_recibo = serie

		if "#" not in serie:
			frappe.throw(
				_("La serie de comprobante debe incluir almohadillas para el correlativo, por ejemplo {0}.").format(
					frappe.bold("PP-JV-.YYYY.-.#####")
				)
			)

		# Dos usuarios con la misma serie compartirían correlativo, que es justo lo que se quiere evitar.
		otro = frappe.db.get_value(
			"Perfil de Pago Proveedor", {"serie_recibo": serie, "name": ("!=", self.name)}, "name"
		)
		if otro:
			frappe.throw(
				_("La serie {0} ya está asignada al usuario {1}. Cada usuario necesita su propio correlativo.").format(
					frappe.bold(serie), frappe.bold(otro)
				)
			)

	def validar_alcance(self):
		if not self.todas_las_empresas and not self.empresas:
			frappe.throw(_("Indique al menos una empresa, o marque «Todas las empresas»."))
		if not self.todas_las_bodegas and not self.bodegas:
			frappe.throw(_("Indique al menos una bodega, o marque «Todas las bodegas»."))

		vistas = set()
		for fila in self.bodegas:
			if fila.bodega in vistas:
				frappe.throw(_("La bodega {0} está repetida.").format(frappe.bold(fila.bodega)))
			vistas.add(fila.bodega)

		vistas = set()
		for fila in self.empresas:
			if fila.empresa in vistas:
				frappe.throw(_("La empresa {0} está repetida.").format(frappe.bold(fila.empresa)))
			vistas.add(fila.empresa)

	def validar_global(self):
		if not self.es_global:
			return

		otro = frappe.db.get_value(
			"Perfil de Pago Proveedor", {"es_global": 1, "name": ("!=", self.name)}, "name"
		)
		if otro:
			frappe.throw(
				_(
					"Ya existe un perfil Global ({0}). Solo puede haber uno a la vez: desmárquelo "
					"primero si quiere que este perfil sea el nuevo Global."
				).format(frappe.bold(otro))
			)


def sincronizar_series_en_pago_proveedor(excluir=None):
	"""Mantiene el desplegable de series de Pago Proveedor al día con los perfiles.

	Sin esto, el correlativo propio de cada usuario no aparecería como opción en el
	formulario nativo y solo funcionaría desde la página.
	"""
	from frappe.custom.doctype.property_setter.property_setter import make_property_setter

	series = ["PP-.YYYY.-"]
	for fila in frappe.get_all("Perfil de Pago Proveedor", fields=["name", "serie_recibo"]):
		if excluir and fila.name == excluir:
			continue
		if fila.serie_recibo and fila.serie_recibo not in series:
			series.append(fila.serie_recibo)

	make_property_setter(
		"Pago Proveedor", "naming_series", "options", "\n".join(series), "Text", validate_fields_for_doctype=False
	)
