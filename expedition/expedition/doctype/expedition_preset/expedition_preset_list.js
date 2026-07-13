frappe.listview_settings["Expedition Preset"] = {
	add_fields: ["title", "category", "is_system", "color", "icon", "size"],
	get_indicator(doc) {
		if (doc.is_system) {
			return [__("System"), "blue", "is_system,=,1"];
		}
		if (doc.category) {
			return [doc.category, "green", `category,=,${doc.category}`];
		}
		return [__("Custom"), "gray", "is_system,=,0"];
	},
};
