# Copyright (c) 2026, Josue Velasquez and contributors
# For license information, please see license.txt
"""Puente quirúrgico entre pagos_chappsa y la contabilidad nativa de ERPNext.

El Origen del perfil de cobranza marcado como Global (ver
`pagos_chappsa.saldos.get_origen_global`) decide cuál de los dos modos aplica.
Sin un perfil Global configurado (`get_origen_global()` devuelve None) ninguno
de los dos modos se activa: nada de lo de aquí toca facturas ni pagos nativos
hasta que alguien lo prenda explícitamente.

* Nota de Entrega (modo actual de esta instalación): la Nota de Entrega sigue
  siendo el documento que se cobra en pagos_chappsa. Cuando por fin se factura,
  esta capa exige que la factura sea 1 a 1 contra la nota (sin combinar varias
  notas ni facturar parcial) y, al validarla, aplica retroactivamente como
  Payment Entry nativo cada pago que pagos_chappsa ya cobró contra esa nota.

* Factura de Venta: el documento que se cobra en pagos_chappsa ya es la propia
  factura, así que cada Pago Cliente validado aplica de inmediato su Payment
  Entry nativo.

En ambos casos el Payment Entry se arma con `get_payment_entry`, el mismo
helper que usa el núcleo de ERPNext, así hereda cuentas, moneda y validaciones
nativas; esta capa solo decide fecha, monto, banco y forma de pago a partir del
detalle capturado en el Pago Cliente.

Anticipos: cuando un Pago Cliente deja excedente (dinero recibido que no se
aplicó a ningún documento), ese excedente se registra como un Payment Entry
nativo "Receive" SIN referencia (un anticipo/avance real en ERPNext, con
`unallocated_amount`). Cuando un pago posterior consume ese anticipo como
crédito contra una factura, el anticipo original se CANCELA y se reemplaza por
(a) un Payment Entry que aplica lo consumido contra esa factura y, si sobra
algo, (b) un nuevo anticipo por el remanente — así nunca hay que editar en
sitio un Payment Entry ya validado, y el rastro completo queda en el historial
de documentos cancelados/reemplazados.

Límites deliberados:

* Si un Pago Cliente reparte su abono entre más de un documento Y usa más de
  una forma de pago (o crédito de anticipo) a la vez, no hay forma confiable
  de saber qué banco pagó qué documento. Se exige registrar el pago nativo a
  mano en ERPNext en vez de adivinar (ver `aplicar_pagos_nativos`).

* Cancelar un pago que CONSUMIÓ el anticipo nativo de OTRO pago está
  bloqueado (`Pago Cliente.validar_no_revierte_consumo_anticipo_nativo`):
  reconstruir ese cruce en reversa de forma automática es demasiado frágil
  para algo tan poco frecuente. Se pide revertirlo a mano en ERPNext.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate

from pagos_chappsa.saldos import TIPO_ANTICIPO, TOLERANCIA, get_origen_global

PREFIJO_ANTICIPO = "Anticipo::"


def crear_custom_fields():
	"""Campos de rastreo en Payment Entry: qué Pago Cliente/forma de pago lo generó.

	Se ejecuta en cada `bench migrate` (after_migrate); create_custom_fields ya
	es idempotente, así que también sirve para instalaciones nuevas de la app.
	"""
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	create_custom_fields(
		{
			"Payment Entry": [
				{
					"fieldname": "custom_pagos_chappsa_pago_cliente",
					"fieldtype": "Link",
					"options": "Pago Cliente",
					"label": "Pago Cliente (pagos_chappsa)",
					"read_only": 1,
					"no_copy": 1,
					"print_hide": 1,
					"insert_after": "remarks",
				},
				{
					"fieldname": "custom_pagos_chappsa_forma_idx",
					"fieldtype": "Int",
					"label": "Fila de forma de pago (pagos_chappsa)",
					"read_only": 1,
					"no_copy": 1,
					"print_hide": 1,
					"insert_after": "custom_pagos_chappsa_pago_cliente",
				},
				{
					"fieldname": "custom_pagos_chappsa_documento",
					"fieldtype": "Data",
					"label": "Documento cobrado (pagos_chappsa)",
					"read_only": 1,
					"no_copy": 1,
					"print_hide": 1,
					"insert_after": "custom_pagos_chappsa_forma_idx",
				},
			]
		},
		update=True,
	)


def _ya_aplicado(pago_cliente, forma_idx, clave_documento):
	return bool(
		frappe.db.exists(
			"Payment Entry",
			{
				"custom_pagos_chappsa_pago_cliente": pago_cliente,
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
	"""Cuentas contables que el perfil Global definió para esta forma de pago.

	Lista vacía = nadie ha configurado nada todavía para ese tipo (o no hay
	perfil Global): no es una restricción, es "sin opinión".
	"""
	campo = TIPO_A_CAMPO_CUENTAS.get(tipo)
	if not campo:
		return []

	nombre_global = frappe.db.get_value("Perfil de Cobranza", {"es_global": 1}, "name")
	if not nombre_global:
		return []

	perfil = frappe.get_cached_doc("Perfil de Cobranza", nombre_global)
	return [f.cuenta for f in perfil.get(campo) or [] if f.cuenta]


def get_cuentas_anticipo_configuradas():
	"""Cuenta(s) de mayor donde se registra el saldo a favor del cliente (anticipo),
	configuradas en el perfil Global. Lista vacía = usar la cuenta por cobrar normal."""
	nombre_global = frappe.db.get_value("Perfil de Cobranza", {"es_global": 1}, "name")
	if not nombre_global:
		return []
	perfil = frappe.get_cached_doc("Perfil de Cobranza", nombre_global)
	return [f.cuenta for f in perfil.get("cuentas_anticipo") or [] if f.cuenta]


def get_cuenta_gastos_bancarios():
	nombre_global = frappe.db.get_value("Perfil de Cobranza", {"es_global": 1}, "name")
	if not nombre_global:
		return None
	return frappe.get_cached_value("Perfil de Cobranza", nombre_global, "cuenta_gastos_bancarios")


def get_cuentas_diferencial_cambiario():
	"""(cuenta_ganancia, cuenta_perdida) del perfil Global, o (None, None) sin perfil."""
	nombre_global = frappe.db.get_value("Perfil de Cobranza", {"es_global": 1}, "name")
	if not nombre_global:
		return None, None
	perfil = frappe.get_cached_doc("Perfil de Cobranza", nombre_global)
	return perfil.get("cuenta_diferencial_ganancia"), perfil.get("cuenta_diferencial_perdida")


def _resolver_cuenta_anticipo(pago_cliente, cuenta_por_defecto):
	"""Cuenta a usar como lado "cliente" de un Payment Entry de anticipo.

	Orden: cuenta explícita en el propio Pago Cliente (elegida por el cobrador o
	autocompletada) > única cuenta configurada en el perfil Global > cuenta por
	cobrar normal del cliente (comportamiento de siempre)."""
	if pago_cliente.get("cuenta_anticipo"):
		return pago_cliente.cuenta_anticipo

	configuradas = get_cuentas_anticipo_configuradas()
	if len(configuradas) == 1:
		return configuradas[0]
	if len(configuradas) > 1:
		frappe.throw(
			_(
				"Hay más de una Cuenta de anticipo configurada en el perfil Global de Cobranza: "
				"indique cuál usar en el campo «Cuenta de anticipo» de este pago."
			)
		)
	return cuenta_por_defecto


def _aplicar_gasto_bancario(pe, gasto_bancario):
	"""Registra la porción de una forma de pago que en realidad es una comisión o
	gasto bancario, usando la tabla nativa "Deductions" del Payment Entry.

	Limitado a Payment Entry tipo "Pay" (proveedores) a propósito: en un "Receive"
	con banco y cuenta por cobrar en la misma moneda, ERPNext sincroniza
	`received_amount` = `paid_amount` sin importar lo que se le asigne aquí (ver
	`PaymentEntry.set_received_amount`), así que reducirlo no tiene efecto — y
	forzar el cuadre a través de "deductions" en ese sentido termina generando un
	`unallocated_amount` (anticipo) ficticio en vez de un gasto limpio. Hasta que
	se implemente un mecanismo aparte para el lado "Receive" (una entrada
	independiente que mueva el banco a Gastos Bancarios), esta función solo
	aplica al lado "Pay".
	"""
	gasto_bancario = flt(gasto_bancario, 2)
	if gasto_bancario <= 0:
		return

	if pe.payment_type != "Pay":
		frappe.throw(
			_(
				"El gasto bancario en formas de pago de cobranza (Receive) todavía no está soportado: "
				"ERPNext no permite reducir de forma limpia el monto recibido en banco cuando la cuenta "
				"bancaria y la cuenta por cobrar comparten moneda. Registre este gasto manualmente en "
				"ERPNext, o quite el gasto bancario de esta forma de pago."
			)
		)

	cuenta = get_cuenta_gastos_bancarios()
	if not cuenta:
		frappe.throw(
			_(
				"Esta forma de pago indica un gasto bancario de {0}, pero no hay una Cuenta de gastos "
				"bancarios configurada en el perfil Global de Cobranza."
			).format(gasto_bancario)
		)

	monto_base = flt(gasto_bancario * flt(pe.source_exchange_rate or 1), 2)
	pe.paid_amount = flt(flt(pe.paid_amount) + gasto_bancario, 2)

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
	"""El gasto bancario de una forma de pago solo se puede aplicar cuando TODO el
	monto de esa forma va a un único Payment Entry: si se reparte entre varios
	documentos, o entre documento y anticipo, no hay forma confiable de saber a
	cuál cargar el gasto (mismo criterio que el resto de este módulo)."""
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


def _fijar_tipo_cambio_real(pe, pago_cliente):
	"""Reemplaza la(s) tasa(s) "de la factura" que `get_payment_entry` propuso
	por la tasa REAL que el cobrador capturó (Pago Cliente.tipo_cambio). Hay
	dos casos, según cómo esté armada la cuenta por cobrar del cliente:

	1. "nativo" — cuenta por cobrar en la MISMA moneda extranjera que el
	   documento y el banco (p. ej. "CLIENTES DEL EXTERIOR" en USD): se fijan
	   AMBAS tasas (source y target) a la real. El diferencial que eso genera
	   lo calcula y contabiliza ERPNext por su cuenta, con su mecanismo nativo
	   de ganancia/pérdida cambiaria por referencia (columna
	   `exchange_gain_loss` de Payment Entry Reference), que asienta un
	   Journal Entry aparte contra `Company.exchange_gain_loss_account` al
	   validar. No hace falta ningún paso extra de este lado.

	2. "manual" — cuenta por cobrar en la moneda de la EMPRESA (el caso que
	   atiende `_monto_para_cuenta_por_cobrar`): solo se fija la tasa del lado
	   banco (target, para Receive). Aquí SÍ hace falta el paso extra de
	   `_registrar_diferencial_cambiario`, porque la cuenta por cobrar ya está
	   en moneda de la empresa y ERPNext no tiene una tasa "de factura" que
	   comparar de ese lado — no genera nada solo.

	Devuelve "nativo", "manual" o None si no aplicó ninguno de los dos casos.
	"""
	tipo_cambio = flt(pago_cliente.get("tipo_cambio"))
	if tipo_cambio <= 0:
		return None

	if pe.paid_from_account_currency == pe.paid_to_account_currency:
		if pe.paid_from_account_currency != pago_cliente.moneda:
			return None  # ambas cuentas ya en moneda de la empresa: nada que ajustar
		pe.source_exchange_rate = tipo_cambio
		pe.target_exchange_rate = tipo_cambio
		return "nativo"

	if pe.paid_from_account_currency != pe.company_currency:
		return None
	if pe.paid_to_account_currency != pago_cliente.moneda:
		return None

	pe.target_exchange_rate = tipo_cambio
	return "manual"


def _registrar_diferencial_cambiario(pe, pago_cliente, aplica):
	"""Con la tasa real ya fijada (`_fijar_tipo_cambio_real`, caso "manual"),
	recalcula los montos y registra la diferencia resultante entre esa tasa y
	la de la factura como ganancia o pérdida cambiaria. El caso "nativo" no
	llama a esta función: ERPNext ya lo contabiliza por su cuenta (ver
	`_fijar_tipo_cambio_real`)."""
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
				"Cobranza."
			).format(abs(diferencia), tipo_cambio, _("pérdida") if es_perdida else _("ganancia"))
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
			"description": _("Diferencial cambiario (tipo de cambio real {0}).").format(tipo_cambio),
		},
	)


def _resolver_cuenta(empresa, forma):
	"""Cuenta contable a usar como banco/caja del Payment Entry.

	Orden: cuenta contable explícita en la fila (ya sea elegida por el cobrador
	o autocompletada desde el perfil Global al capturar el pago) > cuenta ligada
	al Bank Account de la fila > la única cuenta configurada en el perfil Global
	para ese tipo, si acaso la fila llegó sin ninguna de las dos anteriores >
	cuenta por defecto del Modo de Pago para la empresa.
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
	"""Este sitio exige `reference_no` único en todo Payment Entry (property setter
	propia de Solymar). Un mismo recibo puede generar varios Payment Entry (p.ej.
	un anticipo que luego se parte en aplicado + remanente), así que hay que
	desambiguar en vez de reintentar con el mismo texto."""
	base = (base or "").strip() or "S/R"
	candidato = base
	sufijo = 1
	while frappe.db.exists("Payment Entry", {"reference_no": candidato}):
		sufijo += 1
		candidato = f"{base}-{sufijo}"
	return candidato


