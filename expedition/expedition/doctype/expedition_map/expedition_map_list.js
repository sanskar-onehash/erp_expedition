frappe.listview_settings["Expedition Map"] = {
	add_fields: ["title", "is_template", "is_public", "public_access", "last_opened_at"],
	get_indicator(doc) {
		if (doc.is_template) {
			return [__("Template"), "blue", "is_template,=,1"];
		}
		if (doc.is_public) {
			return [
				doc.public_access === "Writable" ? __("Public Writable") : __("Public"),
				doc.public_access === "Writable" ? "orange" : "green",
				"is_public,=,1",
			];
		}
		return [__("Private"), "gray", "is_public,=,0"];
	},
	onload(listview) {
		listview.page.add_inner_button(__("Open Canvas"), () => {
			window.open("/expedition", "_blank");
		});
	},
};
