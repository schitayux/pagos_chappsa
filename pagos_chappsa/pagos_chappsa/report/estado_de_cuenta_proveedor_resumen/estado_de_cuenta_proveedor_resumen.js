// Copyright (c) 2026, Josue Velasquez and contributors
// For license information, please see license.txt

frappe.query_reports["Estado de Cuenta Proveedor Resumen"] = {
	filters: [
		{ fieldname: "proveedor", label: __("Proveedor"), fieldtype: "Link", options: "Supplier" },
		{ fieldname: "empresa", label: __("Empresa"), fieldtype: "Link", options: "Company" },
		{ fieldname: "moneda", label: __("Moneda"), fieldtype: "Select", options: [""] },
		{ fieldname: "hasta", label: __("Saldos al"), fieldtype: "Date", default: frappe.datetime.get_today() },
		{ fieldname: "incluir_saldados", label: __("Incluir saldados"), fieldtype: "Check", default: 0 },
	],
	onload(report) {
		frappe.call({ method: "pagos_chappsa.api.pagos_proveedor.get_monedas_con_documentos" }).then((r) => {
			const filtro = frappe.query_report.get_filter("moneda");
			if (!filtro || !r.message) return;
			filtro.df.options = [""].concat(r.message).join("\n");
			filtro.refresh();
		});

		report.page.add_inner_button(__("🖨️ Imprimir estado de cuenta"), () => {
			const f = frappe.query_report.get_filter_values();
			const p = new URLSearchParams({
				tipo: "resumen",
				proveedor: f.proveedor || "",
				empresa: f.empresa || "",
				moneda: f.moneda || "",
				hasta: f.hasta || "",
				incluir_saldados: f.incluir_saldados ? "1" : "0",
			});
			window.open("/estado_cuenta_proveedor?" + p.toString(), "_blank");
		});
	},
};