def _monto_para_cuenta_por_cobrar(documento, monto):
	"""`monto` viene en la moneda del documento (pagos_chappsa exige que coincidan).
	`get_payment_entry` espera `party_amount` en la moneda de la CUENTA por cobrar
	de la factura, que puede ser distinta (aquí: facturas en USD contra la cuenta
	"CLIENTES" en GTQ). Se convierte con la MISMA tasa de la factura, para no
	inventar un diferencial cambiario que nadie pidió."""
	factura = frappe.db.get_value(
		"Sales Invoice", documento, ["currency", "conversion_rate", "party_account_currency"], as_dict=True
	)
	if factura and factura.party_account_currency and factura.party_account_currency != factura.currency:
		return flt(flt(monto, 2) * flt(factura.conversion_rate or 1), 2)
	return flt(monto, 2)


def _crear_payment_entry(pago_cliente, forma, tipo_documento, documento, monto):
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	if tipo_documento != "Sales Invoice":
		frappe.throw(
			_("Aplicación de pago nativo no soportada para el tipo de documento {0}.").format(tipo_documento)
		)

	_validar_gasto_bancario_aplicable(forma, monto)
	cuenta = _resolver_cuenta(pago_cliente.empresa, forma)
	fecha_referencia = getdate(forma.fecha or pago_cliente.fecha)

	pe = get_payment_entry(
		"Sales Invoice",
		documento,
		party_amount=_monto_para_cuenta_por_cobrar(documento, monto),
		bank_account=cuenta,
		reference_date=fecha_referencia,
	)
	pe.posting_date = getdate(pago_cliente.fecha)
	pe.mode_of_payment = forma.forma_pago or None
	pe.reference_no = _referencia_unica(forma.no_documento or pago_cliente.no_recibo_manual)
	pe.reference_date = fecha_referencia
	pe.remarks = _("Generado automáticamente desde Pago Cliente {0} (recibo {1}).").format(
		pago_cliente.name, pago_cliente.no_recibo_manual
	)
	pe.custom_pagos_chappsa_pago_cliente = pago_cliente.name
	pe.custom_pagos_chappsa_forma_idx = forma.id_linea
	pe.custom_pagos_chappsa_documento = f"{tipo_documento}::{documento}"

	resultado_tipo_cambio = _fijar_tipo_cambio_real(pe, pago_cliente)
	_aplicar_gasto_bancario(pe, forma.gasto_bancario)
	_registrar_diferencial_cambiario(pe, pago_cliente, resultado_tipo_cambio == "manual")

	pe.flags.ignore_permissions = True
	pe.insert(ignore_permissions=True)
	pe.submit()
	return pe


