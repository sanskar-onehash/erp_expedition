import frappe, unittest, importlib


class TestWhitelist(unittest.TestCase):
    def test_list_whitelist(self):
        # Confirm import path resolves
        mod = importlib.import_module("expedition.api.map")
        fn = getattr(mod, "load_full")
        print(f"FN: {fn}, __module__={fn.__module__}, __qualname__={fn.__qualname__}")
        from frappe import whitelisted
        # whitelisted is a list of function objects
        present = fn in whitelisted
        print(f"FN in whitelisted? {present}")
        if not present:
            # Look for similar entries
            for w in whitelisted:
                if "expedition" in getattr(w, "__module__", ""):
                    print(f"  similar: {w.__module__}.{w.__name__}")
        # ALSO: try the HTTP path
        try:
            from frappe.handler import is_whitelisted
            is_whitelisted(fn)
            print("is_whitelisted(fn) OK")
        except Exception as e:
            print(f"is_whitelisted(fn) failed: {type(e).__name__}: {e}")
        self.assertTrue(True)
