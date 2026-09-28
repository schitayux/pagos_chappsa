// =====================================================
// Página "Pagos a Proveedores" — captura y aplicación de pagos a proveedores
//
// Espejo de la página "Pagos" (pagos.js) de cobranza, adaptada a cuentas por
// pagar: se elige el PROVEEDOR, el único documento de cargo es la Factura de
// Compra, y el monto capturado es dinero que la empresa ENTREGA.
// =====================================================

frappe.pages["pagos_proveedores"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Pagos a Proveedores"),
		single_column: true,
	});
	frappe.pagos_proveedores_panel = new PagosProveedoresPanel(page);
};

frappe.pages["pagos_proveedores"].on_page_show = function () {
	const panel = frappe.pagos_proveedores_panel;
	if (panel && panel.$body.find('.nav-link[data-tab="recientes"]').hasClass("active")) {
		panel.refrescar_recientes();
	}
};

const TOL = 0.005;
const TIPO_ANTICIPO = "Pago Proveedor";

class PagosProveedoresPanel {
	constructor(page) {
		this.page = page;
		this.$body = $(page.body);
		this.ctx = {};
		this.cuentas = [];
		this.reset_estado();
		this.build();
	}

	reset_estado() {
		this.pago_name = null;
		this.solo_lectura = false;
		this.puede_imprimir = false;
		this.pago_name_impreso = null;
		this.estado_pago = null;
		this.documentos = [];
		this.empresa = null;
		this.moneda = null;
		this.comentarios = "";
		this.monto = 0;
		this.solo_anticipo = false;
		this.tipo_cambio = null;
		this.cuenta_anticipo = null;
		this.formas = {
			efectivo: { monto: 0, comentario: "" },
			transferencia: [],
			cheque: [],
			retencion: [],
			tarjeta: [],
		};
	}

	fmt(v) {
		return format_currency(flt(v), this.moneda || "GTQ");
	}

	dis() {
		return this.solo_lectura ? " disabled" : "";
	}

	/** Igual que `dis()`, pero además bloquea la tabla de documentos cuando el
	 * pago se marcó como "solo anticipo": no tiene sentido dejar tocar abonos
	 * de documentos que expresamente no se quieren pagar. */
	dis_tabla() {
		return this.solo_lectura || this.solo_anticipo ? " disabled" : "";
	}

	opciones_cuenta(tipo) {
		return (this.ctx.cuentas_por_forma || {})[tipo] || [];
	}

	cuenta_unica(tipo) {
		const opciones = this.opciones_cuenta(tipo);
		return opciones.length === 1 ? opciones[0] : null;
	}

	// ==================================================================
	// Layout
	// ==================================================================
	build() {
		this.$body.html(`
			<style>
				.pg-card { padding:12px 14px; border:1px solid var(--border-color); border-radius:10px; background:var(--card-bg); }
				.pg-tabla th { background:var(--subtle-fg); font-weight:600; white-space:nowrap; }
				.pg-tabla td, .pg-tabla th { padding:5px 8px !important; vertical-align:middle !important; }
				.pg-credito { background:#c6f6d5 !important; color:#0b3d24; font-weight:600; }
				.pg-cuenta { cursor:pointer; padding:6px 12px; border:1px solid var(--border-color);
				             border-radius:20px; font-size:12px; background:var(--bg-color); }
				.pg-cuenta.activa { background:var(--blue-500); color:#fff; border-color:var(--blue-500); font-weight:600; }
				.pg-metrica-valor { font-size:17px; font-weight:700; }
				.pg-metrica-tit { font-size:10px; text-transform:uppercase; letter-spacing:.4px; }
				.pg-seccion { padding:12px; border:1px solid var(--border-color); border-radius:10px; }
				.pg-seccion-tit { font-weight:700; margin-bottom:8px; }
				.pg-monto-input { height:38px !important; font-size:18px !important; font-weight:700; text-align:right; }

				.nav-tabs .nav-link { font-weight:600; border-bottom:3px solid transparent; }
				.nav-tabs .nav-link[data-tab="dist"]        { color:#1a4fa0; }
				.nav-tabs .nav-link[data-tab="dist"].active { background:#e8f0fe; border-bottom-color:#1a4fa0; }
				.nav-tabs .nav-link[data-tab="formas"]        { color:#8a4b00; }
				.nav-tabs .nav-link[data-tab="formas"].active { background:#fff4e5; border-bottom-color:#e08600; }
				.nav-tabs .nav-link[data-tab="recientes"]        { color:#0b6b3a; }
				.nav-tabs .nav-link[data-tab="recientes"].active { background:#e7f7ee; border-bottom-color:#0b8a4a; }
				.pg-fila-pago { cursor:pointer; }
				.pg-fila-pago:hover { background:#eef4ff !important; }
				.pg-banner-lectura { background:#fff4e5; border:1px solid #e0a800; color:#7a4b00;
				                     padding:8px 12px; border-radius:8px; font-weight:600; }
			</style>

			<div style="display:flex; flex-direction:column; gap:12px;">

				<div class="pg-card">
					<div style="display:flex; flex-wrap:wrap; gap:12px; align-items:end;">
						<div id="pg_proveedor" style="min-width:340px; flex:1;"></div>
						<div id="pg_fecha" style="min-width:170px;"></div>
						<div id="pg_no_recibo" style="min-width:200px;"></div>
						<div id="pg_tipo_cambio_wrap" style="display:none; min-width:150px;">
							<div id="pg_tipo_cambio"></div>
						</div>
					</div>
					<div id="pg_cuentas" style="display:flex; gap:8px; flex-wrap:wrap; margin-top:10px;"></div>
					<div style="margin-top:10px;">
						<button class="btn btn-default btn-sm" id="pg_btn_limpiar_todo">🧹 Limpiar datos generales</button>
					</div>
				</div>

				<div id="pg_banner"></div>
				<div id="pg_resumen"></div>

				<ul class="nav nav-tabs" role="tablist" style="margin-bottom:0;">
					<li class="nav-item"><a class="nav-link active" data-tab="dist" href="#">1. Documentos a pagar</a></li>
					<li class="nav-item"><a class="nav-link" data-tab="formas" href="#">2. Formas de pago</a></li>
					<li class="nav-item"><a class="nav-link" data-tab="recientes" href="#">Pagos recientes</a></li>
				</ul>

				<div id="pg_tab_dist" class="pg-tab"></div>
				<div id="pg_tab_formas" class="pg-tab" style="display:none;"></div>
				<div id="pg_tab_recientes" class="pg-tab" style="display:none;"></div>
			</div>
		`);

		this.make_filtros();
		this.make_tabs();

		this.page.set_primary_action(__("Validar pago"), () => this.guardar(1), "check");
		this.page.set_secondary_action(__("Guardar borrador"), () => this.guardar(0));
		this.$body.find("#pg_btn_limpiar_todo").on("click", () => this.limpiar_todo());
		this.actualizar_acciones();

		this.render_todo();
		this.cargar_contexto();
	}

