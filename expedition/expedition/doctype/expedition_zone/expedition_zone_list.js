frappe.listview_settings["Expedition Zone"] = {
	add_fields: ["title", "map", "zone_type", "tag", "color"],
	get_indicator(doc) {
		if (doc.tag) {
			return [doc.tag, "blue", `tag,=,${doc.tag}`];
		}
		if (doc.zone_type === "circle") {
			return [__("Circle"), "orange", "zone_type,=,circle"];
		}
		return [__("Polygon"), "green", "zone_type,=,polygon"];
	},
};
