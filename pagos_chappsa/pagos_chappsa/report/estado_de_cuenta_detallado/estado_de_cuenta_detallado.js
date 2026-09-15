// Copyright (c) 2026, Josue Velasquez and contributors
// For license information, please see license.txt

frappe.query_reports["Estado de Cuenta Detallado"] = {
	filters: [
		{ fieldname: "cliente", label: __("Cliente"), fieldtype: "Link", options: "Customer" },
		{ fieldname: "empresa", label: __("Empresa"), fieldtype: "Link", options: "Company" },
		// Select y no Link: solo las monedas con documentos, no las 148 del catálogo.
		{ fieldname: "moneda", label: __("Moneda"), fieldtype: "Select", options: [""] },
		{ fieldname: "hasta", label: __("Saldos al"), fieldtype: "Date", default: frappe.datetime.get_today() },
		{ fieldname: "incluir_saldados", label: __("Incluir saldados"), fieldtype: "Check", default: 0 },
	],

	onload(report) {
		frappe.call({ method: "pagos_chappsa.api.pagos.get_monedas_con_documentos" }).then((r) => {
			const filtro = frappe.query_report.get_filter("moneda");
			if (!filtro || !r.message) return;
			filtro.df.options = [""].concat(r.message).join("\n");
			filtro.refresh();
		});

		report.page.add_inner_button(__("🖨️ Imprimir estado de cuenta"), () => {
			const f = frappe.query_report.get_filter_values();
			const p = new URLSearchParams({
				tipo: "detallado",
				cliente: f.cliente || "",
				empresa: f.empresa || "",
				moneda: f.moneda || "",
				hasta: f.hasta || "",
				incluir_saldados: f.incluir_saldados ? "1" : "0",
			});
			window.open("/estado_cuenta?" + p.toString(), "_blank");
		});
	},

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		// Los anticipos son dinero a favor del cliente: se marcan en verde.
		if (data && data.clase === "Anticipo") {
			value = `<span style="color:#0b6b3a; font-weight:600;">${value}</span>`;
		}
		return value;
	},
};
