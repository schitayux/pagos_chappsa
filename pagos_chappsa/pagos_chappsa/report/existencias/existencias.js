// Copyright (c) 2026, Josue Velasquez and contributors
// For license information, please see license.txt

frappe.query_reports["Existencias"] = {
	filters: [
		{
			fieldname: "almacen", label: __("Almacén"), fieldtype: "Link", options: "Warehouse",
			get_query: () => ({
				query: "pagos_chappsa.pagos_chappsa.report.existencias.existencias.bodegas_query",
			}),
		},
		{ fieldname: "item_code", label: __("Código"), fieldtype: "Link", options: "Item" },
		{ fieldname: "buscar", label: __("Buscar código / descripción"), fieldtype: "Data" },
		{ fieldname: "solo_con_existencia", label: __("Solo con existencia"), fieldtype: "Check", default: 1 },
	],
};