def _crear_anticipo_nativo(pago_cliente, forma, monto):
	"""Registra dinero recibido que no se aplicó a ningún documento como un
	Payment Entry "Receive" sin referencia (anticipo real en ERPNext).

	Si la cuenta por cobrar del cliente está en una moneda distinta a la del
	pago, se usa el tipo de cambio real que el cobrador capturó
	(Pago Cliente.tipo_cambio) para convertir: al no existir todavía una
	factura de la cual tomar prestada una tasa, esa conversión no genera
	ganancia ni pérdida en este momento (ambos lados del asiento quedan
	valuados igual); el diferencial, si lo hay, aparecerá más adelante cuando
	este anticipo se consuma contra una factura real con su propia tasa.

	Devuelve None (sin crear nada) solo si esa cuenta está en una TERCERA
	moneda que no es ni la del pago ni la de la empresa, o si no hay tipo de
	cambio capturado: ahí sí falta una tasa de la cual no corresponde inventar
	una "de hoy".
	"""
	from erpnext.accounts.party import get_party_account

	_validar_gasto_bancario_aplicable(forma, monto)

	cuenta_cliente_defecto = get_party_account("Customer", pago_cliente.cliente, pago_cliente.empresa)
	cuenta_cliente = _resolver_cuenta_anticipo(pago_cliente, cuenta_cliente_defecto)
	moneda_cuenta_cliente = frappe.get_cached_value("Account", cuenta_cliente, "account_currency")

	tipo_cambio = flt(pago_cliente.get("tipo_cambio"))
	monto_cuenta_cliente = flt(monto, 2)
	convertir = bool(moneda_cuenta_cliente) and moneda_cuenta_cliente != pago_cliente.moneda
	# Banco y cuenta de anticipo YA en la misma moneda extranjera: se usa la
	# tasa real capturada en vez de la que ERPNext adivine solo (get_exchange_rate
	# del día), para no depender de que haya un tipo de cambio configurado ahí.
	mismo_extranjero = bool(moneda_cuenta_cliente) and moneda_cuenta_cliente == pago_cliente.moneda and tipo_cambio > 0
	if convertir:
		moneda_empresa = frappe.get_cached_value("Company", pago_cliente.empresa, "default_currency")
		if moneda_cuenta_cliente != moneda_empresa or tipo_cambio <= 0:
			return None
		monto_cuenta_cliente = flt(flt(monto, 2) * tipo_cambio, 2)

	cuenta = _resolver_cuenta(pago_cliente.empresa, forma)
	fecha_referencia = getdate(forma.fecha or pago_cliente.fecha)

	pe = frappe.new_doc("Payment Entry")
	pe.payment_type = "Receive"
	pe.party_type = "Customer"
	pe.party = pago_cliente.cliente
	pe.company = pago_cliente.empresa
	pe.posting_date = getdate(pago_cliente.fecha)
	pe.paid_from = cuenta_cliente
	pe.paid_to = cuenta
	pe.paid_amount = monto_cuenta_cliente
	pe.received_amount = flt(monto, 2)
	if convertir:
		pe.source_exchange_rate = 1
		pe.target_exchange_rate = tipo_cambio
	elif mismo_extranjero:
		pe.source_exchange_rate = tipo_cambio
		pe.target_exchange_rate = tipo_cambio
	pe.mode_of_payment = forma.forma_pago or None
	pe.reference_no = _referencia_unica(forma.no_documento or pago_cliente.no_recibo_manual)
	pe.reference_date = fecha_referencia
	pe.remarks = _("Anticipo generado desde Pago Cliente {0} (recibo {1}), sin aplicar a ningún documento aún.").format(
		pago_cliente.name, pago_cliente.no_recibo_manual
	)
	pe.custom_pagos_chappsa_pago_cliente = pago_cliente.name
	pe.custom_pagos_chappsa_forma_idx = forma.id_linea
	pe.custom_pagos_chappsa_documento = f"{PREFIJO_ANTICIPO}{pago_cliente.name}"

	_aplicar_gasto_bancario(pe, forma.gasto_bancario)

	pe.flags.ignore_permissions = True
	pe.insert(ignore_permissions=True)
	pe.submit()
	return pe.name


