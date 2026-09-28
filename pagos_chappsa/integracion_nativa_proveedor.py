# Copyright (c) 2026, Josue Velasquez and contributors
# For license information, please see license.txt
"""Puente quirúrgico entre Pagos a Proveedores y la contabilidad nativa de ERPNext.

Equivalente, para cuentas por pagar, de `pagos_chappsa.integracion_nativa`. Es
más simple que aquel porque aquí NO hay dos modos: el único documento que se
paga es la Factura de Compra, así que cada Pago Proveedor validado aplica de
inmediato su Payment Entry nativo (payment_type "Pay", party_type "Supplier"),
igual que el modo "Factura de Venta" del lado de cobranza.

La activación es explícita: nada de este módulo toca facturas ni pagos nativos
hasta que exista un Perfil de Pago Proveedor marcado como Global (ver
`pagos_chappsa.saldos_proveedor.get_activo_global`).

Anticipos: cuando un Pago Proveedor deja excedente (dinero entregado que no se
aplicó a ninguna factura), ese excedente se registra como un Payment Entry
nativo "Pay" SIN referencia (un anticipo real en ERPNext). Cuando un pago
posterior consume ese anticipo como crédito contra una factura, el anticipo
original se CANCELA y se reemplaza por (a) un Payment Entry que aplica lo
consumido contra esa factura y, si sobra algo, (b) un nuevo anticipo por el
remanente — nunca se edita en sitio un Payment Entry ya validado.

Límites deliberados (idénticos en espíritu a los de cobranza):

* Si un Pago Proveedor reparte su abono entre más de un documento Y usa más de
  una forma de pago (o crédito de anticipo) a la vez, se exige registrar el
  pago nativo a mano en ERPNext en vez de adivinar.

* Cancelar un pago que CONSUMIÓ el anticipo nativo de OTRO pago está
  bloqueado: se pide revertirlo a mano en ERPNext.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate

from pagos_chappsa.saldos_proveedor import TIPO_ANTICIPO, TOLERANCIA

PREFIJO_ANTICIPO = "AnticipoProveedor::"


def crear_custom_fields():
	"""Campos de rastreo en Payment Entry: qué Pago Proveedor/forma de pago lo generó.

	Se ejecuta en cada `bench migrate` (after_migrate); create_custom_fields ya
	es idempotente. Reutiliza `custom_pagos_chappsa_forma_idx` y
	`custom_pagos_chappsa_documento`, que ya existen para el lado de cobranza y
	son genéricos (un índice y una clave de texto libre); solo hace falta un
	Link propio, porque un Link no puede apuntar a la vez a "Pago Cliente" y a
	"Pago Proveedor".
	"""
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	create_custom_fields(
		{
			"Payment Entry": [
				{
					"fieldname": "custom_pagos_chappsa_pago_proveedor",
					"fieldtype": "Link",
					"options": "Pago Proveedor",
					"label": "Pago Proveedor (pagos_chappsa)",
					"read_only": 1,
					"no_copy": 1,
					"print_hide": 1,
					"insert_after": "remarks",
				},
			]
		},
		update=True,
	)


def _ya_aplicado(pago_proveedor, forma_idx, clave_documento):
	return bool(
		frappe.db.exists(
			"Payment Entry",
			{
				"custom_pagos_chappsa_pago_proveedor": pago_proveedor,
				"custom_pagos_chappsa_forma_idx": forma_idx,
				"custom_pagos_chappsa_documento": clave_documento,
				"docstatus": ("!=", 2),
			},
		)
	)


TIPO_A_CAMPO_CUENTAS = {
	"Efectivo": "cuentas_efectivo",
	"Transferencia": "cuentas_transferencia",
	"Cheque": "cuentas_cheque",
	"Retención": "cuentas_retencion",
	"Tarjeta de Crédito": "cuentas_tarjeta",
}


def get_cuentas_configuradas(tipo):
	"""Cuentas contables que el perfil Global de Proveedores definió para esta forma de pago."""
	campo = TIPO_A_CAMPO_CUENTAS.get(tipo)
	if not campo:
		return []

	nombre_global = frappe.db.get_value("Perfil de Pago Proveedor", {"es_global": 1}, "name")
	if not nombre_global:
		return []

	perfil = frappe.get_cached_doc("Perfil de Pago Proveedor", nombre_global)
	return [f.cuenta for f in perfil.get(campo) or [] if f.cuenta]


def get_cuentas_anticipo_configuradas():
	"""Cuenta(s) de mayor donde se registra el saldo a favor de la empresa (anticipo
	entregado al proveedor), configuradas en el perfil Global. Lista vacía = usar
	la cuenta por pagar normal."""
	nombre_global = frappe.db.get_value("Perfil de Pago Proveedor", {"es_global": 1}, "name")
	if not nombre_global:
		return []
	perfil = frappe.get_cached_doc("Perfil de Pago Proveedor", nombre_global)
	return [f.cuenta for f in perfil.get("cuentas_anticipo") or [] if f.cuenta]


def get_cuenta_gastos_bancarios():
	nombre_global = frappe.db.get_value("Perfil de Pago Proveedor", {"es_global": 1}, "name")
	if not nombre_global:
		return None
	return frappe.get_cached_value("Perfil de Pago Proveedor", nombre_global, "cuenta_gastos_bancarios")


def get_cuentas_diferencial_cambiario():
	"""(cuenta_ganancia, cuenta_perdida) del perfil Global, o (None, None) sin perfil."""
	nombre_global = frappe.db.get_value("Perfil de Pago Proveedor", {"es_global": 1}, "name")
	if not nombre_global:
		return None, None
	perfil = frappe.get_cached_doc("Perfil de Pago Proveedor", nombre_global)
	return perfil.get("cuenta_diferencial_ganancia"), perfil.get("cuenta_diferencial_perdida")


def _resolver_cuenta_anticipo(pago_proveedor, cuenta_por_defecto):
	"""Cuenta a usar como lado "proveedor" de un Payment Entry de anticipo.

	Orden: cuenta explícita en el propio Pago Proveedor (elegida por el usuario o
	autocompletada) > única cuenta configurada en el perfil Global > cuenta por
	pagar normal del proveedor (comportamiento de siempre)."""
	if pago_proveedor.get("cuenta_anticipo"):
		return pago_proveedor.cuenta_anticipo

	configuradas = get_cuentas_anticipo_configuradas()
	if len(configuradas) == 1:
		return configuradas[0]
	if len(configuradas) > 1:
		frappe.throw(
			_(
				"Hay más de una Cuenta de anticipo configurada en el perfil Global de Pagos a "
				"Proveedores: indique cuál usar en el campo «Cuenta de anticipo» de este pago."
			)
		)
	return cuenta_por_defecto


def _aplicar_gasto_bancario(pe, gasto_bancario):
	"""Registra la porción de una forma de pago que en realidad es una comisión o
	gasto bancario, usando la tabla nativa "Deductions" del Payment Entry, sin que
	afecte lo aplicado a la factura o anticipo del proveedor.

	Pay (proveedor): el banco sale por el monto BRUTO; solo el neto llega al
	proveedor. Se aumenta `paid_amount` (la pata de banco) por el gasto.
	"""
	gasto_bancario = flt(gasto_bancario, 2)
	if gasto_bancario <= 0:
		return

	cuenta = get_cuenta_gastos_bancarios()
	if not cuenta:
		frappe.throw(
			_(
				"Esta forma de pago indica un gasto bancario de {0}, pero no hay una Cuenta de gastos "
				"bancarios configurada en el perfil Global de Pagos a Proveedores."
			).format(gasto_bancario)
		)

	tasa = pe.source_exchange_rate if pe.payment_type == "Pay" else pe.target_exchange_rate
	monto_base = flt(gasto_bancario * flt(tasa or 1), 2)

	if pe.payment_type == "Pay":
		pe.paid_amount = flt(flt(pe.paid_amount) + gasto_bancario, 2)
	else:
		pe.received_amount = flt(flt(pe.received_amount) - gasto_bancario, 2)

	cost_center = frappe.get_cached_value("Company", pe.company, "cost_center")
	if not cost_center:
		frappe.throw(
			_(
				"No se pudo registrar el gasto bancario: la empresa {0} no tiene un Centro de Costo por "
				"defecto configurado (obligatorio para este tipo de asiento en ERPNext). Configúrelo en "
				"el documento de la Compañía."
			).format(frappe.bold(pe.company))
		)
	pe.append("deductions", {"account": cuenta, "cost_center": cost_center, "amount": monto_base})


def _validar_gasto_bancario_aplicable(forma, monto_usado):
	"""Ver `pagos_chappsa.integracion_nativa._validar_gasto_bancario_aplicable`:
	mismo criterio (no repartir un gasto bancario entre varios Payment Entry)."""
	if flt(forma.gasto_bancario) <= 0:
		return
	if abs(flt(monto_usado, 2) - flt(forma.monto, 2)) > TOLERANCIA:
		frappe.throw(
			_(
				"La forma de pago {0} indica un gasto bancario, pero su monto se está repartiendo entre "
				"varios documentos o entre documento y anticipo: no hay forma confiable de saber a cuál "
				"cargar el gasto. Registre el pago nativo manualmente en ERPNext, o separe esta forma de "
				"pago en líneas independientes."
			).format(forma.id_linea)
		)


def _fijar_tipo_cambio_real(pe, pago_proveedor):
	"""Reemplaza, ANTES de aplicar el gasto bancario, la(s) tasa(s) "de la
	factura" que `get_payment_entry` propuso por la tasa REAL capturada en el
	pago (Pago Proveedor.tipo_cambio). Hay dos casos, según cómo esté armada
	la cuenta por pagar del proveedor:

	1. "nativo" — cuenta por pagar en la MISMA moneda extranjera que el
	   documento y el banco (p. ej. "PROVEEDORES DEL EXTERIOR" en USD): se
	   fijan AMBAS tasas (source y target) a la real. El diferencial que eso
	   genera lo calcula y contabiliza ERPNext por su cuenta, con su mecanismo
	   nativo de ganancia/pérdida cambiaria por referencia (columna
	   `exchange_gain_loss` de Payment Entry Reference), que asienta un
	   Journal Entry aparte contra `Company.exchange_gain_loss_account` al
	   validar. No hace falta ningún paso extra de este lado.

	2. "manual" — cuenta por pagar en la moneda de la EMPRESA (el caso que
	   atiende `_monto_para_cuenta_por_pagar`): solo se fija la tasa del lado
	   banco (source, para Pay). Aquí SÍ hace falta el paso extra de
	   `_registrar_diferencial_cambiario`, después del gasto bancario, porque
	   la cuenta por pagar ya está en moneda de la empresa y ERPNext no tiene
	   una tasa "de factura" que comparar de ese lado — no genera nada solo.

	Devuelve "nativo", "manual" o None si no aplicó ninguno de los dos casos.
	"""
	tipo_cambio = flt(pago_proveedor.get("tipo_cambio"))
	if tipo_cambio <= 0:
		return None

	if pe.paid_from_account_currency == pe.paid_to_account_currency:
		if pe.paid_from_account_currency != pago_proveedor.moneda:
			return None  # ambas cuentas ya en moneda de la empresa: nada que ajustar
		pe.source_exchange_rate = tipo_cambio
		pe.target_exchange_rate = tipo_cambio
		return "nativo"

	if pe.paid_to_account_currency != pe.company_currency:
		return None
	if pe.paid_from_account_currency != pago_proveedor.moneda:
		return None

	pe.source_exchange_rate = tipo_cambio
	return "manual"


def _registrar_diferencial_cambiario(pe, pago_proveedor, aplica):
	"""Con la tasa real ya fijada (`_fijar_tipo_cambio_real`, caso "manual") y
	el gasto bancario ya aplicado, recalcula los montos y registra la
	diferencia resultante entre esa tasa y la de la factura como ganancia o
	pérdida cambiaria. El caso "nativo" no llama a esta función: ERPNext ya lo
	contabiliza por su cuenta (ver `_fijar_tipo_cambio_real`)."""
	if not aplica:
		return

	pe.set_amounts()
	diferencia = flt(pe.difference_amount, 2)
	if abs(diferencia) <= TOLERANCIA:
		return

	cuenta_ganancia, cuenta_perdida = get_cuentas_diferencial_cambiario()
	es_perdida = diferencia > 0
	cuenta = cuenta_perdida if es_perdida else cuenta_ganancia
	if not cuenta:
		frappe.throw(
			_(
				"Este pago genera un diferencial cambiario de {0} (tipo de cambio {1} contra el usado en "
				"el documento), pero no hay una Cuenta de {2} cambiaria configurada en el perfil Global de "
				"Pagos a Proveedores."
			).format(abs(diferencia), flt(pago_proveedor.tipo_cambio), _("pérdida") if es_perdida else _("ganancia"))
		)

	cost_center = frappe.get_cached_value("Company", pe.company, "cost_center")
	if not cost_center:
		frappe.throw(
			_(
				"No se pudo registrar el diferencial cambiario: la empresa {0} no tiene un Centro de Costo "
				"por defecto configurado (obligatorio para este tipo de asiento en ERPNext). Configúrelo en "
				"el documento de la Compañía."
			).format(frappe.bold(pe.company))
		)

	pe.append(
		"deductions",
		{
			"account": cuenta,
			"cost_center": cost_center,
			"amount": diferencia,
			"description": _("Diferencial cambiario (tipo de cambio real {0}).").format(pago_proveedor.tipo_cambio),
		},
	)


def _resolver_cuenta(empresa, forma):
	"""Cuenta contable a usar como banco/caja del Payment Entry.

	Mismo orden de prioridad que en cobranza: cuenta explícita en la fila > cuenta
	ligada al Bank Account > única cuenta configurada en el perfil Global > cuenta
	por defecto del Modo de Pago.
	"""
	if forma.cuenta_contable:
		return forma.cuenta_contable

	if forma.cuenta_banco:
		cuenta = frappe.db.get_value("Bank Account", forma.cuenta_banco, "account")
		if cuenta:
			return cuenta

	configuradas = get_cuentas_configuradas(forma.tipo)
	if len(configuradas) == 1:
		return configuradas[0]

	from erpnext.accounts.doctype.journal_entry.journal_entry import get_default_bank_cash_account

	tipo_cuenta = "Cash" if forma.tipo == "Efectivo" else "Bank"
	cuenta = get_default_bank_cash_account(empresa, tipo_cuenta, mode_of_payment=forma.forma_pago)
	if cuenta:
		return cuenta.account

	frappe.throw(
		_(
			"No se pudo determinar la cuenta contable para aplicar el pago nativo de la forma de pago "
			"{0} (fila {1}). Indique la cuenta de banco o la cuenta contable en el detalle de formas de "
			"pago, o configure una cuenta por defecto para esa forma en {2}."
		).format(forma.forma_pago or forma.tipo, forma.id_linea, frappe.bold(empresa))
	)


def _referencia_unica(base):
	"""Ver `pagos_chappsa.integracion_nativa._referencia_unica`: mismo motivo
	(reference_no único y obligatorio en este sitio)."""
	base = (base or "").strip() or "S/R"
	candidato = base
	sufijo = 1
	while frappe.db.exists("Payment Entry", {"reference_no": candidato}):
		sufijo += 1
		candidato = f"{base}-{sufijo}"
	return candidato


def _monto_para_cuenta_por_pagar(documento, monto):
	"""`monto` viene en la moneda del documento (pagos_chappsa exige que coincidan).
	`get_payment_entry` espera `party_amount` en la moneda de la CUENTA por pagar
	de la factura, que puede ser distinta. Se convierte con la MISMA tasa de la
	factura, para no inventar un diferencial cambiario que nadie pidió."""
	factura = frappe.db.get_value(
		"Purchase Invoice", documento, ["currency", "conversion_rate", "party_account_currency"], as_dict=True
	)
	if factura and factura.party_account_currency and factura.party_account_currency != factura.currency:
		return flt(flt(monto, 2) * flt(factura.conversion_rate or 1), 2)
	return flt(monto, 2)


def _crear_payment_entry(pago_proveedor, forma, tipo_documento, documento, monto):
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	if tipo_documento != "Purchase Invoice":
		frappe.throw(
			_("Aplicación de pago nativo no soportada para el tipo de documento {0}.").format(tipo_documento)
		)

	_validar_gasto_bancario_aplicable(forma, monto)
	cuenta = _resolver_cuenta(pago_proveedor.empresa, forma)
	fecha_referencia = getdate(forma.fecha or pago_proveedor.fecha)

	gasto_bancario = flt(forma.gasto_bancario, 2)
	pe = get_payment_entry(
		"Purchase Invoice",
		documento,
		party_amount=_monto_para_cuenta_por_pagar(documento, flt(monto - gasto_bancario, 2)),
		bank_account=cuenta,
		reference_date=fecha_referencia,
	)
	pe.posting_date = getdate(pago_proveedor.fecha)
	pe.mode_of_payment = forma.forma_pago or None
	pe.reference_no = _referencia_unica(forma.no_documento or pago_proveedor.no_recibo_manual)
	pe.reference_date = fecha_referencia
	pe.remarks = _("Generado automáticamente desde Pago Proveedor {0} (comprobante {1}).").format(
		pago_proveedor.name, pago_proveedor.no_recibo_manual
	)
	pe.custom_pagos_chappsa_pago_proveedor = pago_proveedor.name
	pe.custom_pagos_chappsa_forma_idx = forma.id_linea
	pe.custom_pagos_chappsa_documento = f"{tipo_documento}::{documento}"

	resultado_tipo_cambio = _fijar_tipo_cambio_real(pe, pago_proveedor)
	_aplicar_gasto_bancario(pe, gasto_bancario)
	_registrar_diferencial_cambiario(pe, pago_proveedor, resultado_tipo_cambio == "manual")

	pe.flags.ignore_permissions = True
	pe.insert(ignore_permissions=True)
	pe.submit()
	return pe


def _crear_anticipo_nativo(pago_proveedor, forma, monto):
	"""Registra dinero entregado que no se aplicó a ningún documento como un
	Payment Entry "Pay" sin referencia (anticipo real en ERPNext).

	Si la cuenta por pagar del proveedor está en una moneda distinta a la del
	pago, se usa el tipo de cambio real capturado en el pago (Pago
	Proveedor.tipo_cambio) para convertir: al no existir todavía una factura de
	la cual tomar prestada una tasa, esa conversión no genera ganancia ni
	pérdida en este momento; el diferencial, si lo hay, aparecerá cuando este
	anticipo se consuma contra una factura real con su propia tasa.

	Devuelve None (sin crear nada) solo si esa cuenta está en una TERCERA
	moneda que no es ni la del pago ni la de la empresa, o si no hay tipo de
	cambio capturado.
	"""
	from erpnext.accounts.party import get_party_account

	_validar_gasto_bancario_aplicable(forma, monto)

	cuenta_proveedor_defecto = get_party_account("Supplier", pago_proveedor.proveedor, pago_proveedor.empresa)
	cuenta_proveedor = _resolver_cuenta_anticipo(pago_proveedor, cuenta_proveedor_defecto)
	moneda_cuenta_proveedor = frappe.get_cached_value("Account", cuenta_proveedor, "account_currency")

	cuenta = _resolver_cuenta(pago_proveedor.empresa, forma)
	fecha_referencia = getdate(forma.fecha or pago_proveedor.fecha)
	gasto_bancario = flt(forma.gasto_bancario, 2)
	monto_neto = flt(monto - gasto_bancario, 2)

	tipo_cambio = flt(pago_proveedor.get("tipo_cambio"))
	monto_cuenta_proveedor = monto_neto
	convertir = bool(moneda_cuenta_proveedor) and moneda_cuenta_proveedor != pago_proveedor.moneda
	# Banco y cuenta de anticipo YA en la misma moneda extranjera: se usa la
	# tasa real capturada en vez de la que ERPNext adivine solo (get_exchange_rate
	# del día), para no depender de que haya un tipo de cambio configurado ahí.
	mismo_extranjero = (
		bool(moneda_cuenta_proveedor) and moneda_cuenta_proveedor == pago_proveedor.moneda and tipo_cambio > 0
	)
	if convertir:
		moneda_empresa = frappe.get_cached_value("Company", pago_proveedor.empresa, "default_currency")
		if moneda_cuenta_proveedor != moneda_empresa or tipo_cambio <= 0:
			return None
		monto_cuenta_proveedor = flt(monto_neto * tipo_cambio, 2)

	pe = frappe.new_doc("Payment Entry")
	pe.payment_type = "Pay"
	pe.party_type = "Supplier"
	pe.party = pago_proveedor.proveedor
	pe.company = pago_proveedor.empresa
	pe.posting_date = getdate(pago_proveedor.fecha)
	pe.paid_from = cuenta
	pe.paid_to = cuenta_proveedor
	pe.paid_amount = monto_neto
	pe.received_amount = monto_cuenta_proveedor
	if convertir:
		pe.source_exchange_rate = tipo_cambio
		pe.target_exchange_rate = 1
	elif mismo_extranjero:
		pe.source_exchange_rate = tipo_cambio
		pe.target_exchange_rate = tipo_cambio
	pe.mode_of_payment = forma.forma_pago or None
	pe.reference_no = _referencia_unica(forma.no_documento or pago_proveedor.no_recibo_manual)
	pe.reference_date = fecha_referencia
	pe.remarks = _(
		"Anticipo generado desde Pago Proveedor {0} (comprobante {1}), sin aplicar a ningún documento aún."
	).format(pago_proveedor.name, pago_proveedor.no_recibo_manual)
	pe.custom_pagos_chappsa_pago_proveedor = pago_proveedor.name
	pe.custom_pagos_chappsa_forma_idx = forma.id_linea
	pe.custom_pagos_chappsa_documento = f"{PREFIJO_ANTICIPO}{pago_proveedor.name}"

	_aplicar_gasto_bancario(pe, gasto_bancario)

	pe.flags.ignore_permissions = True
	pe.insert(ignore_permissions=True)
	pe.submit()
	return pe.name


def _consumir_anticipo(documento_origen, monto, tipo_documento_destino, documento_destino):
	"""Reasigna hasta `monto` de los anticipos nativos abiertos de `documento_origen`
	(el Pago Proveedor que los generó) contra `documento_destino`."""
	if tipo_documento_destino != "Purchase Invoice":
		frappe.throw(
			_("No se puede aplicar un anticipo nativo contra un documento que no sea una Factura de Compra.")
		)

	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	clave_anticipo = f"{PREFIJO_ANTICIPO}{documento_origen}"
	avances = frappe.get_all(
		"Payment Entry",
		filters={"custom_pagos_chappsa_documento": clave_anticipo, "docstatus": 1},
		pluck="name",
		order_by="creation asc",
	)

	restante = flt(monto, 2)
	creados = []

	for nombre in avances:
		if restante <= 0:
			break

		avance = frappe.get_doc("Payment Entry", nombre)
		disponible = flt(avance.unallocated_amount, 2)
		if disponible <= 0:
			continue

		usar = min(disponible, restante)
		datos = frappe._dict(
			party=avance.party,
			company=avance.company,
			posting_date=avance.posting_date,
			paid_from=avance.paid_from,
			paid_to=avance.paid_to,
			mode_of_payment=avance.mode_of_payment,
			reference_no=avance.reference_no,
			reference_date=avance.reference_date,
			pago_origen=avance.custom_pagos_chappsa_pago_proveedor,
			forma_idx=avance.custom_pagos_chappsa_forma_idx,
		)

		avance.flags.ignore_permissions = True
		avance.cancel()

		nuevo = get_payment_entry(
			"Purchase Invoice",
			documento_destino,
			party_amount=usar,
			bank_account=datos.paid_from,
			reference_date=datos.reference_date,
		)
		nuevo.posting_date = datos.posting_date
		nuevo.mode_of_payment = datos.mode_of_payment
		nuevo.reference_no = _referencia_unica(datos.reference_no)
		nuevo.reference_date = datos.reference_date
		nuevo.remarks = _("Anticipo de {0} aplicado a {1}.").format(datos.pago_origen, documento_destino)
		nuevo.custom_pagos_chappsa_pago_proveedor = datos.pago_origen
		nuevo.custom_pagos_chappsa_forma_idx = datos.forma_idx
		nuevo.custom_pagos_chappsa_documento = f"Purchase Invoice::{documento_destino}"
		nuevo.flags.ignore_permissions = True
		nuevo.insert(ignore_permissions=True)
		nuevo.submit()
		creados.append(nuevo.name)

		restante_avance = flt(disponible - usar, 2)
		if restante_avance > 0:
			remanente = frappe.new_doc("Payment Entry")
			remanente.payment_type = "Pay"
			remanente.party_type = "Supplier"
			remanente.party = datos.party
			remanente.company = datos.company
			remanente.posting_date = datos.posting_date
			remanente.paid_from = datos.paid_from
			remanente.paid_to = datos.paid_to
			remanente.paid_amount = restante_avance
			remanente.received_amount = restante_avance
			remanente.mode_of_payment = datos.mode_of_payment
			remanente.reference_no = _referencia_unica(datos.reference_no)
			remanente.reference_date = datos.reference_date
			remanente.remarks = _("Remanente del anticipo de {0} tras aplicar {1} a {2}.").format(
				datos.pago_origen, usar, documento_destino
			)
			remanente.custom_pagos_chappsa_pago_proveedor = datos.pago_origen
			remanente.custom_pagos_chappsa_forma_idx = datos.forma_idx
			remanente.custom_pagos_chappsa_documento = clave_anticipo
			remanente.flags.ignore_permissions = True
			remanente.insert(ignore_permissions=True)
			remanente.submit()

		restante = flt(restante - usar, 2)

	if restante > 0.005:
		if not avances:
			frappe.throw(
				_(
					"El pago {0} dejó un anticipo, pero nunca se aplicó como Payment Entry nativo en "
					"ERPNext (posiblemente porque estaba en una moneda distinta a la de la cuenta por "
					"pagar del proveedor). Aplique este crédito manualmente en ERPNext."
				).format(documento_origen)
			)
		frappe.throw(
			_(
				"El anticipo nativo de {0} disponible en ERPNext no alcanza para cubrir {1}; faltan {2}. "
				"Revise los Payment Entry marcados como anticipo de ese pago."
			).format(documento_origen, flt(monto, 2), restante)
		)

	return creados


def aplicar_pagos_nativos(pago_proveedor):
	"""Aplica en ERPNext, como Payment Entry(s) nativo(s), los abonos de `pago_proveedor`.

	Más simple que el equivalente de cobranza porque no hay camino retroactivo:
	el único documento que se paga es la Factura de Compra, así que siempre se
	procesan las filas del propio pago, los créditos de anticipo que aplique, y
	cualquier excedente nuevo se registra como anticipo nativo.
	"""
	filas = [
		f for f in pago_proveedor.detalle_documentos if f.tipo_documento == "Purchase Invoice" and flt(f.abono) > 0
	]
	creditos = [
		f for f in pago_proveedor.detalle_documentos if f.tipo_documento == TIPO_ANTICIPO and flt(f.abono) < 0
	]

	formas = list(pago_proveedor.detalle_formas_pago)
	excedente_nuevo = flt(pago_proveedor.excedente, 2)

	if not filas and not creditos and excedente_nuevo <= 0:
		return []

	if len(filas) > 1 and (len(formas) > 1 or len(creditos) > 1):
		frappe.throw(
			_(
				"Este pago aplica a {0} documentos con {1} formas de pago y {2} créditos de anticipo: la "
				"aplicación automática del pago nativo no soporta esa combinación (no hay forma de saber "
				"qué banco o anticipo pagó cuál documento). Registre el pago nativo manualmente en "
				"ERPNext, o divida este comprobante en pagos separados por documento."
			).format(len(filas), len(formas), len(creditos))
		)

	if creditos and len(filas) != 1:
		frappe.throw(
			_("No se puede aplicar un crédito de anticipo sin una única factura de destino en este pago.")
		)

	creados = []
	restante_por_forma = {f.id_linea: flt(f.monto, 2) for f in formas}

	if len(filas) == 1:
		fila = filas[0]
		clave_documento = f"{fila.tipo_documento}::{fila.documento}"
		restante_factura = flt(fila.abono, 2)

		for credito in creditos:
			if restante_factura <= 0:
				break
			usar = min(abs(flt(credito.abono, 2)), restante_factura)
			if usar <= 0:
				continue
			creados.extend(_consumir_anticipo(credito.documento, usar, fila.tipo_documento, fila.documento))
			restante_factura = flt(restante_factura - usar, 2)

		for forma in formas:
			if restante_factura <= 0:
				break
			if _ya_aplicado(pago_proveedor.name, forma.id_linea, clave_documento):
				continue
			# El gasto bancario de la forma se reserva aparte: lo que esta forma
			# necesita aportar para cubrir el resto de la factura es el saldo más
			# el gasto (que no abona nada a la factura, solo viaja con el banco).
			gasto_forma = flt(forma.gasto_bancario, 2)
			necesario = flt(restante_factura + gasto_forma, 2)
			monto = min(restante_por_forma[forma.id_linea], necesario)
			if monto <= 0:
				continue
			pe = _crear_payment_entry(pago_proveedor, forma, fila.tipo_documento, fila.documento, monto)
			creados.append(pe.name)
			restante_por_forma[forma.id_linea] = flt(restante_por_forma[forma.id_linea] - monto, 2)
			restante_factura = flt(restante_factura - flt(monto - gasto_forma, 2), 2)

	elif filas:
		# N facturas, 1 forma de pago: se reparte proporcional al abono de cada una,
		# sin exceder lo que las facturas realmente necesitan (`total_base`). Si la
		# forma de pago trae más dinero que eso (porque en la misma línea también
		# viene el excedente), el resto NO se reparte de más entre las facturas: se
		# deja en `restante_por_forma` para que el paso de excedente, más abajo, lo
		# registre como anticipo.
		forma = formas[0]
		total_base = flt(sum(flt(f.abono) for f in filas), 2)
		bolsa = min(restante_por_forma[forma.id_linea], total_base)
		for fila in filas:
			clave_documento = f"{fila.tipo_documento}::{fila.documento}"
			if _ya_aplicado(pago_proveedor.name, forma.id_linea, clave_documento):
				continue
			peso = flt(fila.abono, 2) / total_base if total_base else 0
			monto = min(flt(bolsa * peso, 2), restante_por_forma[forma.id_linea])
			if monto <= 0:
				continue
			pe = _crear_payment_entry(pago_proveedor, forma, fila.tipo_documento, fila.documento, monto)
			creados.append(pe.name)
			restante_por_forma[forma.id_linea] = flt(restante_por_forma[forma.id_linea] - monto, 2)

	if excedente_nuevo > 0:
		clave_anticipo = f"{PREFIJO_ANTICIPO}{pago_proveedor.name}"
		anticipo_omitido = False
		for forma in formas:
			sobra = restante_por_forma.get(forma.id_linea, 0)
			if sobra <= 0:
				continue
			if _ya_aplicado(pago_proveedor.name, forma.id_linea, clave_anticipo):
				continue
			resultado = _crear_anticipo_nativo(pago_proveedor, forma, sobra)
			if resultado:
				creados.append(resultado)
			else:
				anticipo_omitido = True

		if anticipo_omitido:
			frappe.msgprint(
				_(
					"El excedente de este pago quedó como anticipo en pagos_chappsa, pero NO se aplicó "
					"como Payment Entry nativo en ERPNext: está en una moneda distinta a la de la cuenta "
					"por pagar del proveedor y no hay una tasa de cambio de referencia clara para usar. "
					"Regístrelo manualmente en ERPNext si corresponde."
				),
				title=_("Anticipo no aplicado en ERPNext"),
				indicator="orange",
			)

	return creados


def cancelar_pago_proveedor_si_corresponde(doc, method=None):
	"""Hook `on_cancel` de Payment Entry: si la que se está cancelando (desde
	contabilidad, en ERPNext) es una que generó Pagos a Proveedores, el Pago
	Proveedor que la originó se cancela también. Ver
	`pagos_chappsa.integracion_nativa.cancelar_pago_cliente_si_corresponde`
	para el mismo mecanismo del lado de cobranza.
	"""
	nombre = doc.get("custom_pagos_chappsa_pago_proveedor")
	if not nombre:
		return

	pago_proveedor = frappe.get_doc("Pago Proveedor", nombre)
	if pago_proveedor.docstatus != 1:
		return

	pago_proveedor.flags.ignore_permissions = True
	pago_proveedor.cancel()
	frappe.msgprint(
		_("El Pago Proveedor {0} se canceló automáticamente junto con esta Payment Entry.").format(
			frappe.bold(pago_proveedor.name)
		),
		alert=True,
		indicator="orange",
	)


def revertir_pagos_nativos(pago_proveedor):
	"""Cancela todos los Payment Entry nativos atados a `pago_proveedor`."""
	nombres = frappe.get_all(
		"Payment Entry",
		filters={"custom_pagos_chappsa_pago_proveedor": pago_proveedor.name, "docstatus": 1},
		pluck="name",
	)
	for nombre in nombres:
		pe = frappe.get_doc("Payment Entry", nombre)
		pe.flags.ignore_permissions = True
		pe.cancel()
