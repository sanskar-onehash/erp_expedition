app_name = "expedition"
app_title = "Expedition"
app_publisher = "OneHash"
app_description = "A Frappe-native geospatial intelligence platform. Turn any Frappe DocType into interactive, layered maps with save/load configurations, styling, analytics, and insights."
app_email = "engineering@onehash.ai"
app_license = "mit"

scheduler_events = {
    "hourly": [
        "expedition.tasks.recompute_insights",
    ],
    "daily": [
        "expedition.tasks.recompute_zone_coverage",
    ],
}

after_install = "expedition.install.after_install"
after_uninstall = "expedition.install.after_uninstall"

before_tests = "expedition.install.before_tests"

app_include_icons = "expedition/icons.svg"
# Keep the geolocation loader in Frappe's fingerprinted asset manifest.  A
# directly referenced static file can remain cached after a deployment, which
# in turn leaves the embedded map with mismatched JS and CSS assets.
app_include_js = ["geolocation_loader.bundle.js"]

extend_bootinfo = "expedition.boot.add_navbar_item"