	actualizar_acciones() {
		const p = this.page;
		const perfil = this.ctx.perfil || {};
		const hay_proveedor = !!this.controles?.proveedor?.get_value();

		if (this.solo_lectura) {
			p.btn_primary?.hide();
			p.btn_secondary?.hide();
		} else {
			p.btn_primary?.show();
			p.btn_secondary?.show();
			p.btn_secondary?.prop("disabled", !hay_proveedor || this.ctx.puede_crear === false);
			const puede = !!this.pago_name && this.ctx.puede_validar !== false;
			p.btn_primary?.prop("disabled", !puede);
			p.btn_primary?.attr(
				"title",
				puede ? "" : __("Guarde primero el pago como borrador para poder validarlo.")
			);
		}

		const ctl_recibo = this.controles?.no_recibo;
		if (ctl_recibo) {
			ctl_recibo.df.read_only = this.solo_lectura ? 1 : 0;
			ctl_recibo.refresh();
		}

		this.$body.find("#pg_btn_imprimir").remove();
		this.$body.find("#pg_btn_editar_recibo").remove();
		this.$body.find("#pg_btn_ver_pe").remove();
		if (this.solo_lectura && this.pago_name_impreso && this.estado_pago !== "Cancelado") {
			const $e = $(
				`<button class="btn btn-default btn-sm" id="pg_btn_editar_recibo" style="margin-left:8px;">✏️ Corregir No. de comprobante</button>`
			);
			$e.on("click", () => this.editar_no_recibo(this.pago_name_impreso));
			this.$body.find("#pg_btn_limpiar_todo").after($e);
		}
		let $ancla_pe = this.$body.find("#pg_btn_limpiar_todo");
		if (this.pago_name_impreso && this.puede_imprimir && perfil.permitir_reimpresion_recibo !== 0) {
			const $b = $(
				`<button class="btn btn-default btn-sm" id="pg_btn_imprimir" style="margin-left:8px;">🖨️ Imprimir comprobante</button>`
			);
			$b.on("click", () => this.imprimir_recibo(this.pago_name_impreso));
			this.$body.find("#pg_btn_limpiar_todo").after($b);
			$ancla_pe = $b;
		}
		if (this.pago_name_impreso && this.solo_lectura) {
			const $v = $(
				`<button class="btn btn-default btn-sm" id="pg_btn_ver_pe" style="margin-left:8px;">🔗 Ver entrada de pago</button>`
			);
			$v.on("click", () => this.ver_entrada_pago(this.pago_name_impreso));
			$ancla_pe.after($v);
		}

		this.render_banner();
	}

	render_banner() {
		const $b = this.$body.find("#pg_banner");
		if (!this.solo_lectura) return $b.empty();
		$b.html(
			`<div class="pg-banner-lectura">🔒 Pago ${frappe.utils.escape_html(this.pago_name_impreso || "")}
			 — ${frappe.utils.escape_html(this.estado_pago || "")}. Solo lectura: un pago validado no se modifica.
			 Para corregirlo hay que cancelarlo desde el documento.</div>`
		);
	}

	editar_no_recibo(name) {
		frappe.prompt(
			[
				{
					fieldtype: "Data",
					fieldname: "no_recibo",
					label: __("No. de comprobante de pago"),
					reqd: 1,
					default: this.controles.no_recibo.get_value() || "",
					description: __("Se validará que no esté usado en otro pago."),
				},
			],
			(valores) => {
				frappe.call({
					method: "pagos_chappsa.api.pagos_proveedor.actualizar_no_recibo",
					args: { name, no_recibo: valores.no_recibo },
					freeze: true,
					freeze_message: __("Actualizando No. de comprobante..."),
					callback: (r) => {
						if (!r.message) return;
						this.controles.no_recibo.set_value(r.message.no_recibo_manual);
						this.controles.no_recibo.refresh();
						frappe.show_alert({
							message: __("No. de comprobante actualizado a {0}", [r.message.no_recibo_manual]),
							indicator: "green",
						});
						this.refrescar_recientes();
					},
				});
			},
			__("Corregir No. de comprobante del pago {0}", [name]),
			__("Guardar")
		);
	}

	imprimir_recibo(name) {
		frappe.call({
			method: "pagos_chappsa.api.pagos_proveedor.registrar_reimpresion",
			args: { name },
			callback: (r) => {
				if (!r.message) return;
				window.open(
					"/printview?doctype=" + encodeURIComponent("Pago Proveedor") +
						"&name=" + encodeURIComponent(r.message.name) +
						"&format=" + encodeURIComponent(r.message.formato) +
						"&no_letterhead=1",
					"_blank"
				);
			},
		});
	}

	/** Abre la Payment Entry nativa que este pago generó en ERPNext. Si generó
	 * más de una (p. ej. aplicado + remanente de anticipo), abre la lista
	 * filtrada en vez de adivinar cuál mostrar. */
	ver_entrada_pago(name) {
		frappe.call({
			method: "pagos_chappsa.api.pagos_proveedor.get_payment_entries",
			args: { name },
			freeze: true,
			freeze_message: __("Buscando la entrada de pago..."),
			callback: (r) => {
				const filas = r.message || [];
				if (!filas.length) {
					frappe.show_alert({
						message: __("Este pago no tiene una entrada de pago nativa en ERPNext."),
						indicator: "orange",
					});
					return;
				}
				if (filas.length === 1) {
					window.open("/app/payment-entry/" + encodeURIComponent(filas[0].name), "_blank");
					return;
				}
				window.open(
					"/app/payment-entry?custom_pagos_chappsa_pago_proveedor=" + encodeURIComponent(name),
					"_blank"
				);
			},
		});
	}

	make_tabs() {
		this.$body.find(".nav-link").on("click", (e) => {
			e.preventDefault();
			const $a = $(e.currentTarget);
			this.$body.find(".nav-link").removeClass("active");
			$a.addClass("active");
			const tab = $a.data("tab");
			this.$body.find(".pg-tab").hide();
			this.$body.find(`#pg_tab_${tab}`).show();
			if (tab === "recientes") this.refrescar_recientes();
		});
	}

	make_control($parent, df) {
		$parent.empty();
		const control = frappe.ui.form.make_control({
			df: Object.assign({ reqd: 0 }, df),
			parent: $parent.get(0),
			render_input: true,
		});
		control.refresh();
		return control;
	}

	make_filtros() {
		this.controles = {};

		this.controles.proveedor = this.make_control(this.$body.find("#pg_proveedor"), {
			fieldtype: "Link",
			fieldname: "proveedor",
			label: __("Proveedor"),
			options: "Supplier",
			reqd: 1,
			onchange: () => this.cargar_proveedor(),
		});

		this.controles.fecha = this.make_control(this.$body.find("#pg_fecha"), {
			fieldtype: "Date",
			fieldname: "fecha",
			label: __("Fecha del pago"),
			reqd: 1,
		});
		this.controles.fecha.set_value(frappe.datetime.get_today());

		this.controles.no_recibo = this.make_control(this.$body.find("#pg_no_recibo"), {
			fieldtype: "Data",
			fieldname: "no_recibo_manual",
			label: __("No. de comprobante de pago"),
			reqd: 1,
			description: __("Número del comprobante/voucher que respalda este pago."),
		});

		// Solo se muestra cuando la moneda del pago es distinta a la de la empresa
		// (ver actualizar_tipo_cambio_visible). El contenedor arranca oculto.
		this.controles.tipo_cambio = this.make_control(this.$body.find("#pg_tipo_cambio"), {
			fieldtype: "Float",
			fieldname: "tipo_cambio",
			label: __("Tipo de cambio"),
			precision: 6,
			description: __("Tipo de cambio real de este pago."),
			onchange: () => {
				this.tipo_cambio = flt(this.controles.tipo_cambio.get_value());
			},
		});
	}

	/** Moneda por defecto de la empresa activa, según el contexto cargado (get_contexto). */
	moneda_empresa() {
		const empresa = (this.ctx.empresas || []).find((e) => e.name === this.empresa);
		return empresa ? empresa.default_currency : null;
	}

