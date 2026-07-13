frappe.listview_settings["Expedition Insight"] = {
	add_fields: ["map", "insight_type", "severity", "is_active", "linked_doctype", "computed_at"],
	get_indicator(doc) {
		if (!doc.is_active) {
			return [__("Expired"), "gray", "is_active,=,0"];
		}
		if (doc.severity === "high") {
			return [__("High"), "red", "severity,=,high"];
		}
		if (doc.severity === "medium") {
			return [__("Medium"), "orange", "severity,=,medium"];
		}
		if (doc.insight_type === "linked_money") {
			return [__("Money"), "green", "insight_type,=,linked_money"];
		}
		return [__("Active"), "blue", "is_active,=,1"];
	},
};
