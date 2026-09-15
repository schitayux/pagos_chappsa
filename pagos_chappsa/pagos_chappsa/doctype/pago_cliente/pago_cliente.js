// Copyright (c) 2026, Josue Velasquez and contributors
// For license information, please see license.txt

frappe.ui.form.on("Pago Cliente", {
	refresh(frm) {
		if (!frm.is_new()) {
			frm.add_custom_button(__("Abrir panel de Pagos"), () => {
				frappe.set_route("pagos");
			});
		}

		if (frm.doc.docstatus === 1 && flt(frm.doc.anticipo_disponible) > 0) {
			frm.dashboard.add_indicator(
				__("Anticipo disponible: {0}", [
					format_currency(frm.doc.anticipo_disponible, frm.doc.moneda),
				]),
				"green"
			);
		}
	},

	cliente(frm) {
		if (!frm.doc.cliente || frm.doc.moneda) return;
		frappe.db.get_value("Customer", frm.doc.cliente, "default_currency").then((r) => {
			if (r.message && r.message.default_currency) {
				frm.set_value("moneda", r.message.default_currency);
			}
		});
	},
});