def _consumir_anticipo(documento_origen, monto, tipo_documento_destino, documento_destino):
	"""Reasigna hasta `monto` de los anticipos nativos abiertos de `documento_origen`
	(el Pago Cliente que los generó) contra `documento_destino`.

	Cada anticipo nativo consumido se cancela y se reemplaza por un Payment Entry
	que aplica lo usado contra el destino y, si sobra, un nuevo anticipo por el
	remanente — nunca se edita en sitio un Payment Entry ya validado.
	"""
	if tipo_documento_destino != "Sales Invoice":
		frappe.throw(
			_("No se puede aplicar un anticipo nativo contra un documento que no sea una Factura de Venta.")
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
			pago_origen=avance.custom_pagos_chappsa_pago_cliente,
			forma_idx=avance.custom_pagos_chappsa_forma_idx,
		)

		avance.flags.ignore_permissions = True
		avance.cancel()

		nuevo = get_payment_entry(
			"Sales Invoice",
			documento_destino,
			party_amount=usar,
			bank_account=datos.paid_to,
			reference_date=datos.reference_date,
		)
		nuevo.posting_date = datos.posting_date
		nuevo.mode_of_payment = datos.mode_of_payment
		nuevo.reference_no = _referencia_unica(datos.reference_no)
		nuevo.reference_date = datos.reference_date
		nuevo.remarks = _("Anticipo de {0} aplicado a {1}.").format(datos.pago_origen, documento_destino)
		nuevo.custom_pagos_chappsa_pago_cliente = datos.pago_origen
		nuevo.custom_pagos_chappsa_forma_idx = datos.forma_idx
		nuevo.custom_pagos_chappsa_documento = f"Sales Invoice::{documento_destino}"
		nuevo.flags.ignore_permissions = True
		nuevo.insert(ignore_permissions=True)
		nuevo.submit()
		creados.append(nuevo.name)

		restante_avance = flt(disponible - usar, 2)
		if restante_avance > 0:
			remanente = frappe.new_doc("Payment Entry")
			remanente.payment_type = "Receive"
			remanente.party_type = "Customer"
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
			remanente.custom_pagos_chappsa_pago_cliente = datos.pago_origen
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
					"cobrar del cliente, ver Anticipo no aplicado en ERPNext al validar ese pago). "
					"Aplique este crédito manualmente en ERPNext."
				).format(documento_origen)
			)
		frappe.throw(
			_(
				"El anticipo nativo de {0} disponible en ERPNext no alcanza para cubrir {1}; faltan {2}. "
				"Revise los Payment Entry marcados como anticipo de ese pago."
			).format(documento_origen, flt(monto, 2), restante)
		)

	return creados


