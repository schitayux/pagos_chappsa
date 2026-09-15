// Copyright (c) 2026, Josue Velasquez and contributors
// For license information, please see license.txt

/**
 * Filtros compartidos por los dos estados de cuenta.
 * La moneda es un Select, no un Link: el catálogo de Frappe trae 148 monedas y aquí
 * solo se usan tres. Las opciones se cargan en onload desde el servidor.
 */
const filtros_estado_cuenta = () => [
	{ fieldname: "cliente", label: __("Cliente"), fieldtype: "Link", options: "Customer" },
	{ fieldname: "empresa", label: __("Empresa"), fieldtype: "Link", options: "Company" },
	{ fieldname: "moneda", label: __("Moneda"), fieldtype: "Select", options: [""] },
	{ fieldname: "hasta", label: __("Saldos al"), fieldtype: "Date", default: frappe.datetime.get_today() },
	{ fieldname: "incluir_saldados", label: __("Incluir saldados"), fieldtype: "Check", default: 0 },
];

/** Deja en el filtro de moneda solo las que tienen documentos registrados. */
function cargar_monedas() {
	frappe.call({ method: "pagos_chappsa.api.pagos.get_monedas_con_documentos" }).then((r) => {
		const filtro = frappe.query_report.get_filter("moneda");
		if (!filtro || !r.message) return;
		filtro.df.options = [""].concat(r.message).join("\n");
		filtro.refresh();
	});
}

/** Abre la versión imprimible, con el logo de la empresa filtrada. */
function abrir_impresion(tipo) {
	const f = frappe.query_report.get_filter_values();
	const p = new URLSearchParams({
		tipo,
		cliente: f.cliente || "",
		empresa: f.empresa || "",
		moneda: f.moneda || "",
		hasta: f.hasta || "",
		incluir_saldados: f.incluir_saldados ? "1" : "0",
	});
	window.open("/estado_cuenta?" + p.toString(), "_blank");
}

frappe.query_reports["Estado de Cuenta Resumen"] = {
	filters: filtros_estado_cuenta(),
	onload(report) {
		cargar_monedas();
		report.page.add_inner_button(__("🖨️ Imprimir estado de cuenta"), () => abrir_impresion("resumen"));
	},
};
