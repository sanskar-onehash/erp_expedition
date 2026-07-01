(function () {
	const FILTER_TABLE_FIELD = "filters";
	const FILTER_JSON_FIELD = "filter_json";
	const ICON_FIELD = "icon";
	const SOURCE_FIELD_PICKERS = {
		latitude_field: ["Float"],
		longitude_field: ["Float"],
		label_field: [],
		radius_field: ["Int", "Float", "Currency", "Percent"],
		group_by_field: [],
		heatmap_weight_field: ["Int", "Float", "Currency", "Percent"],
	};
	const BUILTIN_ICONS = [
		"pin-marker",
		"pin-dot",
		"pin-star",
		"pin-square",
		"pin-diamond",
		"pin-triangle",
		"pin-flag",
		"pin-crosshair",
		"pin-building",
		"pin-home",
		"pin-cart",
		"pin-truck",
		"pin-warn",
		"pin-check",
		"pin-x",
		"pin-dollar",
	];

	const CHILD_OPERATOR_LABELS = {
		"=": "equals",
		"==": "equals",
		"!=": "not equals",
		like: "like",
		"not like": "not like",
		in: "in",
		"not in": "not in",
		">": "greater than",
		">=": "greater or equal",
		"<": "less than",
		"<=": "less or equal",
		between: "between",
		Between: "between",
		is: "is",
	};

	const CANONICAL_OPERATORS = {
		equals: "=",
		equal: "=",
		"not equals": "!=",
		"not equal": "!=",
		"is not": "!=",
		"greater than": ">",
		"greater or equal": ">=",
		"greater than or equal": ">=",
		"less than": "<",
		"less or equal": "<=",
		"less than or equal": "<=",
		contains: "like",
		"not contains": "not like",
		Between: "between",
		between: "between",
		Timespan: "timespan",
		timespan: "timespan",
		"is set": "is",
		"is not set": "is",
	};

	const UNSUPPORTED_OPERATORS = new Set([
		"timespan",
		"descendants of",
		"descendants of (inclusive)",
		"not descendants of",
		"ancestors of",
		"not ancestors of",
	]);

	frappe.ui.form.on("Expedition Layer", {
		refresh(frm) {
			setup_icon_field(frm);
			setup_source_field_pickers(frm);
			render_filters_table(frm);
		},

		source_doctype(frm) {
			clear_stored_filters(frm);
			setup_source_field_pickers(frm);
			render_filters_table(frm);
		},

		icon(frm) {
			render_icon_preview(frm);
		},

		before_save(frm) {
			sync_child_table_from_json(frm);
		},
	});

	function setup_icon_field(frm) {
		const field = frm.fields_dict[ICON_FIELD];
		if (!field || !field.$wrapper) return;

		attach_awesomplete(field, get_icon_suggestions().map((icon) => ({ label: icon.label, value: icon.key })));

		const group = field.$wrapper.find(".control-input");
		if (group.length && !group.find(".expedition-icon-picker-btn").length) {
			$(`<button class="btn btn-xs btn-default expedition-icon-picker-btn" type="button" style="margin-top: 6px;">
				${__("Choose Icon")}
			</button>`)
				.appendTo(group)
				.on("click", () => open_icon_picker(frm));
		}

		render_icon_preview(frm);
		refresh_custom_icon_suggestions(frm);
	}

	function render_icon_preview(frm) {
		const field = frm.fields_dict[ICON_FIELD];
		if (!field || !field.$wrapper) return;

		let preview = field.$wrapper.find(".expedition-icon-preview");
		if (!preview.length) {
			preview = $(`<div class="expedition-icon-preview text-muted small" style="margin-top: 8px;"></div>`);
			field.$wrapper.find(".control-input").append(preview);
		}

		const icon_key = frm.doc[ICON_FIELD];
		if (!icon_key) {
			preview.html(__("Choose from available pin icons instead of typing the icon name."));
			return;
		}

		const custom = get_cached_custom_icons().find((icon) => icon.key === icon_key);
		if (custom) {
			preview.html(
				`<span style="display:inline-flex;align-items:center;gap:8px;">
					${custom_icon_preview_html(custom)}
					<span>${frappe.utils.escape_html(custom.title || custom.key)}</span>
				</span>`
			);
			return;
		}

		if (BUILTIN_ICONS.includes(icon_key)) {
			preview.html(
				`<span style="display:inline-flex;align-items:center;gap:8px;">
					${builtin_icon_preview_html(icon_key)}
					<span>${frappe.utils.escape_html(icon_key)}</span>
				</span>`
			);
			return;
		}

		preview.html(
			`<span class="text-warning">${__("Unknown icon key: {0}", [
				frappe.utils.escape_html(icon_key),
			])}</span>`
		);
	}

	function open_icon_picker(frm) {
		refresh_custom_icon_suggestions(frm).then(() => {
			const icons = get_icon_suggestions();
			const dialog = new frappe.ui.Dialog({
				title: __("Choose Pin Icon"),
				fields: [
					{
						fieldtype: "Data",
						fieldname: "search",
						label: __("Search"),
					},
					{
						fieldtype: "HTML",
						fieldname: "icons",
					},
				],
			});
			dialog.show();

			const wrapper = dialog.get_field("icons").$wrapper;
			const search = dialog.get_field("search").$input;

			function render(query) {
				const q = String(query || "").toLowerCase();
				const visible = icons.filter(
					(icon) =>
						!q ||
						icon.key.toLowerCase().includes(q) ||
						String(icon.title || "").toLowerCase().includes(q)
				);
				wrapper.empty();
				const grid = $(`<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px;max-height:420px;overflow:auto;padding-top:8px;"></div>`).appendTo(wrapper);
				visible.forEach((icon) => {
					const item = $(`<button type="button" class="btn btn-default" style="height:74px;text-align:left;display:flex;align-items:center;gap:10px;white-space:normal;">
						${icon.source === "custom" ? custom_icon_preview_html(icon) : builtin_icon_preview_html(icon.key)}
						<span style="min-width:0;">
							<span style="display:block;font-weight:600;overflow:hidden;text-overflow:ellipsis;">${frappe.utils.escape_html(icon.title || icon.label)}</span>
						</span>
					</button>`);
					item.appendTo(grid).on("click", () => {
						frm.set_value(ICON_FIELD, icon.key);
						dialog.hide();
					});
				});
				if (!visible.length) {
					$(`<div class="text-muted text-center" style="padding:24px;">${__("No icons found")}</div>`).appendTo(wrapper);
				}
			}

			search.on("input", () => render(search.val()));
			render("");
		});
	}

	function setup_source_field_pickers(frm) {
		Object.keys(SOURCE_FIELD_PICKERS).forEach((fieldname) => setup_source_field_picker(frm, fieldname));
	}

	function setup_source_field_picker(frm, fieldname) {
		const field = frm.fields_dict[fieldname];
		if (!field || !field.$wrapper) return;

		const allowed_types = SOURCE_FIELD_PICKERS[fieldname];
		get_source_field_options(frm, allowed_types).then((options) => {
			attach_awesomplete(field, options.map((option) => ({ label: option.label, value: option.value })));
		});

		const group = field.$wrapper.find(".control-input");
		if (group.length && !group.find(`.expedition-field-picker-btn[data-fieldname="${fieldname}"]`).length) {
			$(`<button class="btn btn-xs btn-default expedition-field-picker-btn" type="button" data-fieldname="${fieldname}" style="margin-top: 6px;">
				${__("Pick Field")}
			</button>`)
				.appendTo(group)
				.on("click", () => open_source_field_picker(frm, fieldname, allowed_types));
		}
	}

	function open_source_field_picker(frm, target_fieldname, allowed_types) {
		if (!frm.doc.source_doctype) {
			frappe.msgprint(__("Choose a Source DocType first."));
			return;
		}

		get_source_field_options(frm, allowed_types).then((options) => {
			const dialog = new frappe.ui.Dialog({
				title: __("Choose Field"),
				fields: [
					{
						fieldtype: "Data",
						fieldname: "search",
						label: __("Search"),
					},
					{
						fieldtype: "HTML",
						fieldname: "fields",
					},
				],
			});
			dialog.show();

			const wrapper = dialog.get_field("fields").$wrapper;
			const search = dialog.get_field("search").$input;

			function render(query) {
				const q = String(query || "").toLowerCase();
				const visible = options.filter(
					(option) =>
						!q ||
						option.value.toLowerCase().includes(q) ||
						String(option.label || "").toLowerCase().includes(q) ||
						String(option.fieldtype || "").toLowerCase().includes(q)
				);
				wrapper.empty();
				const list = $(`<div class="list-group" style="max-height:420px;overflow:auto;margin-top:8px;"></div>`).appendTo(wrapper);
				visible.forEach((option) => {
					const item = $(`<button type="button" class="list-group-item" style="text-align:left;">
						<div style="font-weight:600;">${frappe.utils.escape_html(option.label)}</div>
						<div class="text-muted small">${frappe.utils.escape_html(option.value)} · ${frappe.utils.escape_html(option.fieldtype)}</div>
					</button>`);
					item.appendTo(list).on("click", () => {
						frm.set_value(target_fieldname, option.value);
						dialog.hide();
					});
				});
				if (!visible.length) {
					$(`<div class="text-muted text-center" style="padding:24px;">${__("No fields found")}</div>`).appendTo(wrapper);
				}
			}

			search.on("input", () => render(search.val()));
			render("");
		});
	}

	function ensure_filter_group() {
		if (frappe.ui && frappe.ui.FilterGroup) return Promise.resolve();
		return frappe.require("list.bundle.js");
	}

	function get_icon_suggestions() {
		const builtins = BUILTIN_ICONS.map((key) => ({
			key,
			label: key,
			title: key,
			source: "builtin",
		}));
		return builtins.concat(get_cached_custom_icons());
	}

	function get_cached_custom_icons() {
		return frappe.boot.expedition_custom_icons || [];
	}

	function refresh_custom_icon_suggestions(frm) {
		if (frm.expedition_icons_loaded) return Promise.resolve(get_cached_custom_icons());
		frm.expedition_icons_loaded = true;
		return frappe
			.call("expedition.api.icon.list_icons")
			.then((r) => {
				frappe.boot.expedition_custom_icons = ((r.message && r.message.custom) || []).map((icon) => ({
					key: icon.key,
					label: icon.title ? `${icon.title} (${icon.key})` : icon.key,
					title: icon.title || icon.key,
					source: "custom",
					icon_format: icon.icon_format,
					svg_content: icon.svg_content,
					image_data_url: icon.image_data_url,
				}));
				setup_icon_field(frm);
				return get_cached_custom_icons();
			})
			.catch(() => get_cached_custom_icons());
	}

	function builtin_icon_preview_html(key) {
		const safe_key = frappe.utils.escape_html(key);
		return `<span style="width:34px;height:34px;border-radius:17px;color:var(--gray-900);display:inline-flex;align-items:center;justify-content:center;flex:0 0 auto;">
			<svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true">
				<use href="/assets/expedition/icons.svg#${safe_key}"></use>
			</svg>
		</span>`;
	}

	function custom_icon_preview_html(icon) {
		const box_style =
			"width:34px;height:34px;border-radius:17px;color:var(--gray-900);display:inline-flex;align-items:center;justify-content:center;overflow:hidden;flex:0 0 auto;";
		if (icon.image_data_url) {
			return `<span style="${box_style}"><img alt="" src="${frappe.utils.escape_html(
				icon.image_data_url
			)}" style="width:20px;height:20px;object-fit:contain;"></span>`;
		}
		if (icon.svg_content) {
			return `<span style="${box_style}">${icon.svg_content}</span>`;
		}
		return `<span style="${box_style}">?</span>`;
	}

	function get_source_field_options(frm, allowed_types) {
		if (!frm.doc.source_doctype) return Promise.resolve([]);
		return new Promise((resolve) => {
			frappe.model.with_doctype(frm.doc.source_doctype, () => {
				const meta = frappe.get_meta(frm.doc.source_doctype);
				const allowed = new Set(allowed_types || []);
				const fields = (meta.fields || [])
					.filter((df) => df.fieldname && !df.hidden)
					.filter((df) => !allowed.size || allowed.has(df.fieldtype))
					.map((df) => ({
						value: df.fieldname,
						label: df.label || df.fieldname,
						fieldtype: df.fieldtype || "",
					}));
				resolve(fields);
			});
		});
	}

	function attach_awesomplete(field, options) {
		if (!field || !field.$input || !field.$input.length) return;
		const input = field.$input.get(0);
		if (!input || !window.Awesomplete) return;
		const list = options.map((option) => ({
			label: option.label || option.value,
			value: option.value,
		}));
		if (input.expedition_awesomplete) {
			input.expedition_awesomplete.list = list;
			return;
		}
		input.expedition_awesomplete = new Awesomplete(input, {
			list,
			minChars: 0,
			maxItems: 20,
			autoFirst: true,
			filter: Awesomplete.FILTER_CONTAINS,
			item(text, input_value) {
				return Awesomplete.ITEM(text, input_value);
			},
		});
	}

	function render_filters_table(frm) {
		hide_storage_fields(frm);

		if (!frm.expedition_filter_area) {
			frm.expedition_filter_area = $('<div class="expedition-filter-summary"></div>');
			frm.fields_dict[FILTER_TABLE_FIELD].$wrapper.before(frm.expedition_filter_area);
		}

		const wrapper = frm.expedition_filter_area.empty().show();
		const table = $(`<table class="table table-bordered" style="cursor:pointer; margin:0;">
			<thead>
				<tr>
					<th style="width: 40%">${__("Filter")}</th>
					<th style="width: 20%">${__("Condition")}</th>
					<th>${__("Value")}</th>
				</tr>
			</thead>
			<tbody></tbody>
		</table>`).appendTo(wrapper);
		$(`<p class="text-muted small">${__("Click table to edit")}</p>`).appendTo(wrapper);

		const filters = get_filter_rows(frm);
		if (!frm.doc.source_doctype) {
			table.find("tbody").append(
				$(`<tr><td colspan="3" class="text-muted text-center">
					${__("Choose a Source DocType to set filters")}</td></tr>`)
			);
		} else if (filters.length) {
			if (!frappe.get_meta(frm.doc.source_doctype)) {
				frappe.model.with_doctype(frm.doc.source_doctype, () => render_filters_table(frm));
				table.find("tbody").append(
					$(`<tr><td colspan="3" class="text-muted text-center">
						${__("Loading filters")}</td></tr>`)
				);
				table.on("click", () => open_filter_dialog(frm));
				return;
			}
			filters.forEach(([fieldname, operator, value]) => {
				const meta = get_field_meta(frm.doc.source_doctype, fieldname);
				table.find("tbody").append(
					$(`<tr>
						<td>${frappe.utils.escape_html(meta.label || fieldname)}</td>
						<td>${frappe.utils.escape_html(operator_label(operator, value))}</td>
						<td>${frappe.utils.escape_html(format_value(value))}</td>
					</tr>`)
				);
			});
		} else {
			table.find("tbody").append(
				$(`<tr><td colspan="3" class="text-muted text-center">
					${__("Click to Set Filters")}</td></tr>`)
			);
		}

		table.on("click", () => open_filter_dialog(frm));
	}

	function hide_storage_fields(frm) {
		if (frm.fields_dict[FILTER_TABLE_FIELD]) {
			frm.fields_dict[FILTER_TABLE_FIELD].$wrapper.hide();
		}
		frm.set_df_property(FILTER_JSON_FIELD, "hidden", 1);
	}

	function open_filter_dialog(frm) {
		if (!frm.doc.source_doctype) {
			frappe.msgprint(__("Choose a Source DocType first."));
			return;
		}

		ensure_filter_group().then(() => {
			const dialog = new frappe.ui.Dialog({
				title: __("Set Filters for {0}", [__(frm.doc.source_doctype)]),
				fields: [
					{
						fieldtype: "HTML",
						fieldname: "filter_area",
					},
				],
				primary_action() {
					if (!filter_group) {
						frappe.msgprint(__("Filters are still loading. Please try again in a moment."));
						return;
					}
					const rows = normalize_filter_group_rows(filter_group.get_filters());
					validate_supported_rows(rows);
					set_filter_rows(frm, rows);
					dialog.hide();
					render_filters_table(frm);
				},
				primary_action_label: __("Set"),
			});

			let filter_group;
			dialog.show();

			frappe.model.with_doctype(frm.doc.source_doctype, () => {
				filter_group = new frappe.ui.FilterGroup({
					parent: dialog.get_field("filter_area").$wrapper,
					doctype: frm.doc.source_doctype,
					on_change: () => {},
				});
				filter_group.add_filters_to_filter_group(
					get_filter_rows(frm).map(([fieldname, operator, value]) => [
						frm.doc.source_doctype,
						fieldname,
						operator,
						value,
					])
				);
			});
		});
	}

	function get_filter_rows(frm) {
		const child_rows = (frm.doc[FILTER_TABLE_FIELD] || [])
			.filter((row) => row.fieldname && row.operator)
			.map((row) => {
				const operator = canonical_operator(row.operator);
				const value =
					operator === "between" ? [row.from_value || "", row.to_value || ""] : row.value || "";
				return [row.fieldname, operator, value];
			});

		if (child_rows.length) return child_rows;
		return parse_filter_json(frm.doc[FILTER_JSON_FIELD]);
	}

	function parse_filter_json(raw) {
		if (!raw) return [];
		try {
			const parsed = typeof raw === "string" ? JSON.parse(raw) : raw;
			if (!Array.isArray(parsed)) return [];
			return parsed
				.filter((row) => Array.isArray(row) && (row.length === 2 || row.length === 3))
				.map((row) => {
					if (row.length === 2) return [row[0], "=", row[1]];
					return [row[0], canonical_operator(row[1]), row[2]];
				});
		} catch (e) {
			return [];
		}
	}

	function normalize_filter_group_rows(filters) {
		return (filters || [])
			.map((filter) => {
				const [, fieldname, operator, value] = filter;
				return [fieldname, canonical_operator(operator), value];
			})
			.filter((row) => row[0] && row[1] && row[2] !== undefined && row[2] !== null && row[2] !== "");
	}

	function validate_supported_rows(rows) {
		const unsupported = rows.find((row) => UNSUPPORTED_OPERATORS.has(row[1]));
		if (unsupported) {
			frappe.throw(__("Operator {0} is not supported for Expedition layer filters yet.", [unsupported[1]]));
		}
	}

	function set_filter_rows(frm, rows) {
		validate_supported_rows(rows);
		frm.clear_table(FILTER_TABLE_FIELD);
		rows.forEach(([fieldname, operator, value]) => add_child_filter(frm, fieldname, operator, value));
		frm.set_value(FILTER_JSON_FIELD, rows.length ? JSON.stringify(rows) : "");
		frm.refresh_field(FILTER_TABLE_FIELD);
	}

	function sync_child_table_from_json(frm) {
		const rows = parse_filter_json(frm.doc[FILTER_JSON_FIELD]);
		validate_supported_rows(rows);
		if (!rows.length) {
			frm.clear_table(FILTER_TABLE_FIELD);
			frm.refresh_field(FILTER_TABLE_FIELD);
			return;
		}
		const existing = get_filter_rows(frm);
		if (JSON.stringify(existing) === JSON.stringify(rows)) return;
		frm.clear_table(FILTER_TABLE_FIELD);
		rows.forEach(([fieldname, operator, value]) => add_child_filter(frm, fieldname, operator, value));
		frm.refresh_field(FILTER_TABLE_FIELD);
	}

	function add_child_filter(frm, fieldname, operator, value) {
		const meta = get_field_meta(frm.doc.source_doctype, fieldname);
		const row = frm.add_child(FILTER_TABLE_FIELD);
		row.fieldname = fieldname;
		row.label = meta.label || fieldname;
		row.fieldtype = meta.fieldtype || "";
		row.operator = child_operator_label(operator);
		row.condition = "AND";

		if (operator === "between" && Array.isArray(value)) {
			row.from_value = value[0] == null ? "" : String(value[0]);
			row.to_value = value[1] == null ? "" : String(value[1]);
			row.value = "";
		} else {
			row.value = Array.isArray(value) ? value.join(", ") : value == null ? "" : String(value);
			row.from_value = "";
			row.to_value = "";
		}
	}

	function clear_stored_filters(frm) {
		frm.clear_table(FILTER_TABLE_FIELD);
		frm.set_value(FILTER_JSON_FIELD, "");
		frm.refresh_field(FILTER_TABLE_FIELD);
	}

	function get_field_meta(doctype, fieldname) {
		const df = frappe.meta.get_field(doctype, fieldname);
		if (!df) return {};
		return { label: df.label, fieldtype: df.fieldtype };
	}

	function canonical_operator(operator) {
		const op = String(operator || "=").trim();
		return CANONICAL_OPERATORS[op] || CANONICAL_OPERATORS[op.toLowerCase()] || op.toLowerCase();
	}

	function child_operator_label(operator) {
		return CHILD_OPERATOR_LABELS[operator] || CHILD_OPERATOR_LABELS[canonical_operator(operator)] || operator;
	}

	function operator_label(operator, value) {
		if (operator === "is") {
			return String(value || "").toLowerCase() === "not set" ? __("is not set") : __("is set");
		}
		return child_operator_label(operator);
	}

	function format_value(value) {
		if (Array.isArray(value)) return value.join(", ");
		return value == null ? "" : String(value);
	}
})();