def aplicar_pagos_nativos(pago_cliente, filas_documento=None):
	"""Aplica en ERPNext, como Payment Entry(s) nativo(s), los abonos de `pago_cliente`.

	`filas_documento`: subconjunto de filas de detalle a liquidar ahora (cada una
	debe traer tipo_documento="Sales Invoice"). Se usa desde el on_submit de
	Sales Invoice para liquidar solo la factura recién validada — en ese camino
	no se consideran créditos de anticipo ni excedente propio, porque el pago
	histórico ya se procesó en su momento. Si es None (flujo de Factura de
	Venta), se procesan las filas Sales Invoice del propio pago, los créditos de
	anticipo que aplique, y cualquier excedente nuevo que deje se registra como
	anticipo nativo.
	"""
	retroactivo = filas_documento is not None

	if retroactivo:
		filas = [f for f in filas_documento if flt(f.abono) > 0]
		creditos = []
	else:
		filas = [
			f
			for f in pago_cliente.detalle_documentos
			if f.tipo_documento == "Sales Invoice" and flt(f.abono) > 0
		]
		creditos = [
			f
			for f in pago_cliente.detalle_documentos
			if f.tipo_documento == TIPO_ANTICIPO and flt(f.abono) < 0
		]

	formas = list(pago_cliente.detalle_formas_pago)
	excedente_nuevo = flt(pago_cliente.excedente, 2) if not retroactivo else 0

	if not filas and not creditos and excedente_nuevo <= 0:
		return []

	if len(filas) > 1 and (len(formas) > 1 or len(creditos) > 1):
		frappe.throw(
			_(
				"Este pago aplica a {0} documentos con {1} formas de pago y {2} créditos de anticipo: la "
				"aplicación automática del pago nativo no soporta esa combinación (no hay forma de saber "
				"qué banco o anticipo pagó cuál documento). Registre el pago nativo manualmente en "
				"ERPNext, o divida este recibo en pagos separados por documento."
			).format(len(filas), len(formas), len(creditos))
		)

	if creditos and len(filas) != 1:
		frappe.throw(
			_("No se puede aplicar un crédito de anticipo sin una única factura de destino en este pago.")
		)

	creados = []
	restante_por_forma = {f.id_linea: flt(f.monto, 2) for f in formas}

	if len(filas) == 1:
		# Una factura: primero los créditos de anticipo, luego formas de pago nuevas.
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
			if _ya_aplicado(pago_cliente.name, forma.id_linea, clave_documento):
				continue
			monto = min(restante_por_forma[forma.id_linea], restante_factura)
			if monto <= 0:
				continue
			pe = _crear_payment_entry(pago_cliente, forma, fila.tipo_documento, fila.documento, monto)
			creados.append(pe.name)
			restante_por_forma[forma.id_linea] = flt(restante_por_forma[forma.id_linea] - monto, 2)
			restante_factura = flt(restante_factura - monto, 2)

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
			if _ya_aplicado(pago_cliente.name, forma.id_linea, clave_documento):
				continue
			peso = flt(fila.abono, 2) / total_base if total_base else 0
			monto = min(flt(bolsa * peso, 2), restante_por_forma[forma.id_linea])
			if monto <= 0:
				continue
			pe = _crear_payment_entry(pago_cliente, forma, fila.tipo_documento, fila.documento, monto)
			creados.append(pe.name)
			restante_por_forma[forma.id_linea] = flt(restante_por_forma[forma.id_linea] - monto, 2)

	# Dinero que no quedó aplicado a ningún documento: anticipo nativo nuevo.
	if excedente_nuevo > 0:
		clave_anticipo = f"{PREFIJO_ANTICIPO}{pago_cliente.name}"
		anticipo_omitido = False
		for forma in formas:
			sobra = restante_por_forma.get(forma.id_linea, 0)
			if sobra <= 0:
				continue
			if _ya_aplicado(pago_cliente.name, forma.id_linea, clave_anticipo):
				continue
			resultado = _crear_anticipo_nativo(pago_cliente, forma, sobra)
			if resultado:
				creados.append(resultado)
			else:
				anticipo_omitido = True

		if anticipo_omitido:
			frappe.msgprint(
				_(
					"El excedente de este pago quedó como anticipo en pagos_chappsa, pero NO se aplicó "
					"como Payment Entry nativo en ERPNext: está en una moneda distinta a la de la cuenta "
					"por cobrar del cliente y no hay una tasa de cambio de referencia clara para usar. "
					"Regístrelo manualmente en ERPNext si corresponde."
				),
				title=_("Anticipo no aplicado en ERPNext"),
				indicator="orange",
			)

	return creados


