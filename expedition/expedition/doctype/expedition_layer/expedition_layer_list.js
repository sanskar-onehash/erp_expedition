frappe.listview_settings["Expedition Layer"] = {
	add_fields: [
		"title",
		"map",
		"enabled",
		"source_doctype",
		"location_source",
		"location_doctype",
		"linked_metrics_json",
		"linked_metric_filters_json",
		"heatmap",
		"heatmap_mode",
		"cluster",
	],
	get_indicator(doc) {
		if (!doc.enabled) {
			return [__("Disabled"), "gray", "enabled,=,0"];
		}
		if (!doc.map) {
			return [__("Master"), "blue", "map,=,"];
		}
		if (doc.linked_metrics_json && doc.linked_metric_filters_json) {
			return [__("Filtered Money"), "orange", "linked_metric_filters_json,is,set"];
		}
		if (doc.linked_metrics_json) {
			return [__("Money"), "green", "linked_metrics_json,is,set"];
		}
		if (doc.heatmap) {
			return [__("Heatmap"), "orange", "heatmap,=,1"];
		}
		if (doc.location_source && doc.location_source !== "Direct Fields") {
			return [__("Linked Location"), "green", "location_source,!=,Direct Fields"];
		}
		return [__("Enabled"), "green", "enabled,=,1"];
	},
};
