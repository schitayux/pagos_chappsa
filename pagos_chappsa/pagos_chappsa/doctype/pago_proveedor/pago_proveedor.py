# Copyright (c) 2026, Josue Velasquez and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from pagos_chappsa.permisos_proveedor import (
	exigir_accion,
	exigir_empresa,
	exigir_reglas_de_pago,
	get_perfil,
)
from pagos_chappsa.saldos_proveedor import (
	TIPO_ANTICIPO,
	TOLERANCIA,
	get_abonos,
	get_activo_global,
	get_config,
	get_saldo_documento,
)


class PagoProveedor(Document):
	# ------------------------------------------------------------------
	# Ciclo de vida
	# ------------------------------------------------------------------
	def before_insert(self):
		if not self.cobrador:
			self.cobrador = frappe.session.user
		exigir_accion("guardar_borrador")

	def before_naming(self):
		perfil = get_perfil(self.cobrador or frappe.session.user)
		if perfil.serie_recibo:
			self.naming_series = perfil.serie_recibo

	def validate(self):
		self.validar_recibo_manual()
		self.validar_tipo_cambio()
		self.normalizar_hijos()
		self.validar_documentos()
		self.validar_formas_pago()
		self.calcular_totales()
		self.validar_cuadre()
		self.validar_permisos()
		self.set_estado()

	def before_update_after_submit(self):
		self.validar_recibo_manual()

	def before_submit(self):
		exigir_accion("validar")
		exigir_reglas_de_pago(self)

	def on_submit(self):
		self.set_estado()
		self.refrescar_anticipos()
		self.aplicar_pago_nativo_si_corresponde()

	def before_cancel(self):
		exigir_accion("cancelar_validado")
		self.validar_anticipo_no_consumido()
		self.validar_no_revierte_consumo_anticipo_nativo()

	def on_cancel(self):
		self.set_estado()
		self.refrescar_anticipos()
		self.revertir_pago_nativo_si_corresponde()

	def on_trash(self):
		exigir_accion("eliminar")
		if self.docstatus == 0:
			exigir_accion("cancelar_borrador")

	# ------------------------------------------------------------------
	# Integración nativa (Factura de Compra)
	# ------------------------------------------------------------------
	def aplicar_pago_nativo_si_corresponde(self):
		"""Con un Perfil de Pago Proveedor Global activo, cada pago validado aplica
		de inmediato su Payment Entry nativo en ERPNext."""
		if not get_activo_global():
			return

		from pagos_chappsa.integracion_nativa_proveedor import aplicar_pagos_nativos

		aplicar_pagos_nativos(self)

	def validar_no_revierte_consumo_anticipo_nativo(self):
		if not get_activo_global():
			return

		consumio_credito = any(
			f.tipo_documento == TIPO_ANTICIPO and flt(f.abono) < 0 for f in self.detalle_documentos
		)
		if consumio_credito:
			frappe.throw(
				_(
					"Este pago aplicó un anticipo de otro comprobante contra una factura, generando "
					"Payment Entry nativos en ERPNext. Cancelar ese cruce automáticamente no está "
					"soportado: cancele a mano en ERPNext el Payment Entry que quedó aplicado a la "
					"factura y restaure el anticipo original antes de cancelar este pago."
				)
			)

	def revertir_pago_nativo_si_corresponde(self):
		"""`validar_anticipo_no_consumido` (before_cancel) ya garantiza que si este
		pago dejó un anticipo, nadie más lo ha usado todavía."""
		if not get_activo_global():
			return

		from pagos_chappsa.integracion_nativa_proveedor import revertir_pagos_nativos

		revertir_pagos_nativos(self)

	def validar_recibo_manual(self):
		self.no_recibo_manual = (self.no_recibo_manual or "").strip()

		if not self.no_recibo_manual:
			frappe.throw(
				_("Ingrese el No. del comprobante de pago que respalda esta transacción."),
				title=_("Falta el No. de comprobante"),
			)

		duplicado = frappe.db.sql(
			"""
			select name, proveedor, fecha
			from `tabPago Proveedor`
			where no_recibo_manual = %(no_recibo)s
			  and name != %(name)s
			  and docstatus < 2
			limit 1
			""",
			{"no_recibo": self.no_recibo_manual, "name": self.name or ""},
			as_dict=True,
		)
		if duplicado:
			otro = duplicado[0]
			frappe.throw(
				_(
					"El No. de comprobante {0} ya se usó en el pago {1} (proveedor {2}, fecha {3}). "
					"Cada comprobante se registra una sola vez."
				).format(
					frappe.bold(self.no_recibo_manual),
					frappe.bold(otro.name),
					otro.proveedor,
					frappe.format(otro.fecha, {"fieldtype": "Date"}),
				),
				title=_("No. de comprobante repetido"),
			)

	def validar_tipo_cambio(self):
		"""El tipo de cambio real solo es obligatorio cuando este pago toca una
		cuenta en moneda distinta a la de la empresa (p. ej. pagos en USD cuya
		cuenta por pagar está en GTQ): sin él no hay forma de calcular el
		diferencial cambiario contra el documento que se está pagando."""
		self.tipo_cambio = flt(self.tipo_cambio)
		if not self.empresa or not self.moneda:
			return

		moneda_empresa = frappe.get_cached_value("Company", self.empresa, "default_currency")
		if moneda_empresa and self.moneda != moneda_empresa and self.tipo_cambio <= 0:
			frappe.throw(
				_(
					"Este pago está en {0} y la empresa {1} maneja {2}: indique el tipo de cambio real "
					"con el que se realizó este pago."
				).format(self.moneda, frappe.bold(self.empresa), moneda_empresa),
				title=_("Falta el tipo de cambio"),
			)

	def validar_permisos(self):
		exigir_empresa(self.empresa)

	# ------------------------------------------------------------------
	# Normalización
	# ------------------------------------------------------------------
	def normalizar_hijos(self):
		for fila in self.detalle_documentos:
			fila.moneda = self.moneda
			if not fila.tipo_documento:
				frappe.throw(_("Fila {0} del detalle de documentos: falta el tipo de documento.").format(fila.idx))
			fila.abono = flt(fila.abono, 2)

		for idx, fila in enumerate(self.detalle_formas_pago, start=1):
			fila.moneda = self.moneda
			fila.id_linea = idx
			fila.monto = flt(fila.monto, 2)
			if not fila.fecha:
				fila.fecha = self.fecha
			if fila.tipo == "Efectivo":
				# cuenta_contable sí se conserva: es la caja que el perfil Global
				# configuró para Efectivo (ver integracion_nativa_proveedor).
				fila.cuenta_banco = None
				fila.banco = None

	# ------------------------------------------------------------------
	# Validaciones del detalle de documentos
	# ------------------------------------------------------------------
	def validar_documentos(self):
		vistos = set()

		for fila in self.detalle_documentos:
			clave = (fila.tipo_documento, fila.documento)
			if clave in vistos:
				frappe.throw(
					_("El documento {0} aparece más de una vez en el detalle.").format(
						frappe.bold(fila.documento)
					)
				)
			vistos.add(clave)

			self.validar_pertenencia(fila)

			saldo_anterior, valor_original, abonado_previo = get_saldo_documento(
				fila.tipo_documento, fila.documento, excluir_pago=self.name
			)

			fila.valor_original = valor_original
			fila.abonado_previo = abonado_previo
			fila.saldo_anterior = saldo_anterior
			fila.clase = "Cargo" if valor_original >= 0 else "Crédito"
			fila.saldo = flt(saldo_anterior - flt(fila.abono), 2)

			self.validar_rango_abono(fila)

	def validar_pertenencia(self, fila):
		"""El documento debe ser del mismo proveedor, empresa y moneda que el pago."""
		if fila.tipo_documento == TIPO_ANTICIPO:
			campos = ["proveedor", "empresa", "moneda", "docstatus"]
			datos = frappe.db.get_value(TIPO_ANTICIPO, fila.documento, campos, as_dict=True)
			if not datos:
				frappe.throw(_("El anticipo {0} no existe.").format(fila.documento))
			if datos.docstatus != 1:
				frappe.throw(_("El anticipo {0} no está validado.").format(frappe.bold(fila.documento)))
			proveedor, empresa, moneda = datos.proveedor, datos.empresa, datos.moneda
			if fila.documento == self.name:
				frappe.throw(_("Un pago no puede aplicarse su propio anticipo."))
		else:
			cfg = get_config(fila.tipo_documento)
			datos = frappe.db.get_value(
				fila.tipo_documento,
				fila.documento,
				[cfg["proveedor"], cfg["empresa"], cfg["moneda"], "docstatus"],
				as_dict=True,
			)
			if not datos:
				frappe.throw(_("El documento {0} no existe.").format(fila.documento))
			if datos.docstatus != 1:
				frappe.throw(
					_("El documento {0} no está validado y no puede recibir abonos.").format(
						frappe.bold(fila.documento)
					)
				)
			proveedor = datos.get(cfg["proveedor"])
			empresa = datos.get(cfg["empresa"])
			moneda = datos.get(cfg["moneda"])

		if proveedor != self.proveedor:
			frappe.throw(
				_("El documento {0} pertenece al proveedor {1}, no a {2}.").format(
					frappe.bold(fila.documento), proveedor, self.proveedor
				)
			)
		if empresa != self.empresa:
			frappe.throw(
				_("El documento {0} pertenece a la empresa {1}, no a {2}.").format(
					frappe.bold(fila.documento), empresa, self.empresa
				)
			)
		if moneda != self.moneda:
			frappe.throw(
				_("El documento {0} está en {1} y el pago está en {2}. Un pago no puede mezclar monedas.").format(
					frappe.bold(fila.documento), moneda, self.moneda
				)
			)

	def validar_rango_abono(self, fila):
		abono = flt(fila.abono, 2)
		saldo = flt(fila.saldo_anterior, 2)

		if fila.clase == "Cargo":
			if abono < -TOLERANCIA:
				frappe.throw(
					_("Fila {0} ({1}): el abono no puede ser negativo.").format(fila.idx, fila.documento)
				)
			if abono - saldo > TOLERANCIA:
				frappe.throw(
					_("Fila {0} ({1}): el abono {2} supera el saldo pendiente {3}.").format(
						fila.idx, frappe.bold(fila.documento), abono, saldo
					)
				)
		else:
			if abono > TOLERANCIA:
				frappe.throw(
					_("Fila {0} ({1}): un crédito solo puede aplicarse con signo negativo.").format(
						fila.idx, fila.documento
					)
				)
			if saldo - abono > TOLERANCIA:
				frappe.throw(
					_("Fila {0} ({1}): se están aplicando {2} pero solo hay {3} disponibles.").format(
						fila.idx, frappe.bold(fila.documento), abs(abono), abs(saldo)
					)
				)

	# ------------------------------------------------------------------
	# Validaciones de formas de pago
	# ------------------------------------------------------------------
	def validar_formas_pago(self):
		for fila in self.detalle_formas_pago:
			if flt(fila.monto) <= 0:
				frappe.throw(
					_("Forma de pago {0}: el monto debe ser mayor a cero.").format(fila.id_linea)
				)

			if fila.tipo in ("Transferencia", "Cheque", "Tarjeta de Crédito") and not fila.no_documento:
				frappe.throw(
					_("Forma de pago {0} ({1}): indique el número de documento o referencia.").format(
						fila.id_linea, fila.tipo
					)
				)

			if fila.tipo == "Retención" and not fila.no_documento:
				frappe.throw(
					_("Forma de pago {0} (Retención): indique la referencia de la constancia.").format(
						fila.id_linea
					)
				)

			if fila.tipo == "Transferencia" and not fila.cuenta_banco:
				frappe.throw(
					_("Forma de pago {0} (Transferencia): indique la cuenta de banco de la empresa.").format(
						fila.id_linea
					)
				)

			if flt(fila.gasto_bancario) > flt(fila.monto) + TOLERANCIA:
				frappe.throw(
					_("Forma de pago {0}: el gasto bancario ({1}) no puede ser mayor que el monto de la línea ({2}).").format(
						fila.id_linea, flt(fila.gasto_bancario), flt(fila.monto)
					)
				)

			if fila.cuenta_banco:
				empresa_cuenta = frappe.db.get_value("Bank Account", fila.cuenta_banco, "company")
				if empresa_cuenta and empresa_cuenta != self.empresa:
					frappe.throw(
						_("Forma de pago {0}: la cuenta {1} pertenece a {2}, no a {3}.").format(
							fila.id_linea, fila.cuenta_banco, empresa_cuenta, self.empresa
						)
					)

	# ------------------------------------------------------------------
	# Totales
	# ------------------------------------------------------------------
	def calcular_totales(self):
		total_aplicado = 0.0
		total_creditos = 0.0

		for fila in self.detalle_documentos:
			abono = flt(fila.abono, 2)
			if abono >= 0:
				total_aplicado += abono
			else:
				total_creditos += -abono

		self.total_aplicado = flt(total_aplicado, 2)
		self.total_creditos_aplicados = flt(total_creditos, 2)
		self.monto_recibido = flt(self.monto_recibido, 2)

		# El gasto bancario que el usuario asignó explícitamente en una forma de
		# pago ya tiene destino propio (la cuenta de gastos bancarios del perfil
		# Global, ver integracion_nativa_proveedor._aplicar_gasto_bancario): no
		# es dinero suelto y no debe contarse como excedente/anticipo pendiente
		# de reconciliar. Solo se vuelve anticipo lo que el usuario deja sin
		# asignar a ningún documento NI a gasto bancario.
		total_gasto_bancario = flt(sum(flt(f.gasto_bancario) for f in self.detalle_formas_pago), 2)
		self.excedente = flt(
			self.monto_recibido - (self.total_aplicado - self.total_creditos_aplicados) - total_gasto_bancario, 2
		)
		self.total_global = flt(self.total_aplicado + max(self.excedente, 0.0), 2)
		self.total_formas_pago = flt(sum(flt(f.monto) for f in self.detalle_formas_pago), 2)

		self.set_anticipo_consumido()

	def set_anticipo_consumido(self):
		consumido = 0.0
		if not self.is_new():
			abonos = get_abonos([(TIPO_ANTICIPO, self.name)], excluir_pago=self.name)
			consumido = -flt(abonos.get((TIPO_ANTICIPO, self.name), 0.0))

		self.anticipo_aplicado = flt(consumido, 2)
		if self.docstatus == 2:
			self.anticipo_disponible = 0.0
		else:
			self.anticipo_disponible = flt(max(flt(self.excedente) - self.anticipo_aplicado, 0.0), 2)

	# ------------------------------------------------------------------
	# Cuadre
	# ------------------------------------------------------------------
	def validar_cuadre(self):
		if not self.detalle_documentos and flt(self.monto_recibido) <= 0:
			frappe.throw(_("Ingrese un monto a pagar o aplique al menos un documento."))

		if flt(self.excedente) < -TOLERANCIA:
			frappe.throw(
				_(
					"Se está aplicando más de lo pagado: faltan {0} de fondos. "
					"Aumente el monto pagado o aplique un anticipo/nota de crédito del proveedor."
				).format(abs(flt(self.excedente)))
			)

		if abs(flt(self.total_formas_pago) - flt(self.monto_recibido)) > TOLERANCIA:
			frappe.throw(
				_("Las formas de pago suman {0} y el monto pagado es {1}. Deben coincidir.").format(
					flt(self.total_formas_pago), flt(self.monto_recibido)
				)
			)

	def set_estado(self):
		self.estado = {0: "Borrador", 1: "Validado", 2: "Cancelado"}.get(self.docstatus, "Borrador")

	# ------------------------------------------------------------------
	# Anticipos
	# ------------------------------------------------------------------
	def validar_anticipo_no_consumido(self):
		if flt(self.excedente) <= TOLERANCIA:
			return

		consumido = -flt(
			get_abonos([(TIPO_ANTICIPO, self.name)], excluir_pago=self.name).get((TIPO_ANTICIPO, self.name), 0.0)
		)
		if consumido > TOLERANCIA:
			frappe.throw(
				_(
					"No se puede cancelar: {0} del anticipo generado por este pago ya fue aplicado en otros pagos. "
					"Cancele primero esos pagos."
				).format(flt(consumido, 2))
			)

	def refrescar_anticipos(self):
		self.set_anticipo_consumido()
		self.db_set("anticipo_aplicado", self.anticipo_aplicado, update_modified=False)
		self.db_set("anticipo_disponible", self.anticipo_disponible, update_modified=False)

		for nombre in {
			f.documento for f in self.detalle_documentos if f.tipo_documento == TIPO_ANTICIPO and f.documento
		}:
			actualizar_anticipo(nombre)


def actualizar_anticipo(nombre_pago):
	"""Recalcula anticipo_aplicado / anticipo_disponible de un Pago Proveedor."""
	excedente = flt(frappe.db.get_value(TIPO_ANTICIPO, nombre_pago, "excedente"))
	consumido = -flt(get_abonos([(TIPO_ANTICIPO, nombre_pago)]).get((TIPO_ANTICIPO, nombre_pago), 0.0))
	disponible = flt(max(excedente - consumido, 0.0), 2)

	frappe.db.set_value(
		TIPO_ANTICIPO,
		nombre_pago,
		{"anticipo_aplicado": flt(consumido, 2), "anticipo_disponible": disponible},
		update_modified=False,
	)
