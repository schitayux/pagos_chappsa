// Copyright (c) 2026, Josue Velasquez and contributors
// For license information, please see license.txt

frappe.query_reports["Pagos a Proveedores por Empleado y Dia"] = {
	filters: [
		{
			fieldname: "desde", label: __("Desde"), fieldtype: "Date",
			default: frappe.datetime.month_start(), reqd: 1,
		},
		{
			fieldname: "hasta", label: __("Hasta"), fieldtype: "Date",
			default: frappe.datetime.get_today(), reqd: 1,
		},
		{ fieldname: "cobrador", label: __("Registrado por"), fieldtype: "Link", options: "User" },
		{ fieldname: "empresa", label: __("Empresa"), fieldtype: "Link", options: "Company" },
		{ fieldname: "moneda", label: __("Moneda"), fieldtype: "Select", options: [""] },
		{
			fieldname: "solo_borradores", label: __("Ver borradores en vez de validados"),
			fieldtype: "Check", default: 0,
		},
	],

	onload() {
		frappe.call({ method: "pagos_chappsa.api.pagos_proveedor.get_monedas_con_documentos" }).then((r) => {
			const filtro = frappe.query_report.get_filter("moneda");
			if (!filtro || !r.message) return;
			filtro.df.options = [""].concat(r.message).join("\n");
			filtro.refresh();
		});
	},
};