	/** Muestra/oculta y exige el campo "Tipo de cambio" solo cuando este pago
	 * toca una cuenta en moneda distinta a la de la empresa: es lo único que
	 * puede generar un diferencial cambiario real al aplicar el pago nativo. */
	actualizar_tipo_cambio_visible() {
		const moneda_empresa = this.moneda_empresa();
		const requiere = !!(this.moneda && moneda_empresa && this.moneda !== moneda_empresa);
		this.$body.find("#pg_tipo_cambio_wrap").toggle(requiere);

		const ctl = this.controles.tipo_cambio;
		if (!ctl) return;
		if (!requiere) {
			this.tipo_cambio = null;
			if (ctl.get_value()) ctl.set_value("");
		}
		ctl.df.reqd = requiere ? 1 : 0;
		ctl.df.read_only = this.solo_lectura ? 1 : 0;
		ctl.refresh();
	}

	cargar_contexto() {
		frappe.call({
			method: "pagos_chappsa.api.pagos_proveedor.get_contexto",
			callback: (r) => {
				this.ctx = r.message || {};
				if (!this.ctx.puede_crear) this.page.btn_secondary?.prop("disabled", true);
				if (!this.ctx.puede_validar) this.page.btn_primary?.prop("disabled", true);
				this.actualizar_tipo_cambio_visible();
			},
		});
	}

	// ==================================================================
	// Carga del proveedor
	// ==================================================================
	cargar_proveedor(mantener_cuenta) {
		if (this._cargando) return;

		const proveedor = this.controles.proveedor.get_value();

		if (!mantener_cuenta && proveedor !== this._proveedor_cargado) {
			this.pago_name = null;
			this.solo_lectura = false;
			this.pago_name_impreso = null;
			this.puede_imprimir = false;
			this.estado_pago = null;
			this.solo_anticipo = false;
			this.tipo_cambio = null;
			this.controles.tipo_cambio?.set_value("");
		}
		this._proveedor_cargado = proveedor;

		if (!proveedor) {
			this.reset_estado();
			this.cuentas = [];
			this.render_todo();
			return;
		}

		frappe.call({
			method: "pagos_chappsa.api.pagos_proveedor.get_documentos",
			args: {
				proveedor,
				empresa: mantener_cuenta ? this.empresa : null,
				moneda: mantener_cuenta ? this.moneda : null,
				pago: this.pago_name || null,
			},
			freeze: true,
			freeze_message: __("Cargando documentos del proveedor..."),
			callback: (r) => {
				if (!r.message) return;
				const data = r.message;

				this.cuentas = data.cuentas || [];
				this.empresa = data.empresa;
				this.moneda = data.moneda;
				this.nombre_proveedor = data.nombre_proveedor;

				this.documentos = (data.documentos || []).map((d) => ({
					...d,
					abono: 0,
					manual: false,
					aplicar: false,
				}));

				this.cargar_cuentas_banco();
				this.distribuir();
			},
		});
	}

	/** Filtradas por moneda: un pago en USD no debe ofrecer una cuenta en GTQ. */
	cargar_cuentas_banco() {
		const clave = `${this.empresa || ""}::${this.moneda || ""}`;
		if (!this.empresa || this._cuentas_banco_de === clave) return;
		this._cuentas_banco_de = clave;
		frappe.call({
			method: "pagos_chappsa.api.pagos_proveedor.get_cuentas_banco",
			args: { empresa: this.empresa, moneda: this.moneda },
			callback: (r) => {
				this.cuentas_banco = r.message || [];
				this.render_formas();
			},
		});
	}

	seleccionar_cuenta(empresa, moneda) {
		this.empresa = empresa;
		this.moneda = moneda;
		this.monto = 0;
		this.cargar_proveedor(true);
	}

	// ==================================================================
	// Distribución
	// ==================================================================
	distribuir() {
		// "Solo anticipo": el usuario dejó explícito que este pago no abona ningún
		// documento. Todo el monto queda como excedente (anticipo a favor).
		if (this.solo_anticipo) {
			this.documentos.forEach((d) => {
				d.abono = 0;
				d.manual = false;
				d.aplicar = false;
				d.saldo = flt(d.saldo_anterior);
			});
			this.render_todo();
			return;
		}

		const prec = precision_moneda();
		const cargos = this.documentos.filter((d) => d.clase === "Cargo");
		const creditos = this.documentos.filter((d) => d.clase === "Crédito");

		creditos.forEach((d) => {
			if (!d.aplicar) {
				d.abono = 0;
				d.manual = false;
			}
		});

		const cargos_fijos = cargos.filter((d) => d.manual);
		const cargos_auto = cargos.filter((d) => !d.manual);
		const creditos_fijos = creditos.filter((d) => d.aplicar && d.manual);
		const creditos_auto = creditos.filter((d) => d.aplicar && !d.manual);

		const comprometido = flt(cargos_fijos.reduce((s, d) => s + flt(d.abono), 0));
		const credito_fijo = flt(creditos_fijos.reduce((s, d) => s + -flt(d.abono), 0));
		const requerido = flt(cargos_auto.reduce((s, d) => s + flt(d.saldo_anterior), 0));

		let fondos = flt(this.monto - comprometido + credito_fijo);
		let faltante = flt(Math.max(requerido - fondos, 0));

		creditos_auto.forEach((d) => {
			if (faltante <= TOL) {
				d.abono = 0;
				return;
			}
			const usar = Math.min(Math.abs(flt(d.saldo_anterior)), faltante);
			d.abono = flt(-usar, prec);
			faltante = flt(faltante - usar);
			fondos = flt(fondos + usar);
		});

		cargos_auto.forEach((d) => {
			if (fondos <= TOL) {
				d.abono = 0;
				return;
			}
			const abono = Math.min(flt(d.saldo_anterior), fondos);
			d.abono = flt(abono, prec);
			fondos = flt(fondos - d.abono);
		});

		this.documentos.forEach((d) => {
			d.saldo = flt(flt(d.saldo_anterior) - flt(d.abono));
		});

		this.render_todo();
	}

	totales() {
		let aplicado = 0;
		let creditos = 0;
		this.documentos.forEach((d) => {
			const a = flt(d.abono);
			if (a >= 0) aplicado += a;
			else creditos += -a;
		});
		// El gasto bancario ya tiene destino propio (cuenta de gastos bancarios
		// del perfil Global): no debe contarse como excedente/anticipo.
		const gasto_bancario = this.total_gasto_bancario();
		return {
			aplicado: flt(aplicado),
			creditos: flt(creditos),
			monto: flt(this.monto),
			gasto_bancario: flt(gasto_bancario),
			excedente: flt(this.monto - (aplicado - creditos) - gasto_bancario),
			saldo_cuenta: flt(this.documentos.reduce((s, d) => s + flt(d.saldo_anterior), 0)),
			total_formas: this.total_formas(),
		};
	}

	/** Suma de "Gasto bancario" en todas las formas que tienen esa columna
	 * (transferencia, cheque, tarjeta — ver render_formas/col_gasto). */
	total_gasto_bancario() {
		let total = 0;
		["transferencia", "cheque", "tarjeta"].forEach((k) => {
			this.formas[k].forEach((f) => (total += flt(f.gasto_bancario)));
		});
		return flt(total);
	}

	// ==================================================================
	// Render
	// ==================================================================
	render_todo() {
		this.render_cuentas();
		this.render_resumen();
		this.render_dist();
		this.render_formas();
		this.actualizar_tipo_cambio_visible();
		this.actualizar_acciones();
	}