def cancelar_pago_cliente_si_corresponde(doc, method=None):
	"""Hook `on_cancel` de Payment Entry: si la que se está cancelando (desde
	contabilidad, en ERPNext) es una que generó Cobranza, el Pago Cliente que la
	originó se cancela también, para que ambos lados queden sincronizados sin
	importar por dónde se cancele.

	Reutiliza las mismas validaciones de `Pago Cliente.before_cancel` (permisos,
	anticipo no consumido, etc.): si alguna de ellas no deja cancelarlo, la
	excepción sube y con ella se revierte también la cancelación de esta
	Payment Entry — quedan en la misma transacción.
	"""
	nombre = doc.get("custom_pagos_chappsa_pago_cliente")
	if not nombre:
		return

	pago_cliente = frappe.get_doc("Pago Cliente", nombre)
	if pago_cliente.docstatus != 1:
		# Ya cancelado (esta misma cascada, si el origen fue el propio Pago
		# Cliente) o nunca llegó a validarse: nada que hacer.
		return

	pago_cliente.flags.ignore_permissions = True
	pago_cliente.cancel()
	frappe.msgprint(
		_("El Pago Cliente {0} se canceló automáticamente junto con esta Payment Entry.").format(
			frappe.bold(pago_cliente.name)
		),
		alert=True,
		indicator="orange",
	)


