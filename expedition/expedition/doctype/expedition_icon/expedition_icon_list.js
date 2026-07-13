frappe.listview_settings["Expedition Icon"] = {
	add_fields: ["title", "icon_key", "icon_format", "scope", "owner_user", "is_active"],
	get_indicator(doc) {
		if (!doc.is_active) {
			return [__("Inactive"), "gray", "is_active,=,0"];
		}
		if (doc.scope === "Global") {
			return [__("Global"), "blue", "scope,=,Global"];
		}
		if (doc.icon_format === "Image") {
			return [__("Image"), "orange", "icon_format,=,Image"];
		}
		return [__("Personal"), "green", "scope,=,Personal"];
	},
};