	render_cuentas() {
		const $c = this.$body.find("#pg_cuentas");
		if (this.cuentas.length <= 1) {
			$c.html(
				this.cuentas.length === 1
					? `<span class="text-muted" style="font-size:11px;">Cuenta: <b>${frappe.utils.escape_html(this.cuentas[0].abbr)}</b> · ${this.cuentas[0].moneda}</span>`
					: ""
			);
			return;
		}

		$c.html(
			`<span class="text-muted" style="font-size:11px; align-self:center;">Este proveedor tiene saldo en varias cuentas:</span>` +
				this.cuentas
					.map((c) => {
						const activa = c.empresa === this.empresa && c.moneda === this.moneda;
						return `<span class="pg-cuenta ${activa ? "activa" : ""}"
							data-empresa="${frappe.utils.escape_html(c.empresa)}" data-moneda="${c.moneda}">
							${frappe.utils.escape_html(c.abbr)} · ${c.moneda} — ${format_currency(c.saldo, c.moneda)}
							<span style="opacity:.75;">(${c.documentos})</span>
						</span>`;
					})
					.join("")
		);

		$c.find(".pg-cuenta").on("click", (e) => {
			const $el = $(e.currentTarget);
			this.seleccionar_cuenta($el.data("empresa"), $el.data("moneda"));
		});
	}

	render_resumen() {
		const t = this.totales();
		const falta_fondos = t.excedente < -TOL;
		const formas_ok = Math.abs(t.total_formas - t.monto) <= TOL;

		const metrica = (tit, val, color) => `
			<div style="min-width:150px;">
				<div class="text-muted pg-metrica-tit">${tit}</div>
				<div class="pg-metrica-valor" ${color ? `style="color:${color};"` : ""}>${val}</div>
			</div>`;

		this.$body.find("#pg_resumen").html(`
			<div class="pg-card" style="display:flex; flex-wrap:wrap; gap:22px; align-items:center;">
				${metrica("Saldo de la cuenta", this.fmt(t.saldo_cuenta))}
				${metrica("Aplicado a documentos", this.fmt(t.aplicado))}
				${metrica("Créditos usados", this.fmt(t.creditos))}
				${t.gasto_bancario > TOL ? metrica("Gasto bancario", this.fmt(t.gasto_bancario)) : ""}
				${metrica(
					falta_fondos ? "Faltan fondos" : "Excedente (anticipo)",
					this.fmt(falta_fondos ? -t.excedente : t.excedente),
					falta_fondos ? "var(--red-500)" : "var(--green-600)"
				)}
				${metrica(
					"Formas de pago",
					`${this.fmt(t.total_formas)} ${formas_ok ? "✔" : "⚠"}`,
					formas_ok ? "var(--green-600)" : "var(--orange-500)"
				)}
				${t.excedente > TOL ? this.celda_cuenta_anticipo() : ""}
			</div>
		`);

		this.$body.find("#pg_cuenta_anticipo").on("change", (e) => {
			this.cuenta_anticipo = $(e.currentTarget).val();
		});
	}

	/** Se muestra únicamente cuando el pago detecta un excedente (anticipo a
	 * generar): a qué cuenta de mayor se dirigirá ese saldo a favor de la empresa. */
	celda_cuenta_anticipo() {
		const opciones = this.ctx.cuentas_anticipo || [];
		if (!opciones.length) return "";

		if (opciones.length === 1) {
			this.cuenta_anticipo = opciones[0];
			return `
				<div style="min-width:200px;">
					<div class="text-muted pg-metrica-tit">Cuenta de anticipo</div>
					<div style="font-weight:600;">${frappe.utils.escape_html(opciones[0])}</div>
				</div>`;
		}

		const items = opciones
			.map(
				(c) =>
					`<option value="${frappe.utils.escape_html(c)}" ${c === this.cuenta_anticipo ? "selected" : ""}>${frappe.utils.escape_html(c)}</option>`
			)
			.join("");
		return `
			<div style="min-width:220px;">
				<label class="control-label" style="font-size:11px;">Cuenta de anticipo (saldo a favor)</label>
				<select class="form-control input-sm" id="pg_cuenta_anticipo"${this.dis()}>
					<option value=""></option>${items}
				</select>
			</div>`;
	}

	render_dist() {
		const t = this.totales();
		const $tab = this.$body.find("#pg_tab_dist");

		if (!this.controles.proveedor.get_value()) {
			$tab.html(`<div class="pg-card" style="border-radius:0 0 10px 10px;">
				<div class="text-muted" style="padding:24px; text-align:center;">
					Escriba el nombre del proveedor arriba. El detalle de sus documentos pendientes se carga solo.
				</div></div>`);
			return;
		}

		const desajuste =
			!this.solo_anticipo && Math.abs(t.aplicado - t.creditos - t.monto) > TOL
				? `<button class="btn btn-xs btn-warning" id="pg_btn_igualar">
					Usar ${this.fmt(t.aplicado - t.creditos)} como monto pagado</button>`
				: "";

		$tab.html(`
			<div class="pg-card" style="border-radius:0 0 10px 10px;">

				<div style="display:flex; flex-wrap:wrap; gap:14px; align-items:end; margin-bottom:14px;">
					<div>
						<label class="control-label" style="font-size:11px;">Monto pagado al proveedor</label>
						<input type="number" step="0.01" id="pg_monto"
							class="form-control pg-monto-input" style="width:200px;" value="${flt(this.monto)}"${this.dis()}>
					</div>
					<div style="display:flex; align-items:center; gap:6px; padding-bottom:8px;">
						<input type="checkbox" id="pg_chk_anticipo" ${this.solo_anticipo ? "checked" : ""}${this.dis()}>
						<label for="pg_chk_anticipo" style="margin:0; font-size:12px; cursor:pointer; white-space:nowrap;">
							No abonar a facturas — dejar todo como anticipo
						</label>
					</div>
					${this.solo_lectura ? "" : `
					<button class="btn btn-default btn-sm" id="pg_btn_redistribuir">↻ Redistribuir</button>
					<button class="btn btn-default btn-sm" id="pg_btn_limpiar">Limpiar</button>
					${desajuste}`}
				</div>

				${
					this.documentos.length
						? `
				<div style="overflow-x:auto;">
				<table class="table table-bordered table-sm pg-tabla" style="margin:0; font-size:12px;">
					<thead><tr>
						<th style="width:34px;" title="Marcar = pagar esta línea completa"><input type="checkbox" id="pg_chk_todos"${this.dis_tabla()}></th>
						<th>Tipo</th><th>Documento</th><th>Fecha</th>
						<th class="text-right">Valor</th>
						<th class="text-right">Abonado antes</th>
						<th class="text-right">Saldo</th>
						<th class="text-right" style="width:130px;">Abono</th>
						<th class="text-right">Saldo final</th>
					</tr></thead>
					<tbody>${this.documentos.map((d, i) => this.fila(d, i)).join("")}</tbody>
				</table>
				</div>
				<div class="text-muted" style="margin-top:8px; font-size:11px;">
					Marque una línea para pagarla completa, o desmárquela para excluirla del pago.
					También puede escribir el abono exacto en la columna <b>Abono</b>.
					Las filas <b style="color:#0b6b3a;">en verde</b> son créditos a favor de la empresa
					(anticipos y devoluciones): márquelas para usarlas contra los documentos pendientes.
				</div>`
						: `<div class="text-muted" style="padding:20px 0;">
							Este proveedor no tiene documentos con saldo pendiente.
							Puede registrar de todos modos un monto pagado: quedará como anticipo a su favor.
						   </div>`
				}
			</div>
		`);

		this.enlazar_dist();
	}