def revertir_pagos_nativos(pago_cliente):
	"""Cancela todos los Payment Entry nativos atados a `pago_cliente` (directos
	o derivados de un anticipo suyo que nadie más ha consumido todavía)."""
	nombres = frappe.get_all(
		"Payment Entry",
		filters={"custom_pagos_chappsa_pago_cliente": pago_cliente.name, "docstatus": 1},
		pluck="name",
	)
	for nombre in nombres:
		pe = frappe.get_doc("Payment Entry", nombre)
		pe.flags.ignore_permissions = True
		pe.cancel()


# ---------------------------------------------------------------------------
# Sales Invoice: regla 1 a 1 y aplicación retroactiva (modo Nota de Entrega)
# ---------------------------------------------------------------------------
def _nota_entrega_de_factura(sales_invoice):
	return {item.delivery_note for item in sales_invoice.items if item.delivery_note}


def validar_factura_desde_nota_entrega(doc, method=None):
	if get_origen_global() != "Nota de Entrega":
		return

	notas = _nota_entrega_de_factura(doc)
	if not notas:
		return

	if len(notas) > 1:
		frappe.throw(
			_(
				"Esta factura combina ítems de {0} Notas de Entrega distintas. Con el Origen «Nota de "
				"Entrega» activo, cada factura debe corresponder a una sola nota, de una a una."
			).format(len(notas))
		)

	nota = notas.pop()

	otra = frappe.db.get_value(
		"Sales Invoice Item",
		{"delivery_note": nota, "docstatus": 1, "parent": ("!=", doc.name)},
		"parent",
	)
	if otra:
		frappe.throw(
			_("La Nota de Entrega {0} ya fue facturada en {1}. No se puede facturar dos veces.").format(
				frappe.bold(nota), frappe.bold(otra)
			)
		)

	items_nota = frappe.get_all("Delivery Note Item", filters={"parent": nota}, fields=["name", "qty"])
	qty_por_fila = {f.name: flt(f.qty) for f in items_nota}
	cubiertas = set()

	for item in doc.items:
		if item.delivery_note != nota:
			continue
		if not item.dn_detail or item.dn_detail not in qty_por_fila:
			continue
		cubiertas.add(item.dn_detail)
		if abs(flt(item.qty) - qty_por_fila[item.dn_detail]) > 0.001:
			frappe.throw(
				_(
					"La factura debe cubrir el 100% de la Nota de Entrega {0}: la fila {1} factura {2} y "
					"la nota tiene {3}. No se permite facturación parcial."
				).format(frappe.bold(nota), item.idx, item.qty, qty_por_fila[item.dn_detail])
			)

	faltantes = set(qty_por_fila) - cubiertas
	if faltantes:
		frappe.throw(
			_(
				"La factura no incluye todas las líneas de la Nota de Entrega {0}. Facture la nota "
				"completa, de una a una."
			).format(frappe.bold(nota))
		)


