(() => {
	const SCRIPT_URL = "/assets/expedition/dist/geolocation.iife.js";
	const STYLE_URL = "/assets/expedition/dist/geolocation.css";
	const records = new Map();
	let assetsPromise = null;
	let observer = null;

	function loadAssets() {
		if (window.ExpeditionGeolocation?.mountWidget) return Promise.resolve();
		if (assetsPromise) return assetsPromise;

		assetsPromise = Promise.all([loadStyle(STYLE_URL), loadScript(SCRIPT_URL)]).then(() => {
			if (typeof window.ExpeditionGeolocation?.mountWidget !== "function") {
				throw new Error("Expedition geolocation bundle did not initialize");
			}
		});
		return assetsPromise;
	}

	function loadStyle(url) {
		if (document.querySelector(`link[data-expedition-geolocation="${url}"]`)) {
			return Promise.resolve();
		}
		return new Promise((resolve, reject) => {
			const link = document.createElement("link");
			link.rel = "stylesheet";
			link.href = url;
			link.dataset.expeditionGeolocation = url;
			link.onload = resolve;
			link.onerror = () => reject(new Error(`Unable to load ${url}`));
			document.head.appendChild(link);
		});
	}

	function loadScript(url) {
		const existing = document.querySelector(`script[data-expedition-geolocation="${url}"]`);
		if (existing) {
			if (window.ExpeditionGeolocation?.mountWidget) return Promise.resolve();
			return new Promise((resolve, reject) => {
				existing.addEventListener("load", resolve, { once: true });
				existing.addEventListener("error", reject, { once: true });
			});
		}
		return new Promise((resolve, reject) => {
			const script = document.createElement("script");
			script.src = url;
			script.async = true;
			script.dataset.expeditionGeolocation = url;
			script.onload = resolve;
			script.onerror = () => reject(new Error(`Unable to load ${url}`));
			document.head.appendChild(script);
		});
	}

	async function mountGeolocationField(options = {}) {
		const context = resolveContext(options);
		const { host, control } = context;
		context.enforceNativeHide?.();
		showLoading(host);

		try {
			await loadAssets();
			const widgetOptions = buildWidgetOptions(options, context);
			const existing = records.get(host);
			const handle = existing?.handle || window.ExpeditionGeolocation.mountWidget(host, widgetOptions);
			if (existing) handle.update(widgetOptions);

			records.set(host, {
				handle,
				control,
				restore: context.restore,
				enforceNativeHide: context.enforceNativeHide,
			});
			// frm.set_value() refreshes Frappe's native Geolocation control. Hide it
			// again after that refresh has completed, leaving only our map visible.
			requestAnimationFrame(() => context.enforceNativeHide?.());
			ensureCleanupObserver();
			return handle;
		} catch (error) {
			context.restore?.();
			host.remove();
			window.frappe?.show_alert?.({
				message: error?.message || "Unable to load the Expedition map.",
				indicator: "red",
			});
			throw error;
		}
	}

	function resolveContext(options) {
		if (options.element instanceof Element) {
			return { host: options.element, control: null, restore: null, frm: options.frm || null };
		}

		const frm = options.frm;
		const fieldname = String(options.fieldname || "").trim();
		if (!frm || !fieldname) {
			throw new Error("Pass either element, or both frm and fieldname");
		}

		const control = frm.fields_dict?.[fieldname];
		if (!control) throw new Error(`Field ${fieldname} was not found on ${frm.doctype}`);
		if (control.df?.fieldtype !== "Geolocation") {
			throw new Error(`${frm.doctype}.${fieldname} must be a Geolocation field`);
		}
		for (const coordinateField of [options.latitudeField, options.longitudeField].filter(Boolean)) {
			if (!frm.fields_dict?.[coordinateField]) {
				throw new Error(`Coordinate field ${coordinateField} was not found on ${frm.doctype}`);
			}
		}

		const wrapper = unwrapElement(control.$wrapper) || control.wrapper;
		if (!(wrapper instanceof Element)) throw new Error(`Field ${fieldname} has no form wrapper`);

		let host = wrapper.querySelector(`:scope > [data-expedition-field="${cssEscape(fieldname)}"]`);
		if (!host) {
			host = document.createElement("div");
			host.className = "expedition-geolocation-host";
			host.dataset.expeditionField = fieldname;
			wrapper.appendChild(host);
		}

		const existingRecord = records.get(host);
		if (existingRecord) {
			existingRecord.enforceNativeHide?.();
			return {
				host,
				control,
				frm,
				restore: existingRecord.restore,
				enforceNativeHide: existingRecord.enforceNativeHide,
			};
		}

		const nativeVisibility = hideNativeControl(control, wrapper, host);

		return {
			host,
			control,
			frm,
			enforceNativeHide: nativeVisibility.enforce,
			restore() {
				nativeVisibility.restore();
			},
		};
	}

	function hideNativeControl(control, wrapper, host) {
		const originalDisplays = new Map();
		const hide = () => {
			const nativeAreas = [
				unwrapElement(control.input_area),
				unwrapElement(control.disp_area),
			].filter((element, index, list) => (
				element && element !== host && !element.contains(host) && list.indexOf(element) === index
			));

			nativeAreas.forEach((element) => {
				if (!originalDisplays.has(element)) originalDisplays.set(element, element.style.display);
				element.style.setProperty("display", "none", "important");
			});
			wrapper.classList.add("expedition-geolocation-mounted");
		};

		// Frappe may replace the native map area when a value changes. Watching only
		// child additions avoids observing MapLibre's style updates, and makes the
		// replacement invisible before it can appear beside the Expedition map.
		const nativeObserver = new MutationObserver(hide);
		nativeObserver.observe(wrapper, { childList: true, subtree: true });
		hide();

		return {
			enforce: hide,
			restore() {
				nativeObserver.disconnect();
				originalDisplays.forEach((display, element) => {
					element.style.removeProperty("display");
					if (display) element.style.display = display;
				});
				wrapper.classList.remove("expedition-geolocation-mounted");
			},
		};
	}

	function buildWidgetOptions(options, context) {
		const frm = context.frm;
		const fieldname = options.fieldname;
		const value = frm && fieldname ? frm.doc?.[fieldname] : options.value;
		const readonly = options.readonly ?? inferReadonly(frm, context.control);
		const mapCenter = window.frappe?.utils?.map_defaults?.center;

		return {
			value: value || "",
			readonly,
			height: options.height,
			styleUrl: options.styleUrl,
			center: options.center || (Array.isArray(mapCenter) ? mapCenter : undefined),
			zoom: options.zoom,
			searchEnabled: options.searchEnabled,
			language: options.language || navigator.language || "",
			onChange: async (change) => {
				if (frm && fieldname) {
					const values = { [fieldname]: change.value };
					if (options.latitudeField) values[options.latitudeField] = change.latitude;
					if (options.longitudeField) values[options.longitudeField] = change.longitude;
					await frm.set_value(values);
					context.enforceNativeHide?.();
					requestAnimationFrame(() => context.enforceNativeHide?.());
				}
				records.get(context.host)?.handle.setValue(change.value);
				if (typeof options.onChange === "function") await options.onChange(change, frm);
			},
		};
	}

	function inferReadonly(frm, control) {
		if (!frm || !control) return false;
		if (control.disabled) return true;
		if (control.df?.read_only) return true;
		if (typeof frm.is_read_only === "function" && frm.is_read_only()) return true;
		return Number(frm.doc?.docstatus || 0) === 1 && !control.df?.allow_on_submit;
	}

	function unmountGeolocationField(options = {}) {
		let host = options.element;
		if (!(host instanceof Element) && options.frm && options.fieldname) {
			const wrapper = unwrapElement(options.frm.fields_dict?.[options.fieldname]?.$wrapper);
			host = wrapper?.querySelector(`:scope > [data-expedition-field="${cssEscape(options.fieldname)}"]`);
		}
		const record = host && records.get(host);
		if (!record) return;
		record.handle.destroy();
		record.restore?.();
		host.remove();
		records.delete(host);
	}

	function ensureCleanupObserver() {
		if (observer || !document.body) return;
		observer = new MutationObserver(() => {
			for (const [host, record] of records) {
				if (host.isConnected) continue;
				record.handle.destroy();
				records.delete(host);
			}
		});
		observer.observe(document.body, { childList: true, subtree: true });
	}

	function showLoading(host) {
		if (records.has(host)) return;
		host.innerHTML = '<div style="min-height:280px;display:grid;place-items:center;border:1px solid var(--border-color,#d1d8dd);border-radius:12px;background:var(--fg-color,#fff);color:var(--text-muted,#6c7680)">Loading Expedition map…</div>';
	}

	function unwrapElement(value) {
		if (value instanceof Element) return value;
		if (value?.get && value.get(0) instanceof Element) return value.get(0);
		if (value?.[0] instanceof Element) return value[0];
		return null;
	}

	function cssEscape(value) {
		return window.CSS?.escape ? window.CSS.escape(value) : String(value).replace(/[^a-zA-Z0-9_-]/g, "\\$&");
	}

	window.Expedition = window.Expedition || {};
	window.Expedition.mountGeolocationField = mountGeolocationField;
	window.Expedition.unmountGeolocationField = unmountGeolocationField;
})();