	fila(d, i) {
		const es_credito = d.clase === "Crédito";
		const marcada = es_credito ? d.aplicar : flt(d.abono) > TOL;
		const enlace =
			d.tipo_documento === TIPO_ANTICIPO
				? `/app/pago-proveedor/${encodeURIComponent(d.documento)}`
				: `/app/${frappe.router.slug(d.tipo_documento)}/${encodeURIComponent(d.documento)}`;

		return `
			<tr class="${es_credito ? "pg-credito" : ""}">
				<td class="text-center"><input type="checkbox" class="pg-chk" data-i="${i}" ${marcada ? "checked" : ""}${this.dis_tabla()}></td>
				<td>${frappe.utils.escape_html(d.etiqueta_tipo || d.tipo_documento)}</td>
				<td><a href="${enlace}" target="_blank">${frappe.utils.escape_html(d.documento)}</a></td>
				<td>${frappe.datetime.str_to_user(d.fecha_documento) || ""}</td>
				<td class="text-right">${this.fmt(d.valor_original)}</td>
				<td class="text-right">${this.fmt(d.abonado_previo)}</td>
				<td class="text-right"><b>${this.fmt(d.saldo_anterior)}</b></td>
				<td class="text-right">
					<input type="number" step="0.01" class="form-control input-sm text-right pg-abono"
						data-i="${i}" value="${flt(d.abono)}" style="height:26px; padding:2px 6px;"${this.dis_tabla()}>
				</td>
				<td class="text-right">${this.fmt(d.saldo)}</td>
			</tr>`;
	}

	enlazar_dist() {
		const $tab = this.$body.find("#pg_tab_dist");

		$tab.find("#pg_monto").on("change", (e) => {
			this.monto = flt($(e.currentTarget).val());
			this.distribuir();
		});

		$tab.find("#pg_btn_redistribuir").on("click", () => {
			this.documentos.forEach((d) => {
				d.manual = false;
				d.abono = 0;
			});
			this.distribuir();
		});
		$tab.find("#pg_btn_limpiar").on("click", () => this.limpiar());
		$tab.find("#pg_chk_anticipo").on("change", (e) => {
			this.solo_anticipo = $(e.currentTarget).is(":checked");
			this.distribuir();
		});
		$tab.find("#pg_btn_igualar").on("click", () => {
			const t = this.totales();
			this.monto = flt(t.aplicado - t.creditos);
			this.distribuir();
		});

		$tab.find("#pg_chk_todos").on("change", (e) => {
			const marcar = $(e.currentTarget).is(":checked");
			this.documentos.forEach((d) => {
				if (d.clase === "Crédito") {
					d.aplicar = marcar;
					d.manual = false;
				} else {
					d.manual = true;
					d.abono = marcar ? flt(d.saldo_anterior) : 0;
				}
			});
			this.distribuir();
		});

		$tab.find(".pg-chk").on("change", (e) => {
			const i = parseInt($(e.currentTarget).data("i"));
			const marcada = $(e.currentTarget).is(":checked");
			const d = this.documentos[i];

			if (d.clase === "Crédito") {
				d.aplicar = marcada;
				d.manual = false;
			} else {
				d.manual = true;
				d.abono = marcada ? flt(d.saldo_anterior) : 0;
			}
			this.distribuir();
		});

		$tab.find(".pg-abono").on("change", (e) => {
			const i = parseInt($(e.currentTarget).data("i"));
			const d = this.documentos[i];
			let valor = flt($(e.currentTarget).val());
			if (d.clase === "Crédito") {
				valor = -Math.abs(valor);
				d.aplicar = Math.abs(valor) > TOL;
			}
			d.abono = valor;
			d.manual = true;
			this.distribuir();
		});
	}

	// ==================================================================
	// Formas de pago
	// ==================================================================
	autocompletar_cuentas() {
		const unica_efectivo = this.cuenta_unica("Efectivo");
		if (unica_efectivo) this.formas.efectivo.cuenta_contable = unica_efectivo;

		const mapa = { transferencia: "Transferencia", cheque: "Cheque", retencion: "Retención", tarjeta: "Tarjeta de Crédito" };
		Object.keys(mapa).forEach((clave) => {
			const unica = this.cuenta_unica(mapa[clave]);
			if (!unica) return;
			this.formas[clave].forEach((f) => {
				if (!f.cuenta_contable) f.cuenta_contable = unica;
			});
		});
	}

