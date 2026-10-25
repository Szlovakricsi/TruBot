"""UPS reporting tests, independent of Telegram and TrueNAS connections."""
import ast
import pathlib
import unittest

SOURCE = pathlib.Path(__file__).resolve().parents[1] / "bot.py"
TREE = ast.parse(SOURCE.read_text(encoding="utf-8"))


def load(names, **deps):
    nodes = [
        n for n in TREE.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names
    ]
    assert len(nodes) == len(names)
    ns = dict(deps)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"), ns)
    return ns


class UpsTests(unittest.TestCase):
    def test_power_flags(self):
        f = load(["ups_power_mode"])["ups_power_mode"]
        self.assertEqual(f("OL CHRG"), "Mains")
        self.assertEqual(f("OB DISCHRG"), "Battery")
        self.assertEqual(f("OB LB"), "Battery (low)")
        self.assertEqual(f("OL LB"), "Mains (low battery)")
        self.assertEqual(f("BYPASS"), "Bypass")
        self.assertEqual(f(None), "Unknown")

    def test_netdata_metric_skips_timestamp(self):
        def fake_latest(graph):
            return graph["vals"]
        f = load(["ups_graph_value"], latest_reporting_values=fake_latest,
                 safe_float=lambda x: float(x) if x is not None else None)["ups_graph_value"]
        self.assertEqual(f({"vals": {"time": 1792870000, "voltage": 26.8}}), 26.8)
        self.assertIsNone(f({"vals": {"time": 1792870000}}))

    def test_render_ups_optional_and_missing_fields(self):
        ns = load(["render_ups_lines"], safe_float=lambda x: float(x) if x is not None else None)
        render = ns["render_ups_lines"]
        self.assertEqual(render(None), [])
        data = render({"name": "Test UPS", "power": "Battery", "charge": 80, "battery_voltage": 24.2})
        self.assertIn("Power: 🟠 Battery", data)
        self.assertIn("Battery: 80%", data)
        self.assertIn("Battery voltage: 24.2 V", data)
        unknown = render({"name": "UPS", "power": "Unknown", "charge": None, "battery_voltage": None})
        self.assertIn("Battery: N/A", unknown)
        self.assertIn("Power: ⚪ Unknown", unknown)

    def test_disabled_ups_is_absent(self):
        def call(name, *args):
            if name == "service.query":
                return [{"service": "ups", "enable": False, "state": "STOPPED"}]
            self.fail(f"Unexpected call: {name}")
        ns = load(["get_ups_data_sync"], tn_call=call)
        self.assertIsNone(ns["get_ups_data_sync"]())

    def test_enabled_ups_uses_api_fallback(self):
        def call(name, *args):
            if name == "service.query":
                return [{"service": "ups", "enable": True, "state": "RUNNING"}]
            if name == "ups.config":
                return {"identifier": "ups", "description": "Test UPS", "mode": "MASTER"}
            if name == "reporting.get_data":
                return [
                    {"name": "upscharge", "legend": ["time", "charge"], "data": [[1792870000, 91]]},
                    {"name": "upsvoltage", "identifier": "battery",
                     "legend": ["time", "voltage"], "data": [[1792870000, 25.1]]},
                ]
            self.fail(f"Unexpected call: {name}")
        def latest(graph):
            return dict(zip(graph["legend"], graph["data"][-1]))
        class Logger:
            def warning(self, *args, **kwargs):
                pass
        ns = load(["get_ups_data_sync", "ups_graph_value"], tn_call=call,
                  UPS_NUT_HOST="", UPS_NUT_PORT=3493, TRUENAS_URI="wss://nas.example/api/current",
                  urlparse=__import__("urllib.parse", fromlist=["urlparse"]).urlparse,
                  log=Logger(), read_nut_variables=lambda *args: {},
                  safe_float=lambda x: float(x) if x is not None else None,
                  latest_reporting_values=latest)
        ups = ns["get_ups_data_sync"]()
        self.assertEqual(ups["power"], "Unknown")
        self.assertEqual(ups["charge"], 91)
        self.assertEqual(ups["battery_voltage"], 25.1)


if __name__ == "__main__":
    unittest.main()