def aplicar_pagos_retroactivos_nota_entrega(doc, method=None):
	if get_origen_global() != "Nota de Entrega":
		return

	notas = _nota_entrega_de_factura(doc)
	if len(notas) != 1:
		return
	nota = notas.pop()

	filas_pago = frappe.get_all(
		"Detalle Documento Pago", filters={"tipo_documento": "Delivery Note", "documento": nota}, fields=["parent"]
	)
	if not filas_pago:
		return

	nombres_pago = sorted({f.parent for f in filas_pago})
	validados = frappe.get_all(
		"Pago Cliente", filters={"name": ("in", nombres_pago), "docstatus": 1}, pluck="name"
	)

	omitidos = []
	for nombre in sorted(validados):
		pago_cliente = frappe.get_doc("Pago Cliente", nombre)
		filas_dn = [
			f for f in pago_cliente.detalle_documentos if f.tipo_documento == "Delivery Note" and flt(f.abono) > 0
		]
		fila = next((f for f in filas_dn if f.documento == nota), None)
		if not fila:
			continue

		if len(filas_dn) > 1:
			# Este pago histórico repartió el abono entre varias notas: no hay forma segura de
			# saber si la forma de pago ya se usó (o se usará) para liquidar las otras. Se deja
			# fuera de la automatización a propósito, ver docstring del módulo.
			omitidos.append(nombre)
			continue

		aplicar_pagos_nativos(
			pago_cliente,
			filas_documento=[frappe._dict(fila.as_dict(), documento=doc.name, tipo_documento="Sales Invoice")],
		)

	if omitidos:
		frappe.msgprint(
			_(
				"Los siguientes Pagos Cliente abonaron a más de un documento y no se aplicaron "
				"automáticamente como pago nativo; regístrelos manualmente en ERPNext: {0}"
			).format(", ".join(frappe.bold(n) for n in omitidos)),
			title=_("Revisión manual pendiente"),
			indicator="orange",
		)