	render_formas() {
		const $tab = this.$body.find("#pg_tab_formas");
		this.autocompletar_cuentas();
		const t = this.totales();
		const dif = flt(t.monto - t.total_formas);

		$tab.html(`
			<div class="pg-card" style="border-radius:0 0 10px 10px; display:flex; flex-direction:column; gap:14px;">

				<div class="alert ${Math.abs(dif) <= TOL ? "alert-success" : "alert-warning"}"
					style="padding:8px 12px; margin:0; display:flex; gap:14px; align-items:center; flex-wrap:wrap;">
					<span>Monto pagado: <b>${this.fmt(t.monto)}</b></span>
					<span>Capturado: <b>${this.fmt(t.total_formas)}</b></span>
					<span>Diferencia: <b>${this.fmt(dif)}</b></span>
					${
						Math.abs(dif) > TOL
							? (this.solo_lectura ? "" : `<button class="btn btn-xs btn-primary" id="pg_btn_efectivo">Completar con efectivo</button>`)
							: "✔ cuadrado"
					}
				</div>

				<div class="pg-seccion">
					<div class="pg-seccion-tit">💵 Efectivo</div>
					<div style="display:flex; gap:10px; align-items:end; flex-wrap:wrap;">
						<div>
							<label class="control-label" style="font-size:11px;">Monto</label>
							<input type="number" step="0.01" id="pg_efectivo_monto" class="form-control input-sm text-right"
								style="width:170px;" value="${flt(this.formas.efectivo.monto)}"${this.dis()}>
						</div>
						<div style="flex:1; min-width:220px;">
							<label class="control-label" style="font-size:11px;">Comentario</label>
							<input type="text" id="pg_efectivo_comentario" class="form-control input-sm"
								value="${frappe.utils.escape_html(this.formas.efectivo.comentario || "")}"${this.dis()}>
						</div>
						${this.celda_cuenta_simple("Efectivo", "pg_efectivo_cuenta", this.formas.efectivo.cuenta_contable)}
					</div>
				</div>

				<div class="pg-seccion">
					<div class="pg-seccion-tit">🏦 Transferencia</div>
					<div id="pg_grid_transferencia"></div>
				</div>

				<div class="pg-seccion">
					<div class="pg-seccion-tit">🧾 Cheque</div>
					<div id="pg_grid_cheque"></div>
				</div>

				<div class="pg-seccion">
					<div class="pg-seccion-tit">📄 Retención</div>
					<div id="pg_grid_retencion"></div>
				</div>

				<div class="pg-seccion">
					<div class="pg-seccion-tit">💳 Tarjeta de Crédito</div>
					<div id="pg_grid_tarjeta"></div>
				</div>

				<div>
					<label class="control-label" style="font-size:11px;">Comentarios del pago</label>
					<input type="text" id="pg_comentarios" class="form-control input-sm"
						placeholder="Referencia interna, quién autoriza el pago, observaciones..."
						value="${frappe.utils.escape_html(this.comentarios || "")}"${this.dis()}>
				</div>
			</div>
		`);

		$tab.find("#pg_btn_efectivo").on("click", () => {
			this.formas.efectivo.monto = flt(flt(this.formas.efectivo.monto) + dif);
			if (this.formas.efectivo.monto < 0) this.formas.efectivo.monto = 0;
			this.render_todo();
		});

		$tab.find("#pg_efectivo_monto").on("change", (e) => {
			this.formas.efectivo.monto = flt($(e.currentTarget).val());
			this.render_resumen();
			this.render_formas();
		});
		$tab.find("#pg_efectivo_comentario").on("change", (e) => {
			this.formas.efectivo.comentario = $(e.currentTarget).val();
		});
		$tab.find("#pg_efectivo_cuenta").on("change", (e) => {
			this.formas.efectivo.cuenta_contable = $(e.currentTarget).val();
		});
		$tab.find("#pg_comentarios").on("change", (e) => {
			this.comentarios = $(e.currentTarget).val();
		});

		const col_gasto = this.ctx.permite_gasto_bancario
			? [{ campo: "gasto_bancario", label: "Gasto bancario", tipo: "number" }]
			: [];

		this.render_grid("transferencia", "#pg_grid_transferencia", [
			{ campo: "no_documento", label: "Referencia", tipo: "text" },
			{ campo: "fecha", label: "Fecha", tipo: "date" },
			{ campo: "monto", label: "Valor", tipo: "number" },
			{ campo: "cuenta_banco", label: "Cuenta banco empresa", tipo: "cuenta" },
			{ campo: "cuenta_contable", label: "Cuenta contable", tipo: "cuenta_contable", forma_tipo: "Transferencia" },
			...col_gasto,
			{ campo: "comentario", label: "Comentarios", tipo: "text" },
		]);

		this.render_grid("cheque", "#pg_grid_cheque", [
			{ campo: "fecha", label: "Fecha", tipo: "date" },
			{ campo: "banco", label: "Banco", tipo: "text" },
			{ campo: "no_documento", label: "No. Cheque", tipo: "text" },
			{ campo: "monto", label: "Valor", tipo: "number" },
			{ campo: "cuenta_banco", label: "Cuenta banco empresa", tipo: "cuenta" },
			{ campo: "cuenta_contable", label: "Cuenta contable", tipo: "cuenta_contable", forma_tipo: "Cheque" },
			...col_gasto,
			{ campo: "comentario", label: "Comentario", tipo: "text" },
		]);

		this.render_grid("retencion", "#pg_grid_retencion", [
			{ campo: "fecha", label: "Fecha", tipo: "date" },
			{ campo: "no_documento", label: "Referencia", tipo: "text" },
			{ campo: "monto", label: "Valor", tipo: "number" },
			{ campo: "cuenta_contable", label: "Cuenta contable", tipo: "cuenta_contable", forma_tipo: "Retención" },
			{ campo: "comentario", label: "Comentario", tipo: "text" },
		]);

		this.render_grid("tarjeta", "#pg_grid_tarjeta", [
			{ campo: "no_documento", label: "Referencia / voucher", tipo: "text" },
			{ campo: "fecha", label: "Fecha", tipo: "date" },
			{ campo: "monto", label: "Valor", tipo: "number" },
			{ campo: "cuenta_banco", label: "Cuenta banco empresa", tipo: "cuenta" },
			{ campo: "cuenta_contable", label: "Cuenta contable", tipo: "cuenta_contable", forma_tipo: "Tarjeta de Crédito" },
			...col_gasto,
			{ campo: "comentario", label: "Comentario", tipo: "text" },
		]);
	}

	celda_cuenta_simple(tipo, id, valor) {
		const opciones = this.opciones_cuenta(tipo);
		if (!opciones.length) return "";

		const unica = this.cuenta_unica(tipo);
		if (unica) {
			return `
				<div>
					<label class="control-label" style="font-size:11px;">Cuenta contable</label>
					<input type="text" class="form-control input-sm" value="${frappe.utils.escape_html(unica)}" disabled>
					<input type="hidden" id="${id}" value="${frappe.utils.escape_html(unica)}">
				</div>
			`;
		}

		const items = opciones
			.map((c) => `<option value="${frappe.utils.escape_html(c)}" ${c === valor ? "selected" : ""}>${frappe.utils.escape_html(c)}</option>`)
			.join("");
		return `
			<div>
				<label class="control-label" style="font-size:11px;">Cuenta contable</label>
				<select class="form-control input-sm" id="${id}" style="width:220px;"${this.dis()}>
					<option value=""></option>${items}
				</select>
			</div>
		`;
	}

	render_grid(clave, selector, columnas) {
		const $cont = this.$body.find(selector);
		const filas = this.formas[clave];

		const cuerpo = filas
			.map((fila, i) => {
				const celdas = columnas
					.map((c) => `<td>${this.input_celda(clave, i, c, fila[c.campo])}</td>`)
					.join("");
				return `<tr>${celdas}<td class="text-center" style="width:40px;">
					${this.solo_lectura ? "" : `<button class="btn btn-xs btn-danger pg-del" data-i="${i}">✕</button>`}</td></tr>`;
			})
			.join("");

		$cont.html(`
			<div style="overflow-x:auto;">
			<table class="table table-bordered table-sm pg-tabla" style="margin:0; font-size:12px;">
				<thead><tr>${columnas.map((c) => `<th>${c.label}</th>`).join("")}<th style="width:40px;"></th></tr></thead>
				<tbody>${cuerpo || `<tr><td colspan="${columnas.length + 1}" class="text-muted text-center">Sin líneas</td></tr>`}</tbody>
			</table>
			</div>
			${this.solo_lectura ? "" : `<button class="btn btn-xs btn-default pg-add" style="margin-top:6px;">+ Agregar línea</button>`}
		`);

		$cont.find(".pg-add").on("click", () => {
			this.formas[clave].push({
				fecha: this.controles.fecha.get_value() || frappe.datetime.get_today(),
				monto: 0,
			});
			this.render_formas();
		});

		$cont.find(".pg-del").on("click", (e) => {
			this.formas[clave].splice(parseInt($(e.currentTarget).data("i")), 1);
			this.render_todo();
		});

		$cont.find(".pg-input").on("change", (e) => {
			const $el = $(e.currentTarget);
			const campo = $el.data("campo");
			const valor = $el.attr("type") === "number" ? flt($el.val()) : $el.val();
			this.formas[clave][parseInt($el.data("i"))][campo] = valor;
			if (campo === "monto") this.render_todo();
		});
	}

	input_celda(clave, i, col, valor) {
		const datos = `data-i="${i}" data-campo="${col.campo}" style="height:26px; padding:2px 6px;"${this.dis()}`;

		if (col.tipo === "cuenta") {
			const opciones = (this.cuentas_banco || [])
				.map(
					(c) =>
						`<option value="${frappe.utils.escape_html(c.name)}" ${c.name === valor ? "selected" : ""}>${frappe.utils.escape_html(c.name)}</option>`
				)
				.join("");
			return `<select class="form-control input-sm pg-input" ${datos}><option value=""></option>${opciones}</select>`;
		}

		if (col.tipo === "cuenta_contable") {
			const opciones = this.opciones_cuenta(col.forma_tipo);
			if (!opciones.length) return "—";
			if (opciones.length === 1) {
				return `<input type="text" class="form-control input-sm" style="height:26px;" value="${frappe.utils.escape_html(opciones[0])}" disabled>
					<input type="hidden" data-i="${i}" data-campo="${col.campo}" class="pg-input" value="${frappe.utils.escape_html(opciones[0])}">`;
			}
			const items = opciones
				.map((c) => `<option value="${frappe.utils.escape_html(c)}" ${c === valor ? "selected" : ""}>${frappe.utils.escape_html(c)}</option>`)
				.join("");
			return `<select class="form-control input-sm pg-input" ${datos}><option value=""></option>${items}</select>`;
		}

		const tipo = col.tipo === "number" ? "number" : col.tipo === "date" ? "date" : "text";
		const clases = `form-control input-sm pg-input${tipo === "number" ? " text-right" : ""}`;
		const extra = tipo === "number" ? 'step="0.01"' : "";
		return `<input type="${tipo}" ${extra} class="${clases}" ${datos} value="${frappe.utils.escape_html(String(valor ?? ""))}">`;
	}

