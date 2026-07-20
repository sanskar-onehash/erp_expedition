# Script Layer Filters

## Current Contract

Python Script layers may set `source_doctype` to opt into the standard Expedition filter UI and source-field schema. When filters are configured on the layer, the server passes them to the script as:

```python
context["source_doctype"]
context["filter_json"]
context["filters"]
```

The script is responsible for applying `context["filters"]` to its own query. This keeps Python Script layers flexible while allowing the existing Layer Editor and Layer Panel filter controls to be reused.

## Long-Term TODO

Add a script-layer filter schema that is independent of `source_doctype`.

This is needed for script/computed properties that are not real fields on a single DocType, for example:

- `open_age_days`
- `product_family`
- `territory`
- `sla_state`
- joined fields from multiple DocTypes

Proposed shape:

```json
[
  {
    "fieldname": "product_family",
    "label": "Product Family",
    "fieldtype": "Data",
    "operators": ["=", "!=", "in", "not in", "like"]
  },
  {
    "fieldname": "open_age_days",
    "label": "Open Age",
    "fieldtype": "Int",
    "operators": ["=", "!=", ">", ">=", "<", "<=", "between"]
  }
]
```

Once available, the filter builder should merge the `source_doctype` schema with this script schema, validate `filter_json` against both, and pass the same normalized `context["filters"]` to the script.
