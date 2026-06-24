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

app_include_icons = "expedition/public/icons.svg"

standard_navbar_items = [
    {
        "item_label": "Expedition",
        "item_type": "Route",
        "route": "/expedition",
        "is_standard": 1,
        "hidden": 0,
    },
]