	total_formas() {
		let total = flt(this.formas.efectivo.monto);
		["transferencia", "cheque", "retencion"].forEach((k) => {
			this.formas[k].forEach((f) => (total += flt(f.monto)));
		});
		return flt(total);
	}

	// ==================================================================
	// Guardar / validar
	// ==================================================================
	construir_payload() {
		const formas_pago = [];
		const fecha = this.controles.fecha.get_value();

		if (flt(this.formas.efectivo.monto) > 0) {
			formas_pago.push({
				tipo: "Efectivo",
				forma_pago: this.forma_pago_por_tipo("Efectivo"),
				fecha,
				monto: flt(this.formas.efectivo.monto),
				comentario: this.formas.efectivo.comentario,
				cuenta_contable: this.formas.efectivo.cuenta_contable || "",
			});
		}

		const mapa = { transferencia: "Transferencia", cheque: "Cheque", retencion: "Retención", tarjeta: "Tarjeta de Crédito" };
		Object.keys(mapa).forEach((k) => {
			this.formas[k].forEach((f) => {
				if (flt(f.monto) <= 0) return;
				formas_pago.push({
					tipo: mapa[k],
					forma_pago: this.forma_pago_por_tipo(mapa[k]),
					fecha: f.fecha || fecha,
					monto: flt(f.monto),
					no_documento: f.no_documento || "",
					banco: f.banco || "",
					cuenta_banco: f.cuenta_banco || "",
					cuenta_contable: f.cuenta_contable || "",
					gasto_bancario: flt(f.gasto_bancario),
					comentario: f.comentario || "",
				});
			});
		});

		return {
			name: this.pago_name,
			empresa: this.empresa,
			proveedor: this.controles.proveedor.get_value(),
			fecha,
			moneda: this.moneda,
			tipo_cambio: flt(this.tipo_cambio),
			no_recibo_manual: (this.controles.no_recibo.get_value() || "").trim(),
			monto_recibido: flt(this.monto),
			comentarios: this.comentarios || "",
			cuenta_anticipo: this.cuenta_anticipo || "",
			documentos: this.documentos.map((d) => ({
				tipo_documento: d.tipo_documento,
				documento: d.documento,
				fecha_documento: d.fecha_documento,
				referencia: d.referencia,
				abono: flt(d.abono),
			})),
			formas_pago,
		};
	}

	forma_pago_por_tipo(tipo) {
		const nombres = {
			Efectivo: ["Efectivo", "Cash"],
			Transferencia: ["Transferencia bancaria", "Transferencia", "Bank Draft"],
			Cheque: ["Cheque", "Cheque/Check"],
			Retención: ["Retención", "Retencion"],
			"Tarjeta de Crédito": ["Tarjetas de credito", "Tarjeta de Crédito", "Tarjeta de credito", "Credit Card"],
		};
		const disponibles = (this.ctx.formas_pago || []).map((f) => f.name);
		return (nombres[tipo] || []).find((n) => disponibles.includes(n)) || null;
	}

	guardar(validar) {
		const payload = this.construir_payload();

		if (!payload.proveedor) {
			frappe.msgprint(__("Seleccione un proveedor."));
			return;
		}
		if (!payload.empresa) {
			frappe.msgprint(__("No se pudo determinar la empresa del pago."));
			return;
		}
		if (!payload.no_recibo_manual) {
			frappe.msgprint({
				title: __("Falta el No. de comprobante"),
				message: __("Escriba el No. del comprobante de pago que respalda esta transacción."),
				indicator: "orange",
			});
			this.controles.no_recibo.$input?.focus();
			return;
		}
		if (this.moneda && this.moneda_empresa() && this.moneda !== this.moneda_empresa() && flt(payload.tipo_cambio) <= 0) {
			frappe.msgprint({
				title: __("Falta el tipo de cambio"),
				message: __(
					"Este pago está en {0} y la empresa maneja {1}: indique el tipo de cambio real con el que se realizó.",
					[this.moneda, this.moneda_empresa()]
				),
				indicator: "orange",
			});
			this.controles.tipo_cambio.$input?.focus();
			return;
		}

		const t = this.totales();
		if (Math.abs(t.monto - t.total_formas) > TOL) {
			frappe.msgprint({
				title: __("Las formas de pago no cuadran"),
				message: __(
					"El monto pagado es {0} y las formas de pago capturadas suman {1}. Complete la pestaña 2 antes de continuar.",
					[this.fmt(t.monto), this.fmt(t.total_formas)]
				),
				indicator: "orange",
			});
			this.$body.find('.nav-link[data-tab="formas"]').trigger("click");
			return;
		}

		frappe.call({
			method: "pagos_chappsa.api.pagos_proveedor.guardar_pago",
			args: { payload: JSON.stringify(payload), validar: validar },
			freeze: true,
			freeze_message: validar ? __("Validando pago...") : __("Guardando borrador..."),
			callback: (r) => {
				if (!r.message) return;
				this.pago_name = r.message.docstatus === 0 ? r.message.name : null;

				frappe.show_alert({
					message: validar
						? __("Pago {0} validado", [r.message.name])
						: __("Borrador {0} guardado", [r.message.name]),
					indicator: "green",
				});

				if (validar) {
					this.ofrecer_impresion(r.message.name);
				} else {
					this.render_todo();
				}
			},
		});
	}

	ofrecer_impresion(nombre) {
		const d = new frappe.ui.Dialog({
			title: __("Pago validado"),
			indicator: "green",
			fields: [
				{
					fieldtype: "HTML",
					options: `<div style="font-size:13px; line-height:1.6;">
						Se registró el pago <b>${frappe.utils.escape_html(nombre)}</b>.<br>
						<span class="text-muted">El comprobante sale en media carta.</span>
					</div>`,
				},
			],
			primary_action_label: __("🖨️ Imprimir comprobante"),
			primary_action: () => {
				this.imprimir_recibo(nombre);
				d.hide();
			},
			secondary_action_label: __("Cerrar"),
			secondary_action: () => d.hide(),
		});
		d.$wrapper.on("hidden.bs.modal", () => {
			this.limpiar_todo(true);
			this.refrescar_recientes();
		});
		d.show();
	}

	limpiar() {
		const proveedor = this.controles.proveedor.get_value();
		this.reset_estado();
		if (proveedor) this.cargar_proveedor();
		else this.render_todo();
	}

	limpiar_todo(forzar) {
		const hacer = () => {
			this.reset_estado();
			this.cuentas = [];
			this.documentos = [];
			this.empresa = null;
			this.moneda = null;
			this._cargando = true;
			this.controles.proveedor.set_value("");
			this._cargando = false;
			this.controles.fecha.set_value(frappe.datetime.get_today());
			this.controles.no_recibo.set_value("");
			this.controles.tipo_cambio.set_value("");
			this.render_todo();
			this.actualizar_acciones();
			frappe.show_alert({ message: __("Pantalla limpia"), indicator: "blue" });
		};

		const hay_datos =
			this.documentos.some((d) => flt(d.abono)) || flt(this.monto) || this.total_formas();
		if (hay_datos && !this.solo_lectura && !forzar) {
			frappe.confirm(__("Se perderán los datos capturados que no haya guardado. ¿Continuar?"), hacer);
		} else {
			hacer();
		}
	}

