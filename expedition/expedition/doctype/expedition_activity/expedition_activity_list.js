frappe.listview_settings["Expedition Activity"] = {
	add_fields: ["activity_type", "user", "map", "related_doctype", "related_name", "outcome", "occurred_at"],
	get_indicator(doc) {
		const outcome = doc.outcome || "";
		if (outcome === "successful") {
			return [__("Successful"), "green", "outcome,=,successful"];
		}
		if (outcome === "unsuccessful") {
			return [__("Unsuccessful"), "red", "outcome,=,unsuccessful"];
		}
		if (outcome === "rescheduled") {
			return [__("Rescheduled"), "orange", "outcome,=,rescheduled"];
		}
		return [__(doc.activity_type || "Activity"), "blue", `activity_type,=,${doc.activity_type || ""}`];
	},
};
