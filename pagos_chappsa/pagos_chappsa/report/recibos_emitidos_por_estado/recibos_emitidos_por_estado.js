// Copyright (c) 2026, Josue Velasquez and contributors
// For license information, please see license.txt

frappe.query_reports["Recibos Emitidos por Estado"] = {
	filters: [
		{ fieldname: "cliente", label: __("Cliente"), fieldtype: "Link", options: "Customer" },
		{
			fieldname: "desde", label: __("Desde"), fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
		},
		{ fieldname: "hasta", label: __("Hasta"), fieldtype: "Date", default: frappe.datetime.get_today() },
		{
			fieldname: "estado", label: __("Estado"), fieldtype: "Select",
			options: ["", "Borrador", "Validado", "Cancelado"].join("\n"),
			default: "",
		},
		{ fieldname: "empresa", label: __("Empresa"), fieldtype: "Link", options: "Company" },
		{ fieldname: "cobrador", label: __("Cobrador"), fieldtype: "Link", options: "User" },
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "estado" && data) {
			const color = { Validado: "green", Borrador: "orange", Cancelado: "red" }[data.estado] || "gray";
			value = `<span class="indicator-pill ${color}">${data.estado || ""}</span>`;
		}
		return value;
	},
};