	cargar_pago(name) {
		frappe.call({
			method: "pagos_chappsa.api.pagos_proveedor.get_pago",
			args: { name },
			freeze: true,
			freeze_message: __("Cargando pago..."),
			callback: (r) => {
				if (!r.message) return;
				const d = r.message;

				this.reset_estado();
				this.solo_lectura = !!d.solo_lectura;
				this.estado_pago = d.estado;
				this.puede_imprimir = !!d.puede_imprimir;
				this.pago_name_impreso = d.name;
				this.pago_name = d.docstatus === 0 ? d.name : null;

				this.empresa = d.empresa;
				this.moneda = d.moneda;
				this.nombre_proveedor = d.nombre_proveedor;
				this.cuenta_anticipo = d.cuenta_anticipo || null;
				this.cuentas = d.cuentas || [];
				this.tipo_cambio = flt(d.tipo_cambio) || null;
				this.monto = flt(d.monto_recibido);
				this.comentarios = d.comentarios || "";

				this.documentos = (d.documentos || []).map((x) => ({
					...x,
					abono: flt(x.abono),
					manual: flt(x.abono) !== 0,
					aplicar: x.clase === "Crédito" && flt(x.abono) !== 0,
					saldo: flt(flt(x.saldo_anterior) - flt(x.abono)),
				}));

				// Si el borrador guardado no abonó ningún documento pese a tener
				// documentos pendientes, se guardó a propósito como "solo anticipo".
				// Se restaura el checkbox para que no se auto-distribuya al reabrirlo.
				this.solo_anticipo =
					this.documentos.length > 0 &&
					flt(this.monto) > TOL &&
					this.documentos.every((x) => Math.abs(flt(x.abono)) <= TOL);

				this.formas = { efectivo: { monto: 0, comentario: "" }, transferencia: [], cheque: [], retencion: [], tarjeta: [] };
				(d.formas_pago || []).forEach((f) => {
					if (f.tipo === "Efectivo") {
						this.formas.efectivo.monto = flt(this.formas.efectivo.monto) + flt(f.monto);
						this.formas.efectivo.comentario = f.comentario || this.formas.efectivo.comentario;
						this.formas.efectivo.cuenta_contable = f.cuenta_contable || this.formas.efectivo.cuenta_contable;
					} else {
						const clave = {
							Transferencia: "transferencia",
							Cheque: "cheque",
							"Retención": "retencion",
							"Tarjeta de Crédito": "tarjeta",
						}[f.tipo];
						if (clave) this.formas[clave].push({ ...f });
					}
				});

				this._cargando = true;
				const terminar = () => {
					this._cargando = false;
					this._proveedor_cargado = d.proveedor;
					this.cargar_cuentas_banco();
					this.render_todo();
					this.actualizar_acciones();
					this.$body.find('.nav-link[data-tab="dist"]').trigger("click");
				};

				Promise.resolve(this.controles.proveedor.set_value(d.proveedor))
					.then(() => this.controles.fecha.set_value(d.fecha))
					.then(() => this.controles.no_recibo.set_value(d.no_recibo_manual || ""))
					.then(() => this.controles.tipo_cambio.set_value(this.tipo_cambio || ""))
					.then(terminar, terminar);
			},
		});
	}

	// ==================================================================
	// Pagos recientes
	// ==================================================================
	refrescar_recientes() {
		frappe.call({
			method: "pagos_chappsa.api.pagos_proveedor.get_pagos_recientes",
			args: { proveedor: this.controles?.proveedor?.get_value() || null, limite: 25 },
			callback: (r) => this.render_recientes(r.message || []),
		});
	}

	render_recientes(filas) {
		const perfil = this.ctx.perfil || {};
		const puede_reimprimir = perfil.permitir_reimpresion_recibo !== 0;

		const cuerpo = filas
			.map(
				(p) => `
			<tr class="pg-fila-pago" data-pago="${frappe.utils.escape_html(p.name)}"
			    title="Clic para abrir este pago en la pantalla">
				<td><b>${frappe.utils.escape_html(p.name)}</b></td>
				<td>${frappe.utils.escape_html(p.no_recibo_manual || "")}</td>
				<td>${frappe.datetime.str_to_user(p.fecha) || ""}</td>
				<td>${frappe.utils.escape_html(p.nombre_proveedor || p.proveedor || "")}</td>
				<td>${frappe.utils.escape_html(p.nombre_cobrador || p.cobrador || "")}</td>
				<td class="text-right">${format_currency(p.monto_recibido, p.moneda)}</td>
				<td class="text-right">${format_currency(p.total_aplicado, p.moneda)}</td>
				<td class="text-right" style="${flt(p.anticipo_disponible) > 0 ? "color:#0b6b3a; font-weight:600;" : ""}">${format_currency(p.anticipo_disponible, p.moneda)}</td>
				<td><span class="indicator-pill ${p.docstatus === 1 ? "green" : p.docstatus === 2 ? "red" : "orange"}">${frappe.utils.escape_html(p.estado || "")}</span></td>
				<td class="text-center" style="white-space:nowrap;">
					${
						p.docstatus === 1 && puede_reimprimir
							? `<button class="btn btn-xs btn-default pg-imprimir" data-pago="${frappe.utils.escape_html(p.name)}"
							     title="Imprimir comprobante">🖨️</button>`
							: ""
					}
					${
						p.docstatus !== 0
							? `<button class="btn btn-xs btn-default pg-ver-pe" data-pago="${frappe.utils.escape_html(p.name)}"
							     title="Ver entrada de pago en ERPNext">🔗</button>`
							: `<span class="text-muted" title="Solo los pagos validados o cancelados tienen entrada de pago">—</span>`
					}
				</td>
			</tr>`
			)
			.join("");

		const $tab = this.$body.find("#pg_tab_recientes");
		$tab.html(`
			<div class="pg-card" style="border-radius:0 0 10px 10px; overflow-x:auto;">
				<div class="text-muted" style="font-size:11px; margin-bottom:8px;">
					Haga clic en una fila para abrirla. Los <b>borradores</b> se pueden seguir editando;
					los <b>validados</b> se abren en solo lectura. El comprobante solo existe para pagos validados.
				</div>
				<table class="table table-bordered table-sm pg-tabla" style="margin:0; font-size:12px;">
					<thead><tr>
						<th>Pago</th><th>No. comprobante</th><th>Fecha</th><th>Proveedor</th><th>Registrado por</th>
						<th class="text-right">Pagado</th><th class="text-right">Aplicado</th>
						<th class="text-right">Anticipo disp.</th><th>Estado</th><th style="width:70px;">Acciones</th>
					</tr></thead>
					<tbody>${cuerpo || `<tr><td colspan="10" class="text-muted text-center">Sin pagos</td></tr>`}</tbody>
				</table>
			</div>
		`);

		$tab.find(".pg-imprimir").on("click", (e) => {
			e.stopPropagation();
			this.imprimir_recibo($(e.currentTarget).data("pago"));
		});

		$tab.find(".pg-ver-pe").on("click", (e) => {
			e.stopPropagation();
			this.ver_entrada_pago($(e.currentTarget).data("pago"));
		});

		$tab.find(".pg-fila-pago").on("click", (e) => {
			this.cargar_pago($(e.currentTarget).data("pago"));
		});
	}
}

function precision_moneda() {
	return frappe.boot?.sysdefaults?.currency_precision || 2;
}
